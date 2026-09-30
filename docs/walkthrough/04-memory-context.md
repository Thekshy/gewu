# 04 · 记忆与上下文装配

> 「上下文准备」= 往 prompt 里放什么、按什么时机放。格物的答案是一套五层模型 +
> 写时萃取 / 读时近因的最小实现。实现在 internal/agent/memory.go 与 query_rewrite.go。

## 五层上下文

| 层 | 内容 | 生命周期 | 读取策略 |
|---|---|---|---|
| 稳定层 | system prompt、工具清单 | 跨会话 | 静态前置（prompt cache） |
| 长期语义记忆 | 用户稳定事实（facts，Kind/Key/Value） | 跨会话 | 预算内近因 |
| 短期情景记忆 | 会话对话要点（episodic） | 会话内 | 近因窗口 |
| 工作记忆 | 任务槽位/阶段（TxSession） | 任务内 | **直接全量读，不检索** |
| 检索层 | RAG 命中、工具结果 | 单轮 | 每轮现查，用完即弃 |

工作记忆和记忆必须分开通路：TxSession 是权威状态机不是语料，检索式读取反而引入
丢失风险——它走 SessionStore 独立通路（SQLite，跨重启续办）。

## 写路径：回合结束的异步固化

`consolidateAsync`（pipeline 出口调用）双通道：

- **episodic 同步写**：本轮问答要点 append 进会话级存储（失败只告警）；
- **facts 异步抽取**：goroutine + 独立超时，flash 从本轮对话抽稳定事实 UPSERT 入库
  （「大三/绩点 3.2/辅导员是某老师」这类跨会话有效的事实）——**不阻塞回答路径**。

写时萃取（而非读时现算）是 mem0 式路线的最小子集：有 ADD/UPDATE 语义（UPSERT），
暂无显式 DELETE/NOOP 决策与相似度冲突比对——规模未到，先不做。

## 读路径：一个回合开始读三次、两个消费者

```
回合开始
├─ Sessions.Get                工作记忆：槽位/阶段（直接读）
├─ ResolveQuery                理解侧：facts(N) + episodes(6) → flash 指代消解
│                                （四重门控：开关/key/有历史/命中指代词——单轮零成本）
├─ decideRoute(补全后的问题)    路由消费②的产物——记忆间接影响路由
└─ 生成侧 assembleMessages     [system(准则), system("已知用户信息:"+facts+episodes 4),
                                 user(参考资料+问题)]
     ReAct 同源：reactSystemPrompt(memoryBlock) 进 system
```

同一份记忆被读两次、拼两次，预算各自独立（理解侧 6 条 episodes，生成侧 4 条）——
**一次记忆多处受益**，与查询改写「一次补全贯通路由与检索」是同一设计手法。

## 业界长期记忆四条路线（格物的定位参照）

| 路线 | 代表 | 机制 | 适合 |
|---|---|---|---|
| 抽取式 | mem0 | 写时抽取 + 四操作决策（ADD/UPDATE/DELETE/NOOP） | 个性化陪伴——**格物 facts 是其最小子集** |
| 自编辑分页 | Letta / MemGPT | 窗口内主存 / 窗口外外存，模型自改记忆块 + sleep-time 后台整理 | 超长期自主体 |
| 时序图谱 | Zep / Graphiti | 双时间轴知识图谱，新事实软失效旧边（不覆盖历史） | 实体关系 +「当时为真」类问题 |
| 文件式 | Anthropic memory tool、Devin | 记忆即文件，追加 + 重读（Devin 刻意不做向量检索） | 工程上最朴素稳健 |

短期记忆业界高度收敛于「滑动窗口 + 压缩（compaction）」——近几轮留原文、更早的摘要
成一段（Claude Code auto-compact、OpenAI threads 截断都是此模式）。格物「每轮存要点」
是把压缩摊薄到每轮的轻量版。

各层用什么存储、向量库表结构与读写路径的细节见 [08](08-storage-choices.md)；
任务中途暂停/退出的恢复方案见 [09](09-durable-execution.md)。

## 装配顺序铁则

1. **稳定前置，易变后置**：system 准则 → 记忆块 → RAG 资料+问题。记忆块若放最前，
   每轮变化都会打碎 prompt cache 前缀；
2. **最需盯住的内容靠近生成端**：当前问题与证据在尾部（attention 的首尾偏置）；
3. **分节打标签**：「已知用户信息：」前缀是简化版节标签——让模型分得清长期事实与
   本轮查到的资料。

## push 模式与它的演进方向

格物是纯 **push**：回合开始系统决定读什么塞进 prompt。业界成熟形态是 push + pull
混合——push 小而稳的核心块（画像+近期要点），pull 其余（给模型 memory_search 工具，
Letta archival / Anthropic memory tool 模式）。pull 解决「检索漏召回」，代价是多一轮
工具调用。十级记忆量下纯 push 是正确选择；记忆跨大量会话增长后的演进路径：

1. facts 从近因换相关性召回（向量检索，machinery 与知识库同构复用）；
2. facts 加冲突决策（mem0 四操作：ADD/UPDATE/DELETE/NOOP）；
3. 需要时挂 memory_search 工具转 push+pull。

## 边界与已知短板

- facts 按近因取（`RecentFacts` 上限常量），量大会淹没关键旧事实——演进方向如上；
  无语义去重与冲突决策——活标本见文末附录的真跑发现；
- 无遗忘策略（TTL/衰减）与记忆质量评测（业界 LoCoMo 类基准），属规模未到的刻意不做。

## 附：worked example——一个用户的三回合（2026-09-07 真跑验证）

> 设定：学生用户（演示模式 user 固定 `demo-student`），`session_id` 全新。
> 流程为设计路径，证据为当日真跑的 SSE 事件与落库数据
> （服务按评测口径 `RATE_LIMIT_PER_MINUTE=600` 起停）。

### 回合 1｜首问「我是大三计算机专业，绩点3.4，转专业到软件工程要什么条件？」——建立记忆

- 门控：无指代词 → `ResolveQuery` 直接跳过，**零记忆读取**；
- 路由 factual（L1 conf=0.8）→ 直答，引用 3 篇；
- 写回：episodic 追加本轮 user/assistant 对；facts 异步 UPSERT（年级/专业/绩点）。

### 回合 2｜追问「那我挂过一门课还能转吗？」——记忆生效的一回合

- 读② `ResolveQuery`：facts + episodes(6) 拼给 flash，补全成自包含问题；
  **真跑证据：route reason = "…；已结合会话上下文补全指代"**；
- 读③ 生成侧 `assembleMessages` 记忆块进 prompt；
  **真跑证据：回答引用画像作个性化判断**（"您的绩点 3.4 满足 GPA 要求；但您有一门
  课程不及格，不满足…"）——是针对这个学生答的，不是条款复读；
- 写回：episodic 追加；facts 无新增（画像未变）。

### 回合 3｜办理「帮我预约明晚的羽毛球馆」——工作记忆

- L0 规则秒判 transaction：**1.2s 完成**（对比直答链路 16~21s），by_llm=false 零 LLM 路由成本；
- 槽位自动抽 venue/date，只追问时段（`slot_question`）→ TxSession 落库（Phase=collect）；
- 「晚上七点」「确认」两轮命中**续轮拦截**（route reason=「继续办理：预约场馆」，
  不走路由）→ `pending_action` 有序参数表（场馆/日期/时段）→ 执行 →
  **回执 VE-0161** → TxSession Clear，工作记忆生命周期结束。

### 落库证据

本会话 episodic 共 10 行（5 轮 × user/assistant 对），与事件流逐轮对齐；
facts 侧 `grade` 由旧值 UPSERT 为「大三」、新增 gpa/major——**同 key 覆盖与读写闭环
均验证成立**。

### 真跑发现：facts 污染是边界的活标本

demo-student 历史测试累积了 60+ 条 facts，暴露两个真实问题：

1. **同语义多 key**：`failed_course` / `failing_course` / `course_record` / `situation`
   全是"有一门课程不及格"——抽取没有 key 归一，语义去重缺失（正是 08 篇所述
   mem0 四操作决策要解决的问题）；
2. **矛盾事实并存**：不同会话留下的请假记录（明天病假 / 下周事假 / 12 月病假）
   互斥却共存；且回合 1 的回答引用了旧 facts（"系统记录显示您的年级为大一"）与
   用户自述对照——记忆确实进了 prompt，只是没被清理过。

`maxFactsInContext=20`：60+ 条时注入哪 20 条取决于 (kind,key) 排序——污染会挤占
记忆预算。清理方式：停服后 `rm data/memory.db`（重启自动建表；2026-09-07 已执行）。
