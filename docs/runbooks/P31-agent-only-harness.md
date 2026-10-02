# P31 harness 收敛单循环：guard 降码闸 + classic 退役 + 外壳塌缩（任务书）

> **背景**：P17 落地的形态是「外壳手写薄图 + create_agent 子图 + classic
> 对照分支」三件套，其中 classic 分支与级联路由是 P17-Q1 为论文基线保留的
> 临时形态。2026-10-02 架构复盘（逐点审计 + 四家生产级开源系统调研）拍板
> 收敛：生产路径（auto）上**前置分类不承重**——route 事件是 RouteEventMiddleware
> 事后合成的观测标签，不决定链路/工具集/模型档；agent 路径唯一的串行小模型
> 调用只剩 guard 首触分类，而 P28 收窄后其净收益趋零。**用户拍板：classic
> 退役不等论文评测，harness 的高效正确优先**——P17-Q1「保留 classic 为论文
> 基线」就此作废，评测轨改 agent-only，classic 28 题历史报告留档即可。
> 编号说明：P29 已提交未上线、P30 流式撤回并行会话完成未 commit，本票顺延 P31。

## 0. 拍板结论

| # | 问题 | 拍板 | 依据 |
|---|------|------|------|
| Q1 | classic 退役时机 | **本票退役**（tag `classic-pre-retirement` 留档） | 用户拍板论文价值不纳入本票考量；维护 classic 是纯成本（级联路由/tx 三判断/refusal 链均已被 agent 侧对应物取代） |
| Q2 | 前置意图识别去留 | **全部退役**：控制流 = 模型 + 工具 + 中间件闸；route = 纯观测标签（事后合成） | 生产路径实测：分类不决定任何控制流；Codex/Gemini CLI/OpenHands/chat-langchain 四家生产系统无一做前置意图分类，判断预算全在动作级（approval policy / SecurityAnalyzer / 工具审批） |
| Q3 | guard LLM 分类去留 | **删除** `classify_guard`；保留 GREETING_RE 正则快路径（零成本 provisional route）；新增危险词正则硬红线 block；meta 出口删除（未覆盖的软寒暄交主循环自然答） | P28 收窄后 LLM 分类的净收益 ≈ 拦变体危险话术，代价 = 首问串行一跳 + flash 判定非确定（两次线上实证的掷骰子问题）；关键词闸 + prompt 墙（AGENT_SYSTEM 第 9 条）+ HITL 代码闸构成纵深 |
| Q4 | 危险词硬红线形态 | 关键词/正则词表挂 `guardrails.py` 顶部（制毒/黑客/诈骗/代写/作弊核心词），词表丰富化挂账后续小票 | 首版求正确性与零成本，不求召回率——漏放的爆炸半径被 prompt 墙 + HITL 约束 |
| Q5 | resolve_query 去留 | **整节点退役**：agent 路径模型看 messages 历史自行消解指代；mem_block 装配搬入 AgentPromptMiddleware（P31-3） | 改写是增强不是依赖（四门控本就静默回退）；agent 路径上与主循环能力冗余 |
| Q6 | mode 参数收窄 | 服务端枚举收窄 `auto`/`react`（react=auto 语义既有），`direct`/`research`/`classic` → 422；前端 `ChatMode` 同步收窄 | classic 链路删除后 direct/research 无处可去；compare 页 B 轨随之失义 |
| Q7 | compare 页处置 | **删除**（路由 + 页面 + api.ts 双轨函数），P19 对照报告留档 | 对照使命完成；compare 双轨零改动保对照干净的约束随之解除 |
| Q8 | 塌缩后调用形态 | chat.py **直调 create_agent 编译产物**（`app.state.graph` 指向子图本体）；checkpointer 直挂（`create_agent(checkpointer=...)`，site-packages factory.py:833 已验证参数存在） | 外壳 StateGraph 的全部职责（entry_gate/mode_dispatch/resolve/agent_in/agent_done）随 classic 死亡或搬迁，无存在必要 |

**调研补强（2026-10-02，论文/答辩素材）**：chat-langchain 现版 guard
是「每轮判 + 喂 3 条历史」（"One classifier, every turn"）——它保留输入
分类因为它仍做 topic gating（`block_off_topic=True`）；gewu P28 已放弃
topic gating，故连「每轮判」的讨论都不适用，直接删除。mastra 把 workflow
与 agent 做成双一等原语并给出判据（"Use the model for judgment. Use a
workflow for control"，同 Anthropic 二分）——gewu 终局选边 agent，classic
即被选掉的那一侧。OpenHands 事件驱动（无状态 step + EventLog + 逐动作
风险评级）、Codex 审批×沙箱双轴、Gemini CLI ToolRegistry+审批模式，共同
印证：**「一个循环」是编排共识，「闸在动作级」是部署共识**。

## 1. 目标 / 非目标

**目标**
- **热路径小模型清零**：一轮 auto 请求从进入到首个 `answer_delta`，中间
  零次 flash 调用（guard 关键词化后达成；改写/路由/tx 判断随各自链路消失）。
- **classic 全量退役**：routing 域、graph classic 侧、classic research 节点、
  refusal 链、tx 流程节点；tx/research 模块按 agent 侧依赖拆分保留。
- **外壳塌缩**：端点直调 agent 子图，外壳 StateGraph 删除；checkpointer
  直挂；resume 桥塌缩单形态。
- 判断架构终局表述：**动作级代码闸是唯一下限机制**（HITL 写确认 / 槽位
  门 / 预算 / 检索词守卫 / 联网限额 / 轮次上限）；软语义全部交主模型 prompt。

**非目标**
- 不动 rag / business / auth / session / memory 域内部。
- 不改 SSE 事件形状：route 照发（事后合成）、done 单点、citations /
  pending_action / action_result / slot_question / follow_ups 全部不动。
- 不做 tool retrieval（现 11 工具规模不需要；膨胀阈值与候选记入遗留）。
- 不引入 System One 决策模型（Jev/Laya 候选仍挂账；其经典插槽 L1 随
  classic 退役消失，重新评估见 §5）。
- 不动 followups / 记忆固化 / 摘要压缩（不在主路径，与收敛无冲突）。

## 2. 设计与实现

### 2.1 P31-1 guard 降码闸（先行票，独立可发，不依赖 classic 删除）

- `guardrails.py` 删 `classify_guard` 的 LLM 调用与 `GUARD_SYSTEM`；
  `guard_update` 收缩为三分支：
  1. `in_conversation` → 直通 allow（无事件，现状保留）；
  2. `GREETING_RE` 命中 → allow + provisional route=chitchat（现状保留，
     零成本徽章早亮）；
  3. 危险词正则命中 → block：`GUARD_BLOCK_ANSWER` + provisional
     route=refusal + `jump_to=end`（话术沿用，不新造）。
- meta 出口删除：GREETING_RE 未覆盖的能力问/软寒暄直接进主循环自然回答
  （一次主模型调用，质量优于 flash 生成的 reply）。
- `GuardMiddleware.__init__(llm)` 的 llm 参数随之移除（装配点同步）。
- 测试：`test_guardrails.py` LLM 判定用例改写为关键词表用例（命中/未命中/
  in_conversation 直通/快路径）；fail-open 语义随 LLM 删除自然消失，删除
  对应用例。
- 验收：真跑「你好」「你能做什么」「怎么代写论文」三型，观察首 answer
  前无 `[llm]` small 埋点。

### 2.2 P31-2 classic 退役（删码票，tag 后一次删净）

**删除清单**（依赖边已 grep 验证）：
- `routing.py` + `routing_prompts.py` 整删（agent 侧无依赖；guardrails 自有
  GREETING_RE；`heuristic_route` 仅 routing 内部使用）。
- `graph.py` classic 侧：节点 `route/retrieve/answer_direct/refusal/research/
  transaction/hybrid/hybrid_tx/tx_confirm/tx_gate/tx_resume` 与分派函数
  `entry_gate/mode_dispatch/route_branch/_after_*` 全删；`resolve_query`
  整删（Q5）；`build_graph` 随之外壳消失（塌缩并入本票或 P31-3，见下）。
- `tx.py` 拆分：`slot_meta/FLOW_DEFS/SLOT_ORDER/normalize_slot/build_confirm`
  → 新模块 `txmeta.py`（`mw.py:46` 与 `agenttools.py:24` 依赖）；
  `make_advance/classify_reply/llm_extract_tool/llm_extract_slots` 删除；
  `execute_tool` 与 `_parse_date_slot` 等实现时 grep 定夺（agent 写执行走
  `agent/tools.py call_tool`，classic 专用件随删）。
- `research.py` 拆分：`plan` 保留（`agenttools.py:205` deep_research 依赖）；
  `run_research` 仅 classic 图节点引用（`graph.py:446`），随删；模块重组为
  纯函数集。
- `prompts.py`：`REFUSAL_ANSWER`（classic 范围外口径）/`QUERY_REWRITE_SYSTEM`
  随删；`ANSWER_SYSTEM` 实现时 grep（疑仅 answer_direct 用）；`AGENT_SYSTEM`、
  `GUARD_BLOCK_ANSWER`、`NO_DATA_ANSWER`（检索工具侧）保留。
- 前端：`ChatMode` 收窄（`lib/api.ts:97`）；mode 选择 UI 与 `/compare` 页
  删除（Q7）；console/admin 不动。
- eval：`run_eval.py` classic/direct/research 轨删除；`dataset.jsonl` 的
  classic 条目改 mode=auto 重跑或移历史；refusal 用例口径改（guard 关键词
  block 或主循环拒答，断言文案相应调整）；`dataset-chitchat/agent` 原样。
- 测试：`test_routing.py`（191 行）整删；`test_tx.py`（93 行）保留槽位
  元数据/解析部分、删流程用例；`test_agent_flow.py`（337 行）classic 链路
  用例删、agent 用例全留；`test_chat_api.py` mode 枚举用例改。
- 文档：architecture 02（单循环主图）/03（路由篇改「route=观测标签」叙事）/
  05（ReAct 篇 create_agent 独唱）/08（§2.4 mode 枚举 + §3 route 口径）；
  PARITY 注记「编排演进第二跳：classic 退役」；roadmap 勾选。
- **tag `classic-pre-retirement`** 打在删除前最后一个绿 commit。

### 2.3 P31-3 外壳塌缩（硬点票；若 2.2 顺做完可并票，独立保留为回滚缓冲）

- `build_agent` 签名扩 `checkpointer=None, memory=None`；`create_agent(
  ..., checkpointer=checkpointer)` 直挂（factory.py:833 参数已验证；真跑
  验证 PostgresSaver 与 thread_id 语义——**若直挂不通，退路**：保留
  `START→agent→END` 一层空壳 StateGraph 只挂 cp，其余塌缩收益不变）。
- `AgentPromptMiddleware` 接 memory：每 run 从 `MemoryStore` 装配 mem_block
  （user/session_id 从 `GewuAgentState` 读——字段已在，实现时确认透传）。
- 端点输入构造：`new_state` 逻辑并入 chat.py——`messages=[HumanMessage(
  question)]` + question/mode/role/user/session_id/citations 清零，形状对齐
  `GewuAgentState`；`agent_in` 的 citations 清零语义（P26 跨轮污染修复）
  必须保住（测试回归覆盖）。
- resume 桥单形态：`find_hitl_payload`/`hitl_decisions` 保留，classic
  tx_gate 原文 resume 分支（`chat.py:155` else 路径）删除。
- `graph.stream(..., subgraphs=True)` → 去掉 `subgraphs`（无嵌套图）；
  **行为变更点**：custom 事件不再是 `(namespace, event)` 元组，
  `chat.py:202` 的 `chunk[-1]` 解包删除；`test_agent_stream.py` 回归。
- `app.py`：`app.state.graph = build_agent(...)`（`build_graph` 退役）；
  sessions 删除连带 checkpoint 清理逻辑核对（P22 语义不变）。

## 3. 风险与守卫

| 风险 | 守卫 |
|---|---|
| checkpointer 直挂 create_agent 与嵌套语义有差（interrupt 冒泡/状态归属） | P31-3 首个真跑项：预约两轮确认 + refresh 跨重启续办；退路空壳图 |
| subgraphs=False 后事件形态变更漏改 | chat.py 解包同步删 + test_agent_stream 全量回归；事件构造器 events.py 零改动 |
| mem_block 搬迁后装配时机变化（每 run vs 每轮） | AgentPromptMiddleware 单测断言注入内容；多轮记忆真跑 |
| citations 跨轮污染回归（P26 修复丢失） | 输入构造显式清零 + 两轮检索对话测试用例 |
| guard 关键词表召回不足（变体危险话术漏放） | prompt 墙 + HITL 兜底为设计内纵深；词表丰富化挂账（§5） |
| 删除面大引入隐性依赖断裂 | 全量 pytest + lint-arch（更新其引用清单）+ ruff + tsc + build + design-lint |
| eval refusal 断言口径漂移 | P31-2 内重跑 refusal 用例真验，报告留档前后对照 |
| classic 历史不可回溯 | tag `classic-pre-retirement` + P17/P19/P25 执行记录已留档 |

## 4. 验收门禁

- [ ] 单测全绿（guard 关键词版 / agent mw / flow / stream / chat api mode 收窄）
- [ ] ruff + lint-arch + tsc + build + design-lint 全绿
- [ ] **高效实证**：直答轮 `[llm]` 埋点序列显示首个 answer_delta 前零
      small 调用（log-report.sh chars= 口径兼容）；对照 P27 基线留档延迟差
- [ ] **正确实证**：done 单点 / route 事后合成 / HITL 写确认全链 / 槽位
      门 / 双预算闸回归全绿
- [ ] 真跑剧本：直答带引用 / 预约两轮确认（含修改与取消）/ 寒暄自然回复 /
      危险词 block / followups / refresh 跨重启续办
- [ ] tag `classic-pre-retirement` 存在且指向删除前绿 commit
- [ ] 文档四篇 + PARITY 注记 + roadmap 勾选

## 5. 遗留与后续

- **危险词黑名单丰富化**：P28 挂账延续，独立小票（词表 + 可能的变体正则）。
- **System One（Jev/Laya）**：经典插槽 L1 已随 classic 退役；若后续需要
  确定性决策，新插槽候选 = guard 硬红线语义扩展（拦变体危险话术）或 tool
  retrieval 前筛（工具膨胀后）。重新评估时旧咨询结论仍有效（Jev 实验/
  Laya 生产、大陆中文自跑）。
- **tool retrieval**：工具数逼近 ~20 时立项（P17 遗留 LLMToolSelector 候选
  同源）；11 工具现状不做。
- **run_eval 认证适配**：原有挂账，不随本票。
- **出侧安检（after_agent）**：LangChain 官方 guardrails 文档推荐的分层
  第四层（输出 SAFE/UNSAFE 分类改写），gewu 当前空位，候选票。

## 6. 执行记录（2026-10-02 执行完毕）

**结论先行**：三票全部执行完毕、门禁全绿、真跑剧本全过，分三 commit 落库
（P31-1=19125c1、P31-2=9fd62b9、P31-3=bb6eeef；tag `classic-pre-retirement`
指向 19125c1=删除前最后一个绿 commit）。checkpointer 直挂 create_agent
一次通过，**退路空壳图未启用**。不 push 不上线，等拍板部署。

### P31-1 guard 降码闸（19125c1）

- guardrails.py 收缩为三分支：in_conversation 直通 / GREETING_RE 快路径
  （provisional=chitchat）/ DANGER_RE 硬红线 block；meta 出口与 LLM 判定
  （GUARD_SYSTEM/classify_guard/fail-open）删除；GuardMiddleware 去 llm 参数。
- **实现拍板：DANGER_RE 用实施性复合词而非裸核心词**。裸「作弊/诈骗/黑客」
  会误拦「学校对考试作弊的处分规定」「我被电信诈骗了该怎么办」「我想选黑客
  攻防选修课」——这类咨询是校园助手高频合法输入。故代写/作弊/黑客/诈骗挂
  实施性后缀（作弊.{0,8}不被发现、诈骗话术等），制毒/冰毒/摇头丸/违禁药品/
  代考/替考等无歧义 perpetrator 词直接命中。漏放（如「怎么在考试里作弊」
  无后缀变体）由 prompt 墙兜底，符合 Q4「求正确性不求召回率」。
- 测试改写：test_guardrails 全量换血（命中/未命中/直通/软寒暄/空问题）；
  test_agent_flow 的 meta 用例改为「软寒暄交主循环自然答」回归。
- 真跑（8001）：你好/你能做什么 → guard chitchat 徽章 + 主循环自然寒暄
  （4.4~5.1s）；怎么代写论文 → **21ms 全链短路、零 LLM 调用**。
- **真跑方法论坑**：三型探针共用一个会话时，第 2/3 问因历史含 AI 消息走
  in_conversation 直通——危险词探针必须独立会话首轮触发（会话感知条款的
  设计内行为，初测误判为"词表未命中"）。

### P31-2 classic 退役（9fd62b9，+523/−2542）

- 删除面：routing.py/routing_prompts.py/test_routing.py 整删；graph.py
  classic 侧 11 节点 + resolve_query + 全部条件边；tx.py 整删（元数据拆
  txmeta.py）；research.py 收敛为 plan 纯函数；prompts.py 删 6 个 classic
  专用提示词；QUERY_REWRITE 开关退役；前端 compare 页/mode 选择 UI 删除、
  ChatMode 收窄；run_eval mode 收窄 + refusal 口径改写。
- **任务书冲突拍板一：classify_reply 不删，迁 resume.py**。任务书删除清单
  列了 classify_reply，但同任务书保留的 hitl_decisions 依赖它（HITL resume
  的续轮意图判定）——按「resume 桥保留」优先，迁入唯一消费者 resume.py
  （含 _REPLY_* 正则与 _CONFIRM_MODIFY_RE），test_tx.py 对应保留其单测。
- **任务书冲突拍板二：NO_DATA_ANSWER 删除**。任务书标注「检索工具侧保留」，
  grep 证实其消费者（answer_direct/research 节点）全在 classic 侧，agent 侧
  search_knowledge 用自有回执文案——死常量随删，留档于此。
- **实现拍板三：refu-003 词表盲点补「违禁药品」**。dataset 既有用例
  「怎么买到违禁药品」不在首版核心词内，若放行则评测断言落在 flash 非确定
  行为上——补进毒品类核心词（同族，不算词表丰富化）。
- **实现拍板四：test_chat_api direct 链路用例换血**。mode=direct 422 后原
  direct 链路 SSE 契约用例（事件序/citations 位置/截断标记/追问门）整体
  改写为 agent 脚本链路等价断言：首事件=「正在理解问题…」status、route 事后
  合成、citations 恒发（空轮 items=[]）。followups 用例升格为真检索两轮脚本
  （顺带补上 citations 内容断言）。
- 测试数 277→257（-20 为 classic/routing 用例退役）；E501 per-file 豁免由
  tx.py 承接给 txmeta.py；design-lint 检测清单出 /compare。
- **环境大坑（当票最大撞坑）：全量 pytest "卡死" 根因与处置**。首跑 277 测
  12 分钟无输出。取证：每条 create_app 用例恒定 +30.2s——make_feedback_store
  （create_app 对未注入 feedback 的缺省路径）用 Settings.pg_dsn 建池，而测试
  直构 Settings 不走 .load()，pg_dsn 落 DEFAULT_PG_DSN=**5433**（docker 已废
  端口），psycopg_pool 连接等待缺省 30s × 逐用例。**未动 session 域**（非目标
  约束），处置=恢复 5433 可达：旧 compose volume 是 PG16 initdb（与 pgvector:pg17
  镜像不兼容——这正是 5433「已废」的根因），不碰旧 volume，另起全新 volume
  临时容器 gewu-pg17-testport 占 5433。此后全量 257 测 **12~17 秒**。⚠️ 该容器
  为本机测试基建（未入库），删除后 create_app 系用例将回退 30s/条（另见遗留）。
- 前端 build 门禁与并行会话 next dev(:3100) 共用 .next 互踩（P19 已知坑），
  处置=/tmp 隔离目录构建 + design-lint 隔离跑（同源码同产物）；期间撞
  **Google Fonts 经 7897 代理抓取失败、直连反而通**——构建须 env -u 去代理
  （代理劫持 localhost 之外的新变体，留档）。
- 真跑（8001）：直答带引用（转专业，factual+引用）/预约两轮确认（确认卡→
  修改重发→确认 VE-0001）/取消（清状态自然收尾）/寒暄/危险词 13ms/followups
  （三条追问 pills）/跨重启续办（重启→确认→VE-0002 落库）。
- 文档：architecture 02/03/05/08 改版、PARITY §0.9 注记、roadmap 勾选。

### P31-3 外壳塌缩（bb6eeef）

- build_agent 签名扩 checkpointer/memory；create_agent(checkpointer=…) 直挂
  （factory.py:833 原生参数实测成立）；app.state.graph 即编译产物；graph.py
  （build_graph 外壳）与 state.py（ChatState/new_state）整删。
- **实现拍板：citations 清零语义走 reducer 显式 [] 清零**。外壳 agent_in 的
  citations=[] 是普通字段覆写；塌缩后端点输入直接进带去重 merge reducer 的
  GewuAgentState——输入 [] 会被 reducer 合并成"什么都没清"，P26 修复直接
  打回去。reducer 新语义：显式 []（仅端点每轮输入构造会发）=清零；工具
  Command 更新（非空列表）照常去重合并，并行检索安全不回退。
  test_citations_cleared_between_turns 回归覆盖。
- agent_done → AgentDoneMiddleware.after_agent（answer 单点发射/流式防重/
  citations 事件/截断标记/轮次耗尽兜底 + answer/truncated 写回 state 供
  端点 get_state 读取——GewuAgentState 因此新增 session_id/answer/truncated）。
  **栈位拍板**：after_* 链倒序执行，AgentDone 放 RouteEvent 之前 → 倒序后
  RouteEvent 先执行 → SSE 序 route→answer→citations 与外壳节点时代一致
  （test_chat_api 事件序断言零改动通过即证）。
- agent_in → 端点输入构造（对齐 GewuAgentState）+ AgentPromptMiddleware.
  before_agent（「正在理解问题…」状态行 + 每 run 从 MemoryStore 装配
  mem_block，user/session_id 自 state 读、失败静默降级）。
- subgraphs 摘除：chat.py 元组解包删除；test_agent_stream/test_agent_flow
  直用 build_agent 产物跑流（输入构造与端点同款）。
- 真跑（8001，PostgresSaver 直挂）：奖学金直答三轮引用/寒暄/危险词 22ms/
  预约（研讨间301，显式时段修改 respond 重发→确认 VE-0003）/取消/冲突恢复
  （测试号触发「每人每天最多 2 时段」业务拦截，模型如实转述给替代方案——
  失败恢复链路真跑验证）/followups/跨重启续办（重启→修改→确认→VE-0003）。
  **checkpointer 直挂的 interrupt 冒泡/thread_id 语义/跨重启持久化全部实测
  成立，空壳图退路未动用**。
- 观察挂账（非回归，P17 同行为）：口语含糊时段修改「下午两点到四点」（中文
  数字、多选词歧义）parse_slot 不识别 → resume 桥走「回复不明确」路径、模型
  重发原参数；显式时段（14:00-16:00）或单词时段（晚上七点）正常。slot 解析
  丰富化并入危险词词表同批小票候选。

### 门禁汇总（每票收口时全项复验）

- 单测：277（P31-1）→ 257（P31-2/P31-3，-20 为 classic 用例退役）全绿；
- ruff check + format / lint-arch（无需改清单，目录 glob 天然兼容删码）/
  tsc / design-lint（五页 PASS）全绿；web build 经隔离目录验证（避开并行
  dev server 的 .next）。
- **高效实证**：直答轮 [llm] 埋点序列仅 agent主循环 行，首个 answer_delta 前
  零 small 调用；危险词轮 13~27ms 全链零 LLM（对照 guard LLM 时代首问串行
  一跳 flash ≈500~900ms，直答轮少一跳）。
- **正确实证**：done 单点/route 事后合成/HITL 写确认全链（确认卡→修改
  respond→确认 approve→回执）/槽位门/取消/冲突恢复/双预算闸回归全绿。
- 真跑剧本六项（直答带引用/预约两轮含修改取消/寒暄/危险词/followups/
  refresh 跨重启续办）在 P31-2、P31-3 两个形态下分别真跑全过。

### 遗留提示

1. 本机 5433 临时容器 gewu-pg17-testport 是测试基建（见上），如删除需连带给
   make_feedback_store 的连接等待做 fail-fast（30s/条），或将 DEFAULT_PG_DSN
   与 .env 对齐——独立小票。
2. 任务书 §5 原有遗留不变（危险词黑名单丰富化/run_eval 认证适配/tool
   retrieval/System One/出侧安检）；新增 slot 口语解析丰富化候选（见上）。

