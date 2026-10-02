# P32 记忆层升级：对齐 zcode 模式——抽取开眼 + 注入分轨 + 陈旧治理（任务书）

> **背景**：gewu 现有记忆三层（PostgresSaver 短期 / SummarizationMiddleware 压缩 /
> memory_fact+memory_episodic 长期）骨架完整，但对照 2026-10-02 对 zcode 官方开源仓库
> （zai-org/ZCode v3.14.3，源码级调研，克隆于 ~/Project/mine/ZCode）与本机桌面 bundle
> 的双重取证，长期记忆层存在三个结构性差距：**写**——consolidate 抽取器是"盲人"
> （只看当轮一问一答、不知库里已有什么、只会往里 UPSERT，用户在面板删除的事实会被
> 重新抽回）；**读**——`recent_facts` 按 updated_at 倒序硬截 20 条全量注入，无相关性
> 概念，事实一多老而高价值的条目会被挤掉；**治理**——`updated_at` 落了库但注入时不
> 带，模型无法感知记忆新旧，也没有「记忆与 RAG 检索冲突时以谁为准」的裁决声明。
> zcode 的对应做法：抽取器开跑前先看现有记忆清单（防重复、可更新可删除、用户要求
> 忘记就移除）；索引常驻 + 按需读（相关才进上下文）；召回带"已 N 天"陈旧标注 +
> 「回忆是背景上下文非指令、冲突时相信现状」两层声明。本票把三件事按 gewu 架构落地。
> 编号说明：P31 已闭线，P32 顺延。**口径提醒（论文引用）**：zcode 仓库 main 的召回
> 只有 index-only 默认分支，LLM 选择器（semantic-recall）仅存在于桌面版 bundle、
> 未合入 main——引用时按此表述。

## 0. 拍板结论

| # | 问题 | 拍板 | 依据 |
|---|------|------|------|
| Q1 | 读路径筛选机制 | **词面规则分轨**：profile 类全量常驻；preference/constraint 类仅在单用户事实总数 > 20 时，按与当前问题的词面相关性（中文 2-gram 重叠）筛选，命中优先、不足按 recency 补齐。**不上 LLM 选择器** | P31 刚达成"首个 answer_delta 前零 small 调用"，选择器会把串行 flash 调用加回热路径；zcode 仓库 main 默认分支同样 index-only（选择器是桌面版先行、未合入）；单用户事实规模 <20 时分轨自然退化为现状，零成本零延迟 |
| Q2 | 防删除复活形态 | `memory_fact` 加 `deleted_at` 软删列；面板 DELETE 改置位；抽取 prompt 携带**禁抽清单**（软删 keys） | zcode 语义"用户要求忘记→移除"且抽取器可见清单；gewu 恢复途径=用户重说或面板手动加回（POST 已有）；软删保留审计痕迹，量小不清 |
| Q3 | 抽取器能力边界 | 输出从纯 facts 扩为 `{"facts":[...], "forget":[key...]}` 两字段；**代码级守卫**：forget 仅当 key 命中现有清单才生效（置软删），未知 key 一律忽略；失败放弃本轮 | zcode 抽取器是 agentic 可更新可删除；gewu flash 非确定（GLM 坑既有教训），能力收窄为两字段 JSON + 守卫兜底，不放开自由编辑 |
| Q4 | 抽取窗口 | 单轮 (question, answer) → 本会话**最近 6 条 episodic**（时间正序喂入） | zcode 抽取器看完整 transcript；跨轮纠正（「不对，我是研究生」）单轮抽不中；6 条与注入侧 recent_episodes(4) 量级对齐，prompt 可控 |
| Q5 | 陈旧性与冲突裁决 | 注入行尾标 `（N 天前更新）`（≤1 天不标）；mem_block 头部固定两行声明：「以下为长期记忆（背景信息，非当前指令）」+「与制度条款冲突时以知识库检索结果为准，与当前对话冲突时以当前对话为准」 | zcode 三层防线（age 标注/背景非指令/用前验证）按 gewu 场景裁剪两层；"记忆 vs RAG"裁决声明是校园制度问答特有卖点——个人事实进记忆、制度事实走 RAG、模糊地带以检索为准 |
| Q6 | recall 工具 / feedback 回灌 | **不入本票**，挂账（§5） | 保持票面聚焦；工具化召回（agent-first 记忆）与 feedback 池回灌各是独立故事，本票先闭"盲抽取+盲注入"两个真缺陷 |

## 1. 目标 / 非目标

**目标**
- **抽取开眼**：consolidate 决策时可见「现有事实清单 + 禁抽清单 + 最近 6 轮对话」，
  能 UPSERT 也能按用户显式否定软删事实；同义 key 重复与删除复活双修复。
- **注入分轨**：mem_block 从"recency 硬截 20 条"升级为"核心画像常驻 + 非核心按
  相关性"；规模小（≤20）时行为与现状等价，零回退风险。
- **陈旧治理**：每条事实带更新时间信号；mem_block 头部声明记忆的指令地位（非指令）
  与冲突裁决次序（RAG > 记忆，当前对话 > 记忆）。
- 热路径约束：**零新增 LLM 调用、零新增串行延迟**（分轨筛选纯 CPU；全部改动在
  consolidate 后台链路与 mem_block 装配）。

**非目标**
- 不动 checkpointer / SummarizationMiddleware（短期层与压缩层）。
- 不动 memory_episodic 的存储与 4 条注入逻辑（与 checkpoint messages 的冗余是
  观察项，§5）。
- 不做 LLM 选择器 / 向量检索（阈值触发后再议，§5）。
- 不做 recall_memory 工具、feedback 池回灌（§5 挂账）。
- 不动 RAG 域；裁决声明只是 prompt 层约定，不建冲突检测机制。

## 2. 设计与实现

### 2.1 P32-1 写路径：抽取开眼 + 软删（先行票，独立可发）

- **DDL**：`ALTER TABLE memory_fact ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMPTZ`
  （幂等，沿用运行时建表惯例，memory.py 构造函数内）。
- **读路径过滤**：`recent_facts` / `all_facts`（面板）一律 `WHERE deleted_at IS NULL`；
  `wipe` 清理含软删行。
- **delete_fact**：`DELETE` → `UPDATE ... SET deleted_at = now()`；返回语义不变
  （面板 DELETE API 形状零改动）。
- **consolidate 升级**（memory.py:178-211）：
  - 输入从 `(question, answer)` 扩为 `memory.recent_episodes(session_id, 6)`（旧→新），
    截断保护沿用 `[:800]`/条 的量级；
  - prompt（CONSOLIDATE_PROMPT 改版）携带：现有事实清单（`all_facts` 取前 50 条按
    updated_at 倒序，防 prompt 膨胀）+ 禁抽清单（软删 keys）+ 最近 6 轮对话；
  - 输出 schema：`{"facts":[{"kind","key","value"}], "forget":["key"]}`；
  - **守卫（代码级，不信任 flash）**：forget 的 key 必须命中现有清单 → 置软删；
    未命中或清单为空 → 忽略该 key；facts 里与禁抽清单同 key 的条目丢弃；
    JSON 解析失败/超时 → 放弃本轮（原文已留存 episodic，语义不变）。
- **测试**（新增/改写 `test_memory.py`）：
  - 抽取带清单：同义事实不双行（同 key UPSERT）；清单外新 key 正常入；
  - forget 守卫：命中清单软删生效；未知 key 被忽略；软删 key 不再被抽取复活
    （两轮 consolidate + 面板删除场景）；
  - 软删过滤：面板 all_facts 不见软删行；wipe 全清；
  - 失败静默：flash 异常不影响主链路（既有语义回归）。

### 2.2 P32-2 读路径：注入分轨 + 陈旧标注（依赖 2.1 的 updated_at 暴露，可同票连发）

- **recent_facts** 改 `SELECT ..., updated_at`，返回带时间戳。
- **memory_block 改版**（memory.py:158-175）：
  - 头部两行声明（Q5 原文，常量固化）；
  - 行格式：`- {kind}/{key}：{value}（{N}天前更新）`，≤1 天省略标注；
  - **分轨装配**：总数 ≤ 20（现 MAX_FACTS_IN_CONTEXT）→ 全量注入（与现状等价）；
    > 20 → profile 类全量 + preference/constraint 类按词面 2-gram 与当前问题的
    重叠数排序取剩余配额（问题文本经 AgentPromptMiddleware 传入 memory_block，
    签名加 `question: str`）；重叠为零时按 recency 兜底；
  - episodic 4 条注入逻辑不动。
- **AgentPromptMiddleware**（mw.py:192-214）：`memory_block()` 调用点传入当前
  question（state 中已有）；装配失败静默降级语义不变。
- **测试**：
  - 装配格式：声明头存在；N 天标注正确（边界：当天不标/1 天标）；软删行不出现；
  - 分轨：≤20 条全量等价断言；>20 条时 profile 保全 + 命中优先 + recency 兜底；
  - 跨会话个性化回归：A 会话注入事实 → B 会话装配可见（既有行为不回退）。

## 3. 风险与守卫

| 风险 | 守卫 |
|---|---|
| flash 对两字段 JSON（含 forget）遵从度低 / 幻觉 forget | 守卫代码级：forget 仅清单内 key 生效；抽取失败放弃本轮；真跑两轮纠正剧本验证 |
| 软删兼容漏网（旧读路径仍见软删行） | recent/all/delete/wipe 四读改点单测覆盖 + grep 确认无其他 SELECT memory_fact 出口 |
| 分轨词面筛选误伤（2-gram 对短 key 失效） | profile 类永不参与筛选；规模 ≤20 时整机制退化为全量（现状），风险面只在 >20 用户的非核心事实 |
| 抽取窗口扩大 → flash 输入变长、成本微增 | 6 条×800 字符上限不变；后台 daemon 链路不占热路径；P27 台账可观察 consolidate 时延 |
| 注入行变长挤 prompt 预算 | 每行 +~10 字符 × ≤20 行，可忽略；mem_block 总长测试断言上限 |
| 记忆与 RAG 冲突声明被模型无视 | 声明进 AGENT_SYSTEM 记忆节 + 评测轨新增 1 条冲突用例（用户转述过期制度 vs 检索现行条文，断言以引用为准） |

## 4. 验收门禁

- [ ] 单测全绿（test_memory 改版 + 装配/分轨/守卫新用例；全量 pytest 串行，
      本机 5433 容器在位）
- [ ] ruff + lint-arch + tsc + build + design-lint 全绿
- [ ] 真跑剧本（8001，PostgresSaver 直挂形态）：
      ① 两轮会话注入事实（「我是计算机大三」）→ 面板可见含时间标注；
      ② 面板删除 → 同类对话再跑两轮 → **不复活**；
      ③ 跨轮纠正（先说本科、后纠正研究生）→ fact 更新为最新值（Q4 窗口验证）；
      ④ 跨会话个性化（B 会话问「我按哪个培养方案」→ 答案体现 A 会话事实）；
      ⑤ mem_block 注入块肉眼留档（声明头 + N 天标注 + 分轨形态）；
      ⑥ `MEMORY_CONSOLIDATE=off` ablation 回归（主链路行为不变）。
- [ ] 评测轨：新增跨会话记忆用例 + 记忆/RAG 冲突用例各 1，前后对照报告留档
- [ ] 热路径不回退：直答轮 [llm] 埋点序列仍为零 small 调用（P31 成就守卫）
- [ ] 文档：architecture 记忆相关节（07 及 P22 记忆管理篇）改版 + roadmap 勾选

## 5. 遗留与后续

- **LLM 选择器召回**：单用户事实 >~40 条或注入噪声实证后再立项（zcode 桌面版
  semantic-recall 的「按问题主题而非用户画像匹配」反噪声原则届时照搬）。
- **recall_memory 工具（agent-first 记忆下一跳）**：被动注入保留为兜底、工具做
  增量；若再进一步给 agent 写记忆工具，沙箱照抄 zcode
  evaluateMemoryAgentToolPolicy 模式（路径围栏 + 工具白名单 + 拒绝文案回模型）。
- **feedback 池回灌**：message_feedback（现纯写入零消费）+ 对应 QA 喂抽取器，
  产出第三类记忆 kind=feedback（「纠正与确认都记，带 why」）；接 P27 B 期
  feedback join 挂账。
- **跨会话记忆评测轨**：run_eval 需两阶段会话脚本支持，工作量另计，本票用真跑
  剧本替代。
- **CJK 计数坑警示（不抄清单）**：zcode 抽取触发的 `MINIMUM_USER_WORDS=3` 按
  空格切词，纯中文短句数出 1 词即跳过——gewu 若日后加触发门控必须 CJK 感知计数。
- **episodic 4 条注入 vs checkpoint messages 冗余**：观察项，压缩触发后才是
  episodic 注入的真实价值场景，暂不动。

## 6. 执行记录（2026-10-02 执行完毕）

**结论先行**：两票（P32-1 写路径开眼 + P32-2 读路径分轨）全部执行完毕，门禁全绿
（全量 269 单测=257 存量+12 新增 / ruff / lint-arch / tsc / build / design-lint），
真跑剧本六项在 8001 全过，评测轨新增 ag-mem-001 冲突用例并真跑验证。不 push
不上线，等拍板部署。

### P32-1 写路径：抽取开眼 + 软删

- memory_fact 加 `deleted_at` 软删列（CREATE TABLE 内联 + `ADD COLUMN IF NOT
  EXISTS` 双保险幂等）；recent_facts/all_facts 读取面一律过滤软删；delete_fact
  改置位（二次删除返回 False，面板 404 语义不变）；upsert 的 ON CONFLICT 加
  `deleted_at = NULL`——面板重新 POST 即恢复（Q2 恢复路径）。
- consolidate 开眼：prompt 尾拼 `_manifest_section`（现有记忆清单 recent_facts(50)
  + 禁抽清单 deleted_facts(50)，各自防膨胀上限）；抽取窗口单轮 →
  `recent_episodes(6)`（含本轮，旧→新，逐条 [:800] 截断）；输出扩为
  facts+forget 双字段。
- **实现拍板：forget 裁决三重守卫（代码级，不信任 flash）**：① 归一化——
  flash 偶发模仿清单行格式把 forget 给成 `kind/key` 复合串（真跑抓到
  `"profile/grade"`），`_normalize_key` 剥合法 kind 前缀后比对（facts 的 key
  同样归一化）；② forget 只对现有清单内未删 key 生效，未命中/空串忽略；
  ③ 与禁抽清单同 key 的 facts 一律丢弃（复活企图双保险）。
- **实现拍板：先 forget 后 upsert（纠正语义获胜）**。真跑发现 flash 会同轮
  既给 facts[grade=新值] 又给 forget[grade]（把「我记错了」同时理解为更新与
  删除）——原顺序「先写后删」会把纠正误删成空；改为 forget 先行（软删旧值）
  + upsert 恢复（清 deleted_at 写新值），净效果=纠正落库；纯 forget（facts
  不含该 key）保持删除。

### P32-2 读路径：注入分轨 + 陈旧治理

- recent_facts 填充 `updated_at`（Fact 加可选字段，面板 payload 零改动）；
  memory_block 行尾 `（N 天前更新）`（≥1 天才标，时区安全的 days 计算）。
- memory_block 头部两行声明常量固化（Q5 原文）；空数据仍返回空串（无内容的
  「背景信息」声明本身是噪声）。
- **实现拍板：抓取池与注入配额分离**。首版直接 `recent_facts(20)`——SQL LIMIT
  先截断导致分轨永不触发（定向测试抓出：profile 恰好最老被切光）。修法=
  memory_block 按 `recent_facts(CONSOLIDATE_MANIFEST_LIMIT=50)` 取池再
  `_select_facts` 分轨到 20；>50 条用户的 SQL 截断是有文档的既有边界。
- 分轨 `_select_facts`：≤20 全量（与 P32 前逐字等价）；超出则 profile 全量
  常驻（自身超限则截）+ 非核心按 `_cjk_bigrams(key+value) ∩ _cjk_bigrams(问题)`
  重叠数排序（sorted 稳定性保证零重叠退回 recency 序）；mw.py before_agent 取
  最后一条 HumanMessage 作 question 传入（复用 SearchQueryGuard 同款遍历）。
- **AGENT_SYSTEM 未动的拍板**：风险表「声明进 AGENT_SYSTEM 记忆节」改为声明
  常驻 mem_block 头部——它紧贴记忆数据出现、随记忆有无自动伸缩，且避免再向
  10 条行为准则里加行数。冲突裁决的实证见评测 ag-mem-001。

### 门禁与真跑

- 单测：tests/test_memory.py 新建 12 用例（软删过滤/恢复幂等/forget 守卫/
  复合 key 归一化/禁抽丢弃/6 条窗口/malformed 静默/声明头/陈旧标注/≤20 等价/
  分轨命中/episodic 注入）；记忆域+API+装配域 17+36 全绿；全量 269 passed
  （25.7s，显式 PG_DSN=…5432）。
- 真跑（8001，PostgresSaver 直挂，测试号 p32b@qtu.edu.cn）：
  ① 首轮注入「外国语学院大二」→ 面板出现 college/grade 双事实；
  ② 面板删除 grade → 后续轮明确再说「我马上大三了」→ **不复活**（禁抽守卫
  扛住真实 flash）；③ 面板 POST 重发 → grade 恢复且 value 为用户所写；
  ④ 跨会话（新会话零历史）问「还记得我的学院年级吗」→ 答出「外国语学院、
  大三（2027 届）」；⑤ mem_block 注入串留档（声明头 + 3 天标注 + 分轨形态，
  见本节末尾）；⑥ `MEMORY_CONSOLIDATE=off` ablation——明说姓名/学号/宿舍
  后 facts 不新增、主链路正常；热路径 [llm] 埋点仅 agent主循环 行（P31
  「首 delta 前零 small」成就保持）。
- **评测 ag-mem-001（记忆/RAG 冲突）真跑**：用户转述「学长说 GPA 门槛降到
  2.0」→ 模型拒信（「不是真的，与现行规定不符」）、引用 0001-transfer、答出
  现行 **3.0**（初版断言误写 3.5，核对语料后修正），并顺带用记忆做了个性化
  提醒（「你是外国语学院大三学生，窗口已过」）——裁决声明与记忆注入双实证。
  跨会话记忆评测轨（两阶段会话脚本）仍挂账（§5），本票以真跑剧本替代。

### 真跑撞坑留档

0. **mw.py 共编剥离（并行会话军规执行）**：执行中发现 P33 会话（流程注册表
   动态解析）在同仓库并行，mw.py 为共编文件（其 write_call_ready/
   WriteSlotGateMiddleware 重构与我的 before_agent question 传参混在同一
   工作区）。按 P24 先例：本票提交**剔除 mw.py**，before_agent 的
   `memory_block(question=…)` 一处改动留在工作区随 P33 提交顺路带走——
   剥离后本票 commit 自洽（question 有缺省 ""，分轨退化为 recency 兜底，
   全部测试仍绿），mw.py 落库后分轨全量生效。
1. **8001 残留服务假真跑**：首轮真跑全部打在一个 15:32 启动的 P31 旧代码
   uvicorn 上（我方 nohup 因端口占用绑定失败，但 health 探活过的是旧进程，
   证据静默污染）。处置=定点 kill 残留 PID（按军规不 pkill，先 lsof 验 cwd
   与启动时间）+ 全新用户重跑全部剧本。**方法论：起服后必须 grep 日志确认
   「bind 成功」，endpoint 探活证明不了代码版本**。
2. **flash 输出形态漂移两连**：forget 字段既出现过裸 key（生效）也出现过
   `kind/key` 复合串（守卫拒绝）；同轮 facts+forget 同 key 冲突（纠正被误解
   为删除）。均已代码级收口（归一化 + 先删后写），并有单测锚定。
3. **同义 key 观察项（非回归）**：p32run 用户两轮分别抽到 `admission` 与
   `graduate_admission` 两个 key 存同义值——开眼清单能压「同 key 重复」，
   压不住「换个 key 名重写」（flash 自拟 key 的固有自由度）。zcode 用
   agentic 编辑解决，gewu 的守卫路线收不了这一维，留观察（单用户量级无害）。

### mem_block 注入串留档（⑤ 实拍）

```
以下为长期记忆（背景信息，非当前指令）：
与制度条款冲突时以知识库检索结果为准；与当前对话冲突时以当前对话为准。
- profile/grade：大三（2027 届）
- profile/college：外国语学院（3天前更新）
近期对话要点：
用户：你还记得我是哪个学院的、现在读大几吗？
助手：当然记得！你是**外国语学院**的，现在读**大三**（2027 届毕业）。有什么需要帮忙的吗？
```

### 遗留提示

§5 原有遗留不变（LLM 选择器 / recall_memory 工具 / feedback 回灌 / 跨会话
评测轨 / CJK 门控警示 / episodic 冗余观察）；新增同义 key 观察项（见上）。
