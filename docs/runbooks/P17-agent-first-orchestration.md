# P17 编排层重构：agent-first 单循环（create_agent 底座）（任务书）

> **背景**：编排层现形态为「前置意图分类路由 → 分支图」（`graph.py` +
> `routing.py` cascade）。实测暴露结构性缺陷：五分类无闲聊归属，L1 提示词
> 明确把「闲聊」写进 refusal 定义（`routing_prompts.py:36`），「你好」被
> **按设计**路由到 refusal 节点输出硬编码话术（`prompts.py:46`）——打招呼
> 收到"范围外"。两轮开源调研（2026-10）定方向：单 agent + 工具自选 +
> lenient guard；实现底座**拍板 B：LangChain 1.x `create_agent` + middleware**
> （官方主线，`create_react_agent` 的后继），classic 分支保持手写图不动
> （对照组双底座叙事）。编号说明：P16 已被前端 shadcn 任务书占用，本任务
> 顺延 P17。

## 0. 拍板结论

| # | 问题 | 拍板 | 依据 |
|---|------|------|------|
| Q1 | 级联路由去留 | **保留为 `mode=classic` 实验链路**（分支图原样），不删 | 毕设三路线对照实验的基线；compare 页 B 轨依赖；论文需要「前置路由 vs 工具自选」实测对照 |
| Q2 | route 事件语义 | **两段式**：guard 出口发 provisional（intent 提示），循环收尾发 effective（按实际工具轨迹合成）；`chitchat` 为新增取值 | SSE 事件形状不变；前端 `patchLast` 覆盖式更新天然兼容多 route 事件；eval 的 set 聚合同理 |
| Q3 | research 形态 | **工具化为 `deep_research`**（内部复用 `run_research` 全管线），主循环可自主调用；`mode=research` 保留为评测直通 | chat-langchain/open_deep_research 的分流即「结构化工具 + agent 自主」；守卫：单轮限 1 次 |
| Q4 | 前端改动边界 | **仅 `labels.ts` 加一行 `chitchat: "寒暄"`**，其余零改 | 未知 route 值前端有 `?? route` 兜底，不加也只是显示原文；一行改动换徽章正确 |
| Q5 | 主循环模型档 | 主循环 standard；guard / 日期解析 small；deep_research 内部维持 flagship | 模型档策略从 `fill_policy` 路由级下沉到调用点级 |
| Q6 | **实现底座** | **B：`create_agent` + middleware**（2026-10-01 二次调研后拍板）；classic/direct/research 分支保持手写图 | 官方主线（生产级循环+中间件家族）；「轮子白嫖、验收自写」哲学；SummarizationMiddleware 顺手吸收 P13 顺延的上下文压缩；双底座（官方 harness vs 手写图）本身是论文素材 |

**二次调研印证（2026-10，论文素材）**：编码类 agent 三家
（[Codex CLI](https://openai.com/index/unrolling-the-codex-agent-loop/) /
[Gemini CLI](https://github.com/google-gemini/gemini-cli) / Claude Code）
全部为「单 agent 主循环 + 模型自主工具选择」，无一家做前置意图路由；规划
是工具（update_plan / enter_plan_mode）；危险控制 = 逐工具调用的代码级
审批门 + 沙箱，LLM 判险仅补充层；上下文靠阈值触发 auto-compact。
[AgentScope](https://github.com/agentscope-ai/agentscope) 1.x 教程把
「结构化输出前置分类 / 工具调用路由」并列为两种合法形态（恰对应本仓库
classic vs agent-first），2.0 收敛为单 Agent 循环 + 逐工具权限系统
（FAQ："单模型+单工具集不需委派时，单 agent 就够"）——gewu 的迁移方向
与该演化同构。WeKnora 插件式 pipeline 判定过重不采用（平台化工程选择，
AgentScope 2.0 亦从重编排后撤）。

**抄什么 / 不抄什么**（对照 chat-langchain 2026 形态 + create_agent 生态）：

| 开源机制 | 决策 |
|---|---|
| create_agent 主循环 + 官方 middleware（HITL / Summarization / ModelCallLimit） | **抄（Q6 底座）**：auto 主路换底座；classic 分支不迁 |
| guardrails：lenient、默认放行、greetings/身份/能力白名单 ALWAYS ALLOW、不确定放行、fail-open | 抄（自定义 GuardMiddleware，结构照搬 chat-langchain `guardrails_prompts.py`） |
| 主 agent prompt「能直接回答的问题（问候/澄清）立即回答，不调用工具」条款 | 抄（主循环 system prompt 头部） |
| 业务工具 safe/sensitive 分组 + 写操作确认门 | 等价物平移：写工具四件挂 interrupt 审批；只读工具不挂（lenient） |
| 检索即工具（search_knowledge） | 已有，随底座升级为标准 `@tool` |
| deepagents（Filesystem/SubAgent/Skills 全家桶） | 不抄（场景用不上且 Filesystem 不可拆） |
| guardrails 与主循环并行执行（省延迟） | 不抄（同步先跑，简单优先；延迟进对照报告） |
| 拒答话术动态生成 | 不抄：block 时沿用 `REFUSAL_ANSWER` 静态文案（eval 的「只能回答」断言依赖） |

参照系：[create_agent](https://docs.langchain.com/oss/python/langchain/agents) /
[middleware 家族](https://docs.langchain.com/oss/python/langchain/middleware/built-in) /
[自定义 middleware 钩子](https://docs.langchain.com/oss/python/langchain/middleware/custom) /
[HITL](https://docs.langchain.com/oss/python/langchain/human-in-the-loop) /
[流式（get_stream_writer）](https://docs.langchain.com/oss/python/langchain/streaming) /
[LangGraph v1 迁移指南](https://docs.langchain.com/oss/python/migrate/langgraph-v1) /
[chat-langchain](https://github.com/langchain-ai/chat-langchain)（演化史 + guardrails prompt）。

## 1. 目标 / 非目标

**目标**
- `mode=auto` 默认链路切换为：`resolve_query → mode_dispatch → agent 子图
  （create_agent + middleware）`；外壳保持手写薄图。
- GuardMiddleware（lenient + fail-open + 正则快路径）：闲聊/问身份能力不再
  触发拒答——「你好」由主循环直接自然回复（零工具调用）。
- 工具全面 `@tool` 化：search_knowledge / parse_date / deep_research（复用
  research 管线，单轮限 1 次）/ 8 个业务工具（薄包 `call_tool`）。
- 写操作确认门迁至 HITL 中间件（interrupt_on 写工具四件），chat.py resume
  桥翻译 decision 格式——SSE 事件形状与前端零改动。
- P10 截断防御铁律平移为自定义中间件。
- effective route 合成 + 两段式 route 事件；`chitchat` 新取值。
- `mode=classic` 收编旧分支图（cascade、transaction/hybrid workflow、
  tx_resume 原样可达，**不迁移**）；`mode=react` 与 auto 合并同路。
- SummarizationMiddleware 吸收上下文压缩（P13 顺延线收口）。
- 评测双轨：classic 28/28 回归 + agent-first 全量 + chitchat 新集 +
  双底座对照报告（延迟/token/正确率）留档。

**非目标**
- 不改 SSE 事件形状与校验序列（route 取值扩展除外）；前端只动 labels.ts 一行。
- 不删级联路由与其单测（Q1）；不动 rag / business 域内部。
- 不引入 deepagents；middleware 只采 HITL/Summarization/ModelCallLimit +
  三个自定义件（PII/Shell/Fallback 等不引入）。
- classic 分支不迁移 create_agent（对照基线必须保手写图形态）。

## 2. 设计与实现

### 2.1 图形状（P17-2 主票）

```
START → entry_gate → resolve_query → mode_dispatch（外壳，手写薄图）
    ├─ classic → route → {factual/refusal/research/hybrid/transaction}   [原样]
    ├─ direct  → retrieve → answer_direct                                [原样]
    ├─ research → research 管线                                           [原样]
    └─ auto/react → agent 子图 = create_agent(...)
```

装配（示意）：

```python
agent = create_agent(
    model=glm_standard,
    tools=[search_knowledge, parse_date, deep_research, *biz_tools],
    system_prompt=AGENT_SYSTEM,          # REACT_SYSTEM_HEAD 升级版
    middleware=[
        GuardMiddleware(),               # 自定义：before_agent
        ModelCallLimitMiddleware(...),   # 替代 REACT_MAX_TURNS=8
        TruncationDefenseMiddleware(),   # 自定义：P10 铁律（P17-4）
        HumanInTheLoopMiddleware(        # 写工具四件 interrupt_on
            interrupt_on={"book_venue": ..., "submit_leave": ...,
                          "cancel_booking": ..., "approve_leave": ...}),
        SummarizationMiddleware(trigger=("tokens", ...)),  # P13 收口
        RouteEventMiddleware(),          # 自定义：after_agent
    ],
    state_schema=...,                    # 扩 AgentState：route/tx 字段
)
```

- 子图嵌套外壳图，checkpointer 仍挂顶层（PostgresSaver 原样）；
  interrupt 从子图冒泡到顶层暂停，resume 走既有桥。**验证点**：子图嵌套
  下的 interrupt 冒泡 + 跨重启续办真跑。
- 消息历史由 AgentState 的 messages reducer 原生管理（跨轮在场），
  SummarizationMiddleware 防膨胀——不再需要自管 msgs 截断。
- `mode_dispatch` 替代原 route 节点的控制权；`fill_policy`/cascade 全套
  只服务 classic；`react.py` 五节点引擎随底座退役（防护语义平移见
  P17-3/4，git 历史留档）；`tx.py` 的槽位归一/classify_reply/
  execute_tool 继续复用。

### 2.2 GuardMiddleware（P17-1）

- **正则快路径**（零 LLM）：`你好|您好|hi|hello|在吗|你是谁|你能.*做|谢谢|
  再见` 等纯问候/身份问 → 直接放行 + provisional route=chitchat。
- **LLM 判定**（small 档）：`{"decision":"allow|meta|block","intent":"...",
  "reply":"..."}`；meta（软寒暄表达）→ reply 直答收尾；block（高置信
  范围外）→ `REFUSAL_ANSWER` + route=refusal；三原则照搬 chat-langchain：
  默认放行 / greetings·身份·能力 ALWAYS ALLOW / 不确定放行（中文重写，
  校园语境，"钱塘大学前缀再判"决断规则）。
- **fail-open**：无 key / 异常 / 解析失败 → allow，链路永不断。
- 单测：三 decision 全路径 + 快路径 + fail-open + 白名单用例（你好/
  你是谁/帮我写代码/今天股市）。

### 2.3 确认门迁移与 resume 桥（P17-3，硬点一）

- 写工具四件挂 `interrupt_on`（只读工具不挂=天然 lenient）；`when` 谓词
  留扩展位（如同工具的读式调用放行）。
- **chat.py resume 桥翻译**：现 `Command(resume=用户文本)` → HITL 的
  `Command(resume={"decisions": [{"type": ...}]})`；用现成 `classify_reply`
  把用户文本（确认/取消/修改）映射为 approve/reject/respond decision，
  **前端零改动**。修改槽位走 respond 决策回填模型续收集。
- pending_action / action_result / slot_question 事件：从 middleware 与
  工具函数内 `get_stream_writer()` 发（PARITY 形状不变）。
- **退路**：若 HITL decisions 语义与现有「修改→重确认 / 失败恢复→字段
  追问」行为对不齐，改为 `wrap_tool_call` 自定义中间件 + 内部 `interrupt(
  payload)` 保 tx_gate 原语义（决策点在 P17-3 实现中验证后定）。

### 2.4 P10 截断防御中间件（P17-4，硬点二）

- `finish_reason=length 且带 tool_calls` → 不执行工具、assistant 回填 +
  合成错误 observation 交模型重发（Pi 式，不记指纹）——官方无现成件，
  规则平移自 `react.py` `_route_after_agent`/`_make_truncated_node`。
- 单测：mock length 响应断言工具未执行、observation 回填、循环续跑。

### 2.5 事件发射点迁移 + deep_research + route 合成（P17-5，硬点三）

- **PARITY 事件搬家**：图节点 emit → 工具函数/middleware 内
  `get_stream_writer()`（工具 status、action_result、slot_question；
  middleware 发 route/guard 拒答）。事件 JSON 形状零改动。
  注意：`get_stream_writer` 仅图执行上下文内可用——单测须在图运行内
  断言事件（现有 emitter 测试模式相应调整）。
- `deep_research` 工具：schema 写明使用时机（多条件/并列/系统性梳理），
  内部调 `run_research`（flagship 档），observation 取综合摘要；
  **代码守卫：单轮限 1 次**（flash 无视否定指令必须代码兜底）。
- **effective route 合成**（代码常量表）：写工具确认/执行→transaction
  （本轮有检索→hybrid）；deep_research→research；仅读工具/检索→factual；
  零工具→chitchat；guard block→refusal。after_agent 合成，与 provisional
  不同则补发（`route_decision_evt`，layer=guard/effective）。
- `chat.py` mode 枚举扩 `classic`；react 与 auto 同路（语义注释）；
  `labels.ts` 加 `chitchat: "寒暄"`（唯一前端改动）。
- eval：`expect.route=agent` 且 mode=react 特判（run_eval.py:202-206）
  语义平移——react 即主循环，保持通过。

### 2.6 评测与依赖（P17-6/7）

- **classic 回归轨**：28 题 mode=classic，28/28 必须保持（降级不改行为）。
- **agent-first 轨**：同 28 题 mode=auto 全绿（refusal 3 题改由 guard 承担；
  tx 7 + hybrid 1 走确认门；multi_hop 8 可吃 deep_research 红利）。
- **chitchat 新集**（6~8 题：你好/在吗/你是谁/能办什么/谢谢/早安）：
  断言 = 不含「只能回答」+ 非空 + citations 空 + route 含 chitchat。
- **对照报告** `eval/reports/orchestration-{date}.md`：双底座双轨延迟/
  token/正确率（分类前置省调用 vs 工具循环开销 + 手写图 vs create_agent）。
- dataset-agent.jsonl 多轮用例（mode=react）全量回归（对话式收集防线）。
- **依赖**：引入 `langchain>=1.4`（现仅 langchain-core/langgraph），
  uv.lock 锁版本；升级走 minor changelog（middleware 生态 1.3.x 才密集
  补齐，迭代快是已知风险）。
- 文档：architecture 02（agent-first 主图+双底座注记）/03（guard + classic
  实验保留）/05（ReAct 篇改为 create_agent 底座叙述）/08（route 事件语义
  两段式 + chitchat）/10（双轨评测）；roadmap 勾选；PARITY 补「超出 Go
  终态的编排演进」注记。

## 3. 风险与守卫

| 风险 | 守卫 |
|---|---|
| langchain 1.x 迭代快（1.3 才密集补 middleware） | uv.lock 锁版本；升级独立 ticket 走 changelog |
| 子图嵌套下 interrupt 冒泡 / resume 路径与预期不符 | P17-3 首个验证项真跑（含跨重启续办）；退路 wrap_tool_call 自定义中断 |
| HITL decisions 语义 vs 现有修改/取消/失败恢复行为 | classify_reply 翻译层 + dataset-agent 多轮用例全覆盖；对不齐走退路 |
| get_stream_writer 仅图执行上下文可用 | 单测在图运行内断言事件；emitter 测试模式调整 |
| PARITY 事件搬家引入形状漂移 | 事件构造器（events.py）不动，只动发射点；28 题评测是保险丝 |
| flash 无视否定指令：deep_research 误触发 / 乱调写工具 | 单轮限 1 次代码闸；写确认门；ModelCallLimit |
| token/延迟上升（全工具 schema + 历史在场） | 预算闸（已有）+ 对照报告如实呈现；SummarizationMiddleware 控历史 |
| refusal 评测语义迁移（路由判定 → guard 判定） | 3 题 refusal 真跑 + guard 单测白名单 |

## 4. 验收门禁

- [ ] 单测全绿（guard / 截断防御 / route 合成 / resume 桥翻译；cascade
      与 tx 旧测保持）
- [ ] ruff + lint-arch 全绿；langchain 版本锁定（uv.lock）
- [ ] `make eval` classic 轨 28/28；agent-first 轨全绿（含 chitchat 新集）
- [ ] 前端 demo 真跑：寒暄自然回复（徽章=寒暄）/ RAG 引用 / 预约两轮确认
      办理（含修改与取消路径）/ 范围外礼貌拒绝；refresh 续办（PostgresSaver
      + 子图 interrupt 冒泡）
- [ ] 双底座双轨对照报告留档 eval/reports/
- [ ] 文档五篇 + roadmap 更新

## 5. 遗留与后续

- 影子路由混淆矩阵（可选追加）：agent-first 运行时 shadow 调 CascadeRouter，
  对照 effective route 量化「前置路由误路由率」——论文直接可用，随 P17-6
  顺手做。
- 上下文压缩（P13 顺延线）：**由 SummarizationMiddleware 吸收收口**，
  本任务完成即闭线（消融数据可在语料/对话变长后补）。
- classic 链路退役：论文完成后按 Go 退役模式（tag + 留档 + 删码）收口。
- middleware 扩展候选（后续观察）：ModelFallback、ToolRetry、
  LLMToolSelector（工具膨胀后预筛）——本任务不引入。

## 6. 执行记录（2026-10-01，当日完成）

实现序：P17-0（langchain 1.4.3 + create_agent/middleware/HITL 源码级 API 摸底，读
site-packages 定钩子签名与 jump_to 短路机制）→ P17-1/4/5 基础件（guardrails.py /
mw.py 九件 / agenttools.py / prompts AGENT_SYSTEM / state.messages / llm.agent_model
+record_usage）→ P17-2/3（agent.py 装配 / graph.py 外壳重构 mode_dispatch+agent_in+
agent_done / chat.py resume 桥+subgraphs=True / react.py+test_react.py 退役）→
测试（agent_fakes.py 共享假件 + test_guardrails 9 / test_agent_mw 14 / test_agent_flow
8，含 guard 图内短路、HITL 中断-resume-执行全链、多轮槽位收集回归）→ 评测双轨 →
文档收口。

**拍板转实现决策**（任务书未预见，实现中定）：
- guard 用方法级 `@hook_config(can_jump_to=["end"])` 的 `before_agent`（装饰器
  `@before_agent` 只适用于函数式中间件，类方法上会被吞——首轮真跑发现 guard 未生效）；
- 截断防御落点从任务书草案的「after_model 条件边」改为 `wrap_model_call` 的 handler
  重调——after_model 链上与 HITL 顺序纠缠，包裹层实现 Pi 式回填更干净；
- 指纹去重（旧②）随引擎退役不平移：repeat 场景由 ModelCallLimit 兜底（裁剪决策）；
- resume 翻译层：确认轮的 reason/purpose 自由文本补充=respond（修改），但确认词优先
  （防「确认」被自由文本 parse 吞掉）；
- 日期换算收回确定性层：工具描述强制传中文原文 + call_tool 前 normalize（LLM 自行
  换算把「12月1日」写死 2024 年——classic 靠解析器换算的同款纪律在 agent 底座复刻）。

**迁移撞坑（对后来者有值，已记入对照报告 §2）**：
1. 子图 custom 事件默认不冒泡——必须 `graph.stream(..., subgraphs=True)`（症状：
   徽章/状态静默丢失，route/status 全部消失但 answer 正常）；
2. guard 吃多轮短回复——办理收集轮「研讨间301」被安检判 meta 寒暄就地直答（修复=
   会话感知：历史存在 AI 消息时只放行，chat-langchain "NOT a follow-up" 条款）；
3. `snap.tasks` 在 langgraph 1.2 是 tuple 不是 dict（resume.py 做了双形态兼容）；
4. 无 key 环境 ChatOpenAI 构造即抛——api_key 占位 "not-set"（真实调用由 has_key 门禁拦截）。

**评测记录**（RATE_LIMIT=600 / MEMORY_CONSOLIDATE=off）：

| 轨 | 结果 | 说明 |
| --- | --- | --- |
| classic 28 题（--mode classic） | 28/28 | tx-007 hybrid 一次 flaky，重跑过——降级不改行为达成 |
| agent-first 28 题（mode=auto） | 27/28 | 唯一失败 tx-002（已知 flaky，同日 r4 通过）；factual 9/9、multi_hop 8/8（延迟 -38%）、refusal 3/3（guard 单跳 ~1-2s）、hybrid 1/1 |
| chitchat 新集 8 题 | 8/8 | 自然回复+零引用+route=chitchat；快路径轮 <1s |
| dataset-agent 8 题（--mode react） | 8/8 | expect.route=agent 特判语义平移后全绿 |
| 重启续办真跑 | ✓ | 预约停 HITL 门 → 杀进程 → 重启 → 「确认」→ VE-0269 落库（PostgresSaver + 子图 interrupt 冒泡） |

对照报告：eval/reports/orchestration-20261001.md（含三坑记录与双底座工程对照表）。
单测 183 全绿（新增 31：guard 9 / mw 14 / flow 8）+ ruff + lint-arch 全绿；前端改动
仅 labels.ts 一行 + api.ts ChatMode 扩 classic + compare 页 B 轨改 mode=classic（react
与 auto 同路后 B 轨对照意义收口），next build 绿。
