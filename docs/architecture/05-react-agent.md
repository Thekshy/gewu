# 05 · agent 主循环（create_agent 底座 · P17）

P17 起 `mode=auto/react` 的默认链路是 **agent-first 单循环**：LangChain 1.x `create_agent` 官方底座（生产级模型-工具循环 + middleware 家族）+ 五个自研中间件。模型通过原生 tool-calling 协议**自主决定**调用哪个工具、循环到信息足够为止；意图分流不再前置（[03](03-routing.md)），由 guard 安检（入口）与 effective route 合成（出口）两端表达。P14~P16 的手写 ReAct 子图（`react.py` 五节点引擎）已随本底座退役，其防护语义全部平移进中间件——本文按「官方件 / 平移件」双线拆解。classic 分支（mode=classic）保持手写图形态，构成双底座对照（论文素材，对照数据见 eval/reports/orchestration-*.md）。

## 装配

`gewu/agent/agent.py` 的 `build_agent`：create_agent 编译产物直接 `add_node` 嵌套进外壳图（checkpointer 只挂顶层，子图 interrupt 冒泡暂停，[02](02-orchestration-graph.md)）。模型经 `llm.agent_model()` 工厂构造（温度/max_tokens 固化在实例，不经 LLMService 的 bind 链；压缩摘要用小档 `agent_model(small=True)`）；工具集 `agenttools.py` 全量 `@tool` 化（11 个基线：检索/日期/deep_research + 8 业务工具；P26 联网开启时 +1=web_search）。装配常量：`AGENT_MAX_TURNS=8`（轮次上限，react.py 同值平移）、`AGENT_MAX_TOKENS=1200`（单次模型调用上限）、`SUMMARY_TRIGGER_TOKENS=30_000` / `SUMMARY_KEEP_MESSAGES=20`（上下文压缩）。

联网检索（P26，条件装配）：`settings.iqs_api_key` 非空时整链开启——web_search 工具注册、AGENT_SYSTEM 拼联网准则、WebSearchBudgetMiddleware 入栈；key 空=三处全部缺席（能力注入：配置里没有的工具，模型看不见）。适配层 `gewu/websearch.py` 走阿里 IQS（POST `/search/unified`，Bearer 鉴权；实测口径以 2026-10-01 真调为准——`contents` 字段勿传、`publishedTime` 为 ISO 串）。AGENT_SYSTEM 第 9 条同步通用化：校外问题尽力答（联网/通用知识+口径声明），仅危险违法才拒——原「引导回校园话题」废止。

## 中间件栈（执行序：wrap_* 外层=列表在前者）

| 件 | 来源 | 钩子 | 职责 |
| --- | --- | --- | --- |
| `GuardMiddleware` | 自研（chat-langchain 同构） | before_agent，can_jump_to=end | lenient 安检：正则快路径（纯问候零 LLM）→ LLM 判 allow/meta/block（fail-open）；block/meta 短路收尾。**P28 起 block 只拦内容安全**（危险/违法违规/学术不端，话术 GUARD_BLOCK_ANSWER）——范围外合法问题放行交主循环联网/通用知识（与 AGENT_SYSTEM 第 9 条同一堵墙）；classic 的 REFUSAL_ANSWER（范围外口径）保持基线零改动 |
| `ModelCallLimitMiddleware` | 官方 | wrap_model_call | `run_limit=8`（旧 REACT_MAX_TURNS 等价） |
| `TruncationDefenseMiddleware` | 自研平移 | wrap_model_call | **P10 截断防御铁律**：`finish_reason=length` 且带 tool_calls 时不执行，assistant 原样回填 + 合成错误 observation 重调 handler（Pi 式，重发不记指纹；上限 2 次防 length 死循环） |
| `UsageRecordMiddleware` | 自研 | wrap_model_call | token 记账走 LLMService 预算闸（与 classic 同口径）；P24-1 兼任观测——每次模型调用打 `[llm] agent主循环` 一行（ms + ctx_profile），补 create_agent 内部 model.invoke 不经 LLMService 封装的埋点盲区 |
| `AgentPromptMiddleware` | 自研 | wrap_model_call | system prompt（AGENT_SYSTEM）+ 记忆块尾部注入（`request.override(system_message=…)`） |
| `HumanInTheLoopMiddleware` | 官方 | after_model | 写工具四件 interrupt 确认门：`interrupt_on` 配 allowed_decisions=[approve,reject,respond] + `when=write_call_ready` 谓词（参数不齐不中断，交给槽位门） |
| `PendingActionMiddleware` | 自研平移 | after_model | 确认摘要（pending_action + 文案）在 HITL 中断**之前**发射——interrupt 节点零副作用纪律（P14）的延续 |
| `WriteSlotGateMiddleware` | 自研平移 | wrap_tool_call | 写工具缺必填参数不执行：emit slot_question + 引导模型向用户收集（classic advance 追问语义的事件级等价物） |
| `ResearchLimitMiddleware` | 自研 | wrap_tool_call | deep_research 单轮限 1 次（flash 无视否定指令必须代码兜底） |
| `WebSearchBudgetMiddleware` | 自研 | wrap_tool_call | web_search 每日次数闸（P26；IQS 按次计费，agent 循环失控即烧钱——进程内 date 键计数，超限回执降级；仅联网开启时入栈） |
| `SearchQueryGuardMiddleware` | 自研 | wrap_tool_call | search_knowledge / web_search（P26 扩）检索词与原问题 CJK bigram 零重合时拼回原话（P24-3；docstring 引导是软防线，本件是硬防线；deep_research 不拦——子问题是 plan 拆解产物） |
| `RouteEventMiddleware` | 自研 | after_agent | effective route 合成补发（两段式第二段；guard block/meta 轮跳过） |
| `SummarizationMiddleware` | 官方 | — | 上下文压缩（30k tokens 触发、保 20 条）——P13 顺延线收口 |

## 防护语义平移对照（react.py → 中间件）

| 旧四件套 | 新落点 |
| --- | --- |
| ① 唯一终止判据（无 tool_calls 即终答） | create_agent 原生循环（`model_to_tools` 条件边） |
| ② 指纹去重 | 随引擎退役——repeat 场景由 ModelCallLimit 兜底（平移裁剪决策：flash 场景指纹误伤率高于死循环率） |
| ③ 轮次上限 + 到顶收敛 | ModelCallLimit（exit_behavior=end 注入人工收尾消息）+ agent_done 的 partial_answer 兜底 |
| ④ 截断防御（P10 铁律） | TruncationDefenseMiddleware（落点从图条件边改为 wrap_model_call 的 handler 重调——after_model 链上与 HITL 顺序纠缠，包裹层更干净） |

## 运行时环境传递

运行时对象不能进 state（checkpointer msgpack 序列化拒绝，P14 硬约束）。P17 的解法比 P14 更彻底：**工具以闭包持有 business/tools/retriever**（`build_agent_tools` 装配期捕获），role/user/mem_block 经 `GewuAgentState` 的普通字段随外壳图 state 流入子图（自定义 TypedDict 字段天然过 input schema）——`config.configurable` 通道只剩 checkpointer 自己用。工具内取 state 经 `ToolRuntime`（langgraph 原生注入：`runtime.state`/`runtime.tool_call_id`）。

## 工具表（agenttools.py）

| 工具 | 类型 | 说明 |
| --- | --- | --- |
| `search_knowledge` | 读 | 混合检索；返回 `Command(update={messages, citations})`——observation 与引用通道一次更新（citations 带 (doc_id,title) 去重 reducer，并行 Send 安全合并） |
| `web_search` | 读 | 联网检索（P26 条件注册；P29 三改）：IQS 适配层 `gewu/websearch.py` 防腐翻译——返回 (hits, status) 三分支（ok/empty/error：无命中引导换词重试，不可用如实降级）+ freshness 时间窗（day/week/month/year → 顶层 timeRange，四档实测生效，治时效题旧闻混排）+ canonical URL 去重；citations 通道 source 统一「联网检索」组（站点名进标题）；mainText 有意不取（token 成本），全文抓取二期须带 SSRF 校验 |
| `parse_date` | 读 | 确定性日期解析（`gewu/dates.py`），零 LLM 成本 |
| `deep_research` | 读 | 复用 research 管线（plan→逐路检索→聚合，flagship 综合留在主循环）；ResearchLimit 单轮 1 次 |
| 8 个业务工具 | 读 4 / 写 4 | 薄包 `call_tool` 单一出口；**不做角色过滤**——权限判定保持在工具层单一出口，越权回执是有效 observation（模型转述，ag-read-002/tx-006 断言语义） |

日期换算纪律（迁移中撞出来的坑）：工具描述强制日期参数传**中文原文**（「明天」「12月1日」），由 `normalize_tool_args` 在 call_tool 前过确定性解析器归一——LLM 自行换算会把「12月1日」写死成错误的年份。

## 写操作确认流（HITL）

模型发起写工具调用后：参数不齐 → WriteSlotGate 拦截并引导收集；参数齐 → PendingActionMiddleware 发确认摘要 → HITL after_model `interrupt(HITLRequest)`（子图冒泡暂停）→ 用户回复经 `resume.py` 翻译为 decisions（approve=确认 / reject=取消 / respond=修改重发或切话题）→ 放行执行 → action_result 回执。**模型可发起写操作，不能拍板**——与 classic 的 tx_confirm/tx_gate 语义对齐（[06](06-transaction.md)），前端零改动。

## 相关文件

| 文件 | 职责 |
| --- | --- |
| `gewu/agent/agent.py` | create_agent 装配与中间件栈 |
| `gewu/agent/mw.py` | 自研中间件族 + GewuAgentState + effective_route/槽位门纯函数 |
| `gewu/agent/guardrails.py` | GuardMiddleware（快路径正则/lenient prompt/fail-open/会话感知） |
| `gewu/agent/agenttools.py` | @tool 工具集（事件就地发射 + Command 状态更新） |
| `gewu/websearch.py` | IQS 联网搜索适配层（P26；支撑域，反向禁依赖 agent——lint-arch 守护） |
| `gewu/agent/resume.py` | resume 桥翻译（用户文本 → HITL decisions） |
| `gewu/agent/tools.py` | 业务工具表、权限矩阵与 call_tool 单一出口（双底座共用） |
| `tests/test_agent_flow.py` / `tests/test_agent_mw.py` / `tests/test_guardrails.py` | 全链流测试 / 中间件单测 / guard 单测 |

---

下一篇《06 · 知行执行层》进入写操作的完整生命周期：classic workflow 与 agent HITL 两条确认链、失败恢复与权限矩阵。
