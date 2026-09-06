# SERVICES —— 微服务拆分映射与行为锚点

> 微服务升级提示词（[microservices-upgrade-prompt.md](./microservices-upgrade-prompt.md)）第一步产出。
> 依据：冻结单体（当前 `main`，P0 将打 tag `go-monolith`）+ [PARITY.md](./PARITY.md) 逐文件核对。
> 本文档回答：每个 `internal/` 包去哪、每个行为锚点在哪份代码里、迁移时的保真要点与风险。
> **§10 冲突清单的 A~D 四项已由用户于 2026-09-04 拍板（全部采纳推荐方案），结论已回写正文。**

---

## 1. 目标拓扑（六进程 + 三基础设施）

| 进程 | 职责（一句话） | 建议端口 | 存储 | 被谁调用 |
| --- | --- | --- | --- | --- |
| gateway | gin HTTP :8000，对外唯一入口：SSE 透传、限流、CORS、身份派生、`/api/*` 分发、`/admin/*` 转发 | 8000（HTTP） | 无（限流桶进程内，可迁 Redis） | 浏览器 / eval / apps/web |
| orchestrator | 编排管线（PARITY §4）+ 五分类路由 + 工具调度 + `agent_config` 管理 | 9001（gRPC） | PG `agent_config` + Redis 缓存 | gateway |
| conversation | 办理会话状态（§12.3）+ 消息落库（新增，仅审计） | 9002（gRPC） | PG 会话表 / 消息表 | orchestrator |
| generate | LLM 网关：provider 接口 + 模型分层 + **每日 token 预算集中计量** | 9003（gRPC） | PG/Redis（预算） | orchestrator、rag |
| tool | 工具注册 + 权限矩阵 + mock 业务系统（SQLite→PG） | 9004（gRPC） | PG venues/bookings/leave_tickets | orchestrator、gateway（reset/overview） |
| rag | 混合检索 + 摄入流水线（Redis Streams）+ 长期记忆（P5） | 9005（gRPC） | PG chunk 元数据 + Milvus FLAT（向量） | orchestrator、gateway（search/docs/health） |

基础设施：PostgreSQL（业务/会话/配置/chunk 元数据）、Redis（配置缓存/限流可选/预算/Streams MQ）、
Milvus standalone（FLAT，IP 度量；资源受限降级 pgvector——ADR）。

调用链：

```
client ─HTTP/SSE─▶ gateway ─┬─ gRPC ─▶ orchestrator ─┬─▶ conversation（会话状态）
                            │                         ├─▶ generate（路由/拆解/抽槽/意图/选工具/主答案流）
                            │                         ├─▶ rag（检索）
                            │                         └─▶ tool（工具执行/场馆解析）
                            ├─ gRPC ─▶ tool（/api/business/reset|overview 直转）
                            └─ gRPC ─▶ rag（/api/search、/api/docs、health 聚合）
generate ─▶ provider（OpenAI 兼容端点）；rag ─▶ generate（查询改写/embed）
摄入：make ingest（阻塞 CLI）─▶ rag 发布 Redis Streams ─▶ consumer（解析→切分→embed[走 generate]→入库）
```

流式：客户端 SSE ← gateway ← orchestrator（gRPC server-streaming `Chat`）← generate（`ChatStream`）← provider。
取消：客户端断开 → gateway ctx 取消 → orchestrator/gRPC 流取消 → generate ctx 取消 → provider HTTP 请求取消
（README 宣示的「断开不烧 token」，gRPC 全链路 ctx 贯通可实现，P1 验收项）。

---

## 2. `internal/` 包 → 服务归属与复用方式

铁律回顾：`internal/` 冻结只读（改动逐条记录）；`cmd/server` 保持可构建可运行作 A/B 回退。

| 冻结包 | 去向 | 复用方式 | 说明 |
| --- | --- | --- | --- |
| `internal/agent`（pipeline/router/direct/research/events/jsonx/prompts） | orchestrator | **拷贝改造** → `internal/services/orchestrator/` | 依赖注入形态巨变（Business/Retriever/LLM 从进程内对象变 RPC 客户端），无法原样 import；文案/正则/分支/参数逐字拷贝 |
| `internal/agent/session.go` | conversation | **拷贝改造** → `internal/services/conversation/` | TTL 30min 惰性清理语义逐字；存储从进程内 map 换 PG/Redis（有意差异，PARITY-MS） |
| `internal/agent/tools.go` | tool | **拷贝改造** → `internal/services/tool/` | 权限矩阵、`need()` 缺参文案、读工具消息格式逐字；`business.Result` 形态进 proto |
| `internal/agent/transaction.go` | orchestrator | **拷贝改造** | 状态机本体在编排侧；但 `slotMetaTable` 的 Parse 闭包依赖 `d.Business`（parseVenue/normVenueID）→ 改调 tool RPC（见 §6.4） |
| `internal/business` | tool | **拷贝改造** | 业务规则（校验顺序/文案/单号/时区/审批映射/种子）逐字复刻；存储层 SQLite→PG 重写（DDL 见 §6.2） |
| `internal/llm` | generate | **拷贝改造** | 参数复刻（§4.2）；新增 provider 接口/工厂 + 受限重试（ADR）；`internal/llm` 冻结 |
| `internal/budget` | generate + orchestrator | **新实现（参照复刻）** | 计量集中 generate（PG/Redis 原子累计、UTC 滚动）；orchestrator chat 入口经 generate 预检 RPC；文件版冻结不动；429/Ensure 文案逐字 |
| `internal/rag` | rag | **import 共享为主**（待决策项 A） | tokenize/store（BM25/RRF/截断）/ingest 解析切分 直接共享；`retrieve.go`/`rewrite.go` 绑定 `*llm.Client` 需接口化或拷贝（§7.1） |
| `internal/config` | 全部进程 | **import 共享（只读）** | 新增服务端口/DSN/MQ 配置放 `internal/services/<svc>/config.go` 或独立 `internal/svcconfig`，不改冻结库 |
| `internal/dates` | orchestrator、tool | **import 共享** | 纯函数零外部依赖，直接共享（PARITY §11 唯一实现） |
| `internal/middleware` | gateway | **import 共享** | 限流中间件原样（§8.4）；单实例 gateway 下进程内桶与单体语义一致，Redis 迁移为后续可选 |
| `cmd/server` | 保留 | 原样冻结 | A/B 对照与回退入口，零改动 |

预计对冻结库的唯一改动（已决策 A）：`internal/rag` 的 `NewRetriever`/`rewriter` 把 `*llm.Client` 换成
小接口 `interface{ HasKey() bool; Chat(...); Embed(...) }`——行为零变化，改动原因与影响记入 PARITY-MS。
其余全部通过「新代码放 `internal/services/` + import 只读共享」实现。

---

## 3. orchestrator —— 编排、五分类路由、工具调度

### 3.1 编排管线（PARITY §4 逐字，源 `internal/agent/pipeline.go:60-142`）

```
1. 会话处于 collect/confirm（先查 conversation RPC）：
   classify_reply(question)：
     continue → route{transaction,"继续办理："+流程label,by_llm:false} → handle_reply → done
     cancel    → 清会话 → route{transaction,"用户取消办理",false}
                 → answer_delta "好的，已取消本次办理。有别的事随时找我。" → done
     new_topic → 清会话，继续走 2
2. 路由：mode=direct/research 直接采用（reason="用户指定 {mode}"，by_llm=false）；
   否则 route_question（§3.2）。发 route 事件。
3. 分发：refusal→拒答文案+citations[]；factual→AnswerDirect(k=RETRIEVAL_K=6)；
   research→RunResearch(k=5)；hybrid→status"先回答你的政策问题…"→直答→
   status"接下来为你办理业务…"→StartFlow；transaction→StartFlow；
   未知 route→error "未知路由：{route}"
4. done（latency_ms 口径见 §8.3 决策项 B）
异常：BudgetExceeded→error{message}→done；其他→error{"{Go 错误文案}"}→done
```

固定文案逐字（`internal/agent/prompts.go:61-72`）：REFUSAL_ANSWER / NO_DATA_ANSWER（注意 Go 版为
「知识库中暂时没有**找到**与这个问题相关的资料。…」，与 PARITY §4 摘录差「找到」二字，以代码为准逐字迁移）/ DEMO_MODE_NOTE。

### 3.2 五分类路由（`internal/agent/router.go`）

**LLM 判定（有 key）**：模型 = 小模型（`LLM_SMALL_MODEL`，默认 glm-5.3-flash）；
`json_mode`（`response_format:{"type":"json_object"}`）、temperature=0、max_tokens=200、Small=true；
system=`RouterSystem`（prompts.go:9-30 逐字），user=问题原文。
解析 `{"route","reason"}`：route ∈ {factual,research,refusal,transaction,hybrid} 才采用；reason 截 100 rune。
解析失败/调用异常 → 降级启发式（仅调用异常时 log）。

**启发式（无 key / 失败，顺序不可换）**：
1. 办理动词正则 `预约|预订|退订|取消预约|请假|事假|病假|销假|假申请|我的预约|待审批|批准` 命中：
   - 请求词 `帮我|给我|我想|我要|麻烦|想请|想约|想订|帮我查|帮我看` 且 咨询词
     `什么|怎么|为什么|是不是|需不需要|能不能|多少|谁|规定|要求|政策|意思` → **hybrid**，reason「启发式：办理诉求 + 政策咨询」
   - 有请求词 或 无咨询词 → **transaction**，「启发式：业务办理诉求」
   - 仅咨询 → 落入 2
2. rune 长度 >32 或含信号词 `并且|同时|以及|分别|然后|还要|再加上|又想|还能|会不会|能不能|影响` → **research**，「启发式：长问题或含并列/多条件信号」
3. 否则 **factual**，「启发式：短事实型问题」

### 3.3 直答与深研对下游的调用形态

- 直答（`direct.go`）：`Search(question, k=6)`；无命中→NO_DATA+citations[]；无 key→
  `DEMO_MODE_NOTE\n\n` + 前 3 条 `[i] 《title》：text前180字…`（`\n\n` 连接）+ citations（全部命中）；
  有 key→system=AnswerSystem，user=`参考资料：\n\n{编号上下文}\n\n问题：{question}`，
  编号上下文每条 `[i] 《title》（来源：source）\ntext`（完整 chunk，`\n\n` 连接），流式 answer_delta*→citations。
- 深研（`research.go`）：MAX_SUBQUESTIONS=4、每路 k=5、MAX_EVIDENCE=12、单条证据截 600 rune；
  status「正在拆解问题…」→ plan（无 key=[原问题]；有 key=LLM json、temp 0、max_tokens 400、small；
  取非空字符串数组前 4，失败→[原问题]）→ 逐路检索发 `step{index(1起),subquestion,sources:前3命中去重title}`，
  证据池按 chunk_id 去重保到达序 → 无证据→NO_DATA+citations[] →
  status「共检索到 {N} 条证据，正在交叉验证与综合…」→ 证据块 `[n] 《title》（来源：source）\ntext[:600]`，
  **blocks 用 `\n` 连接（与直答的 `\n\n` 不同，勿"修正"）** → 无 key 演示文案
  `DEMO_MODE_NOTE\n\n围绕 {子问题数} 个子问题共检索到 {证据数} 条相关段落，节选：\n\n` + 前 3 块；
  有 key→流式 ANSWER_SYSTEM 同直答。

### 3.4 工具调度与续轮意图

- 工具识别（`transaction.go:26-38`，按序首个命中）：cancel_booking `取消预约|退订` →
  approve_leave `批准|通过.*(请假|申请)` → pending_leaves `待审批|审批.*(请假|申请)|谁.*请了假` →
  leave_status `请假.*(单号|进度|状态|批了没)|LV-\d+` → my_bookings `我的预约|我预约了|我订了` →
  query_venues `(有|哪些|什么|能).*(场馆|场地|研讨间)|场馆.*(有|能|可)` →
  submit_leave `请假|事假|病假|销假|休.*假|请.*天.*假` → book_venue `预约|预订|订.*(馆|场|间)`。
  启发式未中且有 key → LLM 选工具（json、temp 0、**max_tokens=100**、small，system=角色可见工具清单）；
  仍无 → 知识兜底文案「这个问题我理解为你想咨询校园信息，为你转知识库检索：」→ 直答。
- 读工具直答（query_venues/my_bookings/leave_status/pending_leaves + 非 FLOW_DEFS 工具）：
  无会话直接 tools.call → action_result → 成功 answer{message}，失败 `办理未完成：{message|未知错误}。`；
  query_venues 原句解析出日期则带 date 参数。
- 写流程状态机：FLOW_DEFS（book_venue[venue,date,slot|purpose]、submit_leave[leave_type,start_date,end_date,reason]、
  cancel_booking[booking_id]、approve_leave[ticket_id]、leave_status[ticket_id∈FLOW_DEFS 但走读直答]）；
  SLOT_META 的 ask 文案与解析器见 PARITY §9.3 表（逐字，含 slot ask 文案「（也可回复上午/下午/晚上）」——
  注意实现里 上午/下午 映射两个时段不唯一、实际解析不命中，仅 晚上/中午 唯一命中：**保留现状，勿改**）。
- collect：有 key=LLM 抽槽（SlotExtractSystem，user 消息含今天 ISO/工具/字段定义 JSON/已收集 JSON/用户消息，
  json、temp 0、max_tokens 300、small），抽出值过 `_normalize`（purpose/reason/booking_id/ticket_id 取 strip
  原文，其余过确定性解析器，解析不出丢弃），只填未收集槽；无 key=上轮追问槽位定向解析 / 首轮机会性抽取
  （结构化槽：venue/slot/leave_type；book_venue 的 date；submit_leave 的 start/end 用 ParseAll 首末；
  自由文本槽不猜）。随后统一 `_apply_days_phrase`（submit_leave 有 start 无 end 且文本含
  `([一二三四五六七八九]|\d+)\s*天` → end=start+(n-1) 天）。缺槽追问第一个缺失槽：
  `slot_question{slot,ask}` + `answer_delta{ask}`，记 last_asked。齐全→confirm。
- confirm 摘要：args=有序中文标签表（FLOW_DEFS 顺序），submit_leave 追加 `共:"{days} 天"`、
  `审批:"{approver}（按学校规定）"`，病假且 days>3 加 note「病假超过 3 天建议附医院证明。」；
  book_venue 的「场馆」值显示场馆名（非 ID）。事件 `pending_action{tool,label,args}` + answer_delta
  `请确认{label}信息——{k1}：{v1}；…。{note}回复「确认」提交，或直接告诉我需要修改的地方。`
  （**args 插入序是展示契约，proto 需用 repeated KV 而非 Struct，见 §8.2**）。
- confirm 回复：先遍历 SLOT_META（跳过 purpose/reason）尝试修改（必填或已收集且解析出**不同值**）→
  有修改则 status「已更新，请重新确认：」+ 重发摘要；确认词 `确认|确定|好的|可以|提交|是的|对`→执行；
  取消词 `取消|算了|不办|不要`→清会话+取消文案；其他→「没太听懂——请回复「确认」提交，或「取消」放弃，
  也可以直接告诉我需要修改的日期、时段等信息。」
- 执行与恢复：成功→清会话+`action_result{success:true}`+`办理成功：{message}（凭证号：{receipt}）`
  （无 receipt 省略括号段）；失败含 field 且在 SLOT_META→回 collect、删该槽、last_asked=field、
  `action_result{success:false}` + `slot_question{field, ask+("可选时段：a、b" 若有 alternatives)}` +
  `answer_delta "{message|执行失败}。{question}"`；其他失败→清会话+`办理未完成：{message|未知错误}。如需继续请重新发起。`
- 续轮意图 classify_reply：有 key=LLM（json、temp 0、**max_tokens=60**、small，system 含阶段/已收集 JSON/
  三选一说明）；失败或无 key 启发式按序：取消词→cancel；确认词→continue；疑问词
  `什么|怎么|为什么|几点|哪|谁|吗`→new_topic；last_asked 可解析→continue；已收集结构化槽可解析→continue；
  ≤12 rune 且无中英文问号→continue；否则 new_topic。

### 3.5 `agent_config` 表（新增）

- 字段：系统提示词（router/planner/slot_extract/answer 四条）、工具集（JSON）、模型参数（模型名/温度/max_tokens 分层）。
- **默认行 = `internal/agent/prompts.go` 四个提示词逐字快照**（含 §3.1 固定文案可继续硬编码或入库，P2 定）。
- 读走 Redis 缓存（写时失效），热路径零配置 RPC。`/admin/*`（gateway 转发）提供查看/修改——
  新增命名空间，不触碰既有 `/api/*` 契约。

---

## 4. generate —— LLM 网关与预算计量

### 4.1 provider 接口

- 同步 `Chat(ctx, messages, options) (string, error)`、流式
  `ChatStream(ctx, messages, options, onDelta) error`、`Embed(ctx, texts)`；取消走 ctx。
- OpenAI 兼容协议复刻（`internal/llm/client.go`）：`{LLM_BASE_URL}/chat/completions`、`/embeddings`
  （base 以 `/` 结尾拼接，TrimRight 兼容无斜杠）；`Authorization: Bearer`；json_mode →
  `response_format:{"type":"json_object"}`；`LLM_DISABLE_THINKING=true` → 追加 `thinking:{"type":"disabled"}`
  （含流式）；`max_tokens` 缺省 2048（零值必须显式补）；请求超时 10 分钟（历史基线出现 200s 级端点抖动，
  不能更短）；流式手工解析 SSE `data:` 行、`[DONE]` 干净终止、无法解析的行静默跳过（端点心跳兼容）。
- 模型分层：small=true（路由/拆解/抽槽/改写/意图/选工具）→ `LLM_SMALL_MODEL`；主答案与 embedding →
  `LLM_MODEL`/`EMBED_MODEL`。**流式永远主模型**。
- 无 key：`HasKey()=false`，一切调用前置检查，直接调用返回错误「未配置 LLM_API_KEY，无法调用模型」。
- 超时/网络错误向上抛，**降级决策留在调用方**（orchestrator/rag），与现状一致。
- 重试（新增，ADR 备案）：仅「连接失败且请求未发出」重试 1 次；流式不自动重试。

### 4.2 全部 LLM 调用点参数矩阵（迁移保真清单）

| 调用点 | 模型 | json_mode | temp | max_tokens | 流式 |
| --- | --- | --- | --- | --- | --- |
| 五分类路由 | small | ✓ | 0 | 200 | 否 |
| 深研拆解 plan | small | ✓ | 0 | 400 | 否 |
| 槽位抽取 | small | ✓ | 0 | 300 | 否 |
| 续轮意图 | small | ✓ | 0 | 60 | 否 |
| LLM 选工具 | small | ✓ | 0 | 100 | 否 |
| 查询改写（rag 内） | small | ✗ | 0 | 80 | 否 |
| 直答/深研主答案 | 主模型 | ✗ | **0（决策 C：以冻结代码为准）** | 2048 | ✓ |
| embedding | EMBED_MODEL | — | — | — | 否 |

### 4.3 预算（集中计量，PARITY §12.2 语义不变）

- 周期：**UTC 日期**滚动；持久化从 `{DATA_DIR}/usage.json` 迁 PG/Redis，**原子累计**（多调用方并发）。
- 预检两级：orchestrator 在 `/api/chat` 入口预检（耗尽 → HTTP 429
  `{"detail":"今日 token 预算已用尽（上限 2000000），请明天再试"}`，文案由 generate 返回、gateway 透传）；
  generate 在**每次** Chat/ChatStream/Embed 调用前内部预检（耗尽 → 特定错误码回传调用方，
  调用方按现状语义处理：路由/改写等辅助调用失败→各自降级；主答案流失败→整轮 error 事件+done。
  这条错误路径的事件文案 = BudgetExceeded 中文消息，须与单体一致）。
- 计量口径逐字：非流式按 `usage.total_tokens`；流式按产出字符数（rune）/2 下限 1，**中途断开也入账**
  （go-notes §10-4 的修复不能丢）；embedding 按 `usage.total_tokens`，缺省按输入文本 rune 总和/2。
- `/api/health` 的 `budget:{used,limit}` 由 gateway 向 generate 查询聚合。

---

## 5. conversation —— 会话状态与消息

- 状态语义 PARITY §12.3 逐字（源 `internal/agent/session.go`）：session_id →
  {role,user,phase(idle|collect|confirm),tool,slots,last_asked,updated}；TTL 30 分钟 get/ensure 惰性清理；
  ensure 刷新 role/user 与时间戳；clear 移除。
- RPC：Get / Ensure / Save（phase/slots/last_asked 变更落库）/ Clear；orchestrator 每轮 chat 先 Get。
- 消息落库（新增）：role/question/事件摘要/时间，仅审计与前端展示，**不参与答案生成**。
- 上下文组装 API 预留不接入（各轮独立作答是 26/26 的前提；接入与否迁移完单独评估，ADR）。
- 有意差异：服务重启后会话保留（单体进程内 map 重启即失）→ PARITY-MS。

---

## 6. tool —— 工具中心 + mock 业务系统

### 6.1 权限矩阵与文案（PARITY §10 逐字）

| 工具 | label | 角色可见 | 写 |
| --- | --- | --- | --- |
| query_venues | 查询场馆 | student,counselor | 否 |
| my_bookings | 我的预约 | student,counselor | 否 |
| leave_status | 请假单查询 | student,counselor | 否 |
| pending_leaves | 待审批请假 | **仅 counselor** | 否 |
| book_venue | 预约场馆 | student,counselor | 是 |
| cancel_booking | 取消预约 | student,counselor | 是 |
| submit_leave | 请假申请 | student,counselor | 是 |
| approve_leave | 批准请假 | **仅 counselor** | 是 |

返回文案：未知工具 `未知工具：{name}`（error=unknown_tool）；越权
`当前身份（学生|辅导员）无权执行「{label}」`（error=permission）；缺参 `缺少参数：{字段名}`（error=missing_arg）。
`tool_descriptions(role)` 只列该角色工具：`- {name}：{description}` 换行连接（给 LLM 的清单，顺序=表序）。
**敏感参数 `user` 由服务端从身份注入**（gRPC 元数据携带 role/user，调用方不可传）——现状 chat 请求体
本无 user 字段、RunChat 服务端派生 `demo-{role}`，语义已满足，迁移保持。

### 6.2 表结构（SQLite→PG，语义逐字）

- venues(id TEXT PK, name, kind, capacity INT)
- bookings(id SERIAL PK, venue_id, date 'YYYY-MM-DD', slot, purpose DEFAULT '', user,
  status DEFAULT '有效'（'有效'|'已取消'）, created_at 中国时区 ISO 秒精度 `2006-01-02T15:04:05+08:00`)
- leave_tickets(id SERIAL PK, user, leave_type, start_date, end_date, days INT, reason,
  approver_level, status DEFAULT '待审批'（'待审批'|'已通过'）, created_at 同上)
- 种子（venues 空表时按序插入）：venue-badminton 羽毛馆 体育场馆 2；venue-basketball 篮球场 体育场馆 1；
  venue-room301 研讨间301 图书馆研讨间 1；venue-room302 研讨间302 图书馆研讨间 1。
- SLOTS：`08:00-10:00,10:00-12:00,14:00-16:00,16:00-18:00,19:00-21:00`（固定序，alternatives 输出稳定靠它）。
- `approver_of(days)`：≤3 辅导员；≤7 学院；>7 教务处。
- 单号：`VE-{id:04d}` / `LV-{id:04d}`（`%04d`）；查单取数字部分拼接解析（`VE-0003`→3，无数字→-1 查不到）。
- **序列语义备案**：SQLite AUTOINCREMENT 与 PG SERIAL 在 DELETE 后都不复位——reset 后新单号继续增长，
  两侧一致；评测断言不含单号字面值（已核对 dataset：仅 venue_contains/slot/date_text/days/approver/bookings_count），无回归风险。

### 6.3 每工具校验顺序与错误文案（PARITY §8 逐字，源 `internal/business/service.go`）

- **book_venue(venue_id,date,slot,purpose,user)**：①场馆不存在→`{invalid,"场馆不存在"}`（无 field）；
  ②slot∉SLOTS→`{invalid,field:slot,"时段不合法"}`；③date<今天(CN)→`{invalid,field:date,"不能预约过去的日期"}`；
  ④该 user 当天有效预约≥2→`{quota,"每人每天最多预约 2 个时段"}`；⑤余量=capacity−该(场馆,日期,时段)有效数≤0→
  `{conflict,field:slot,"{场馆名} {date} 的 {slot} 已约满",alternatives:[按 SLOTS 序有余量时段]}`；
  ⑥成功→`{ok,receipt:"VE-XXXX","已预约 {场馆名} {date} {slot}"}`。
- **cancel_booking(booking_id,user)**：不存在或非"有效"→`{not_found,"预约记录不存在或已取消"}`；
  非本人→`{permission,"只能取消本人的预约"}`；成功→`{ok,"预约 {原单号文本} 已取消"}`。
- **my_bookings(user)**：有效预约按 date,slot 排序→`[{booking_id,venue,date,slot,purpose}]`。
- **remaining(venue_id,date)**：`{slot:余量}`（余量<0 记 0）；场馆不存在返回 `{}`。
- **submit_leave(user,leave_type,start,end,reason)**：days=end−start+1；非法或 end<start→
  `{invalid,field:end_date,"结束日期不能早于开始日期"}`；start<今天→`{invalid,field:start_date,"开始日期不能是过去"}`；
  成功→`{ok,receipt:"LV-XXXX",days,approver,"请假申请已提交（{days} 天），按学校规定将由{approver}审批"}`。
- **leave_status(ticket_id,user)**：不存在→`{not_found,"请假单不存在"}`；非本人→`{permission,"只能查询本人的请假单"}`；
  成功→`{ok,ticket,leave_type,start,end,days,approver,status}`（Go 版含可读 message 摘要，PARITY §18.6 已备案）。
- **pending_leaves**："待审批"按 id 升序→`[{ticket,user,leave_type,start,end,days,approver}]`。
- **approve_leave(ticket_id)**：不存在→not_found；已处理→`{invalid,"该请假单已处理"}`；成功→`{ok,"请假单 {原单号文本} 已通过"}`。
- **reset**：DELETE 全部 bookings 与 leave_tickets（场馆保留）→`{"status":"ok"}`。
- **overview**：`{bookings:[{booking_id,venue,date,slot,user}](有效按 id)},{tickets:[全部按 id,含 status]}`。
- 读工具的对外消息格式（编排层拼装，tool 返回数据）：query_venues
  `{date(缺省今天)} 可预约场馆：\n- {name}（{kind}，每时段 {capacity} 组）：{有余量时段顿号连接|（今日已约满）}`；
  my_bookings 空`你目前没有有效预约。`/非空`你的有效预约：\n- VE-XXXX：{venue} {date} {slot}`；
  pending_leaves 空`当前没有待审批的请假申请。`/非空`待审批请假申请：\n- LV-XXXX：{user} {leave_type} {start}~{end}（{days} 天，{approver}审批）`。

### 6.4 对编排侧的支撑 RPC

槽位解析器依赖业务数据：`parseVenue`（名称子串匹配→venue_id）、`normVenueID`（venue_id→场馆名，
确认摘要展示）。tool 服务暴露 VenueByName / ListVenues（或并入统一 gRPC 服务），orchestrator 调用。
频率低（每办理轮 1~2 次），可接受 RPC 开销。

---

## 7. rag —— 检索、摄入、记忆

### 7.1 检索实现复用与 LLM 依赖（决策项 A）

- **直接 import 共享**：`tokenize.go`（分词）、`store.go` 的 `BM25Search`/`RRFFuse`/`ChunkRows`/`DocMetaMap`/
  `ListDocs`/`GetStats`、`ingest.go` 的 `ParseDoc`/`ChunkText`/分批逻辑——「行为对齐靠同一份代码」。
- **接口化（决策 A）**：`retrieve.go` 的 `Retriever` 与 `rewrite.go` 的 `rewriter` 构造参数从 `*llm.Client`
  改为小接口 `interface{ HasKey() bool; Chat(...); Embed(...) }`——冻结库唯一改动、行为零变化；
  rag 服务用实现该接口的 generate RPC 客户端注入，检索编排仍是同一份代码。

### 7.2 检索管线参数（PARITY §7.5~7.7 逐字）

1. 查询改写 `expand`：有 key → LLM（small、temp 0、max_tokens 80、非 json、system=改写提示词逐字）把口语
   改写为政策词串，结果=`"{原查询} {改写}"`（中间一个空格）；失败或无 key → 原查询；**进程内缓存**
   （map+RWMutex，不迁 Redis——单 rag 实例内语义等价）。
2. BM25 取 **k\*2** 条：分词 = ASCII `[a-zA-Z0-9]+` 整词小写 + 汉字相邻**字符二元语法**（先收集拉丁词再拼
   汉字二元组）；k1=1.5、b=0.75、idf=`ln(1+(N−df+0.5)/(df+0.5))`；tf=token 在 chunk 内计数；查询侧 token
   去重后累计；doc_len 下限 1；**平局按 chunk id 升序**（Go 构造性保证）；索引懒构建、写入后失效重建。
3. 向量路：库中有向量 **且** 有 key → embed 查询 → 余弦取 k\*2；异常 → 本查询降级纯 BM25（log warning）。
4. 融合：有向量结果 → `RRFFuse([bm_ids,vec_ids], k=60)` 取前 k（平局按首次出现序）；否则 `bm_ids[:k]`。
5. 组装 Hit{chunk_id,doc_id,seq,text,title,source}（doc 元信息缺失 title=doc_id、source=""）。
- 向量实现迁移：暴力余弦（float32、L2 归一化、维度不一致跳过、平局按 id）→ **Milvus FLAT + IP 度量**
  （入库前 float64 L2 归一化→float32，与「归一化后 IP=余弦」等价）。**平局次序风险**：Milvus 并列分数的
  返回序不保证按 chunk_id——rag 服务需对召回结果按 `(score, chunk_id)` 重排稳定化，否则 P3「逐位一致」
  验收在极端平局下会翻车（ADR 记录）。pgvector 降级实现同理。
- `/api/search` 响应：`[{doc_id,title,source,seq,text}]`，text 截 300 rune；query 1~200、k 1~20（缺省 5），
  越界 422 `{"detail":"…"}`（文案见 §8.4）。

### 7.3 摄入流水线（PARITY §15 语义 + 异步化）

- 数据流：corpus/\*.md → frontmatter 解析（`---\n…\n---\n`，key 小写，缺省 title=文件名去扩展/source=钱塘大学/
  updated=""）→ 分块（空行分段、段聚合 ≤450 rune、超长单段 450 硬切保留末 80 重叠）→（可选）向量化
  （批 32、L2 归一化）→ PG chunk 元数据 + Milvus 向量。
- **`make ingest` 阻塞语义**：CLI 发布 Redis Streams（consumer group）后等待完成回执，超时报错——
  评测与 A/B 需要确定性索引状态。异步上传 API 为新增演示面。
- embed 失败一次 → 本批全程降级仅 BM25（不再重试）——PARITY §15 降级语义保留（失败判定在 rag 侧，
  embed 调用走 generate，错误透传即触发）。
- 幂等：同 doc_id 重导先删旧 chunks+vectors 再插；`--no-embed`/`--rebuild` 语义保留。
- **向量重建注意**：现状 data/index.db 为 15 篇/22 chunk/0 向量（仅 BM25）。P3 验收的「带向量」轮需有 key
  重新摄入；两轮 A/B 必须同索引状态对照。

### 7.4 长期记忆（P5）

knowledge/memory 两组 API（检索/摄入、Recall/Put），共用 embedding 管线（走 generate），collection 分离。
**Recall 结果不接入答案生成**——26 题行为不变；接入与否迁移后单议（ADR）。

---

## 8. gateway —— HTTP/SSE 契约保真

### 8.1 路由与转发

| 路径 | 后端 | 备注 |
| --- | --- | --- |
| GET /api/health | 聚合 rag（docs/chunks/embeddings）+ generate（llm/budget） | 字段与形态见下；聚合失败 → HTTP 503 `{"detail":"<服务名> 不可达"}`（决策 D） |
| GET /api/docs | rag | `[{doc_id,title,source,updated,chunks}]` 按 doc_id 升序，空为 `[]` 非 null |
| POST /api/search | rag | 校验+截断见 §7.2 |
| POST /api/chat | orchestrator（gRPC server-streaming） | SSE，见 §8.2 |
| POST /api/business/reset | tool | `{"status":"ok"}` / 500 `{"detail":…}` |
| GET /api/business/overview | tool | `{"bookings":[],"tickets":[]}` |
| /admin/*（新增） | orchestrator | 配置管理命名空间，不触碰 /api/* |

health 形态（PARITY §2.1）：`{status:"ok",version:"0.1.0",llm,embeddings,docs,chunks,budget:{used,limit}}`。

### 8.2 SSE 帧编码（P1 golden 验收）

- 每事件 `data: {JSON}\n\n`；JSON **不转义 HTML 字符且保留 UTF-8 原文**（等价
  `json.dumps(ensure_ascii=False)`：`SetEscapeHTML(false)` + Encoder，注意剥掉 Encoder 追加的换行）；
- 响应头 `Content-Type: text/event-stream` + `Cache-Control: no-cache` + `X-Accel-Buffering: no`，
  状态 200 在首事件前下发；逐事件立即 flush；
- 事件类型与字段（PARITY §3 全表）：route/route,reason,by_llm；status/text；step/index(1起),subquestion,sources(≤3)；
  answer_delta/text；citations/items[{n,doc_id,title,source}]（空也要 `[]`）；slot_question/slot,question；
  pending_action/tool,label,args；action_result/tool,success,message,receipt(null|str)；error/message；
  done/latency_ms(int)。
- **args 有序性**：pending_action.args 是插入序中文标签表（§3.4），proto 用 `repeated KeyValuePair`
  固化，gateway 按序输出 JSON 键——`google.protobuf.Struct`（map 无序）会破坏契约。
- golden 用例基础：现 `cmd/server/main_test.go` 的 `TestMarshalNoEscapeKeepsUTF8` / `sseEvents` helper
  可提为跨服务 golden。

### 8.3 chat 校验顺序与错误文案（逐字，源 `cmd/server/main.go:220-261`）

请求体不合法 JSON→422「请求体不是合法 JSON」；question 空→「问题不能为空」；超 MAX_QUESTION_CHARS(500)→
「问题过长」；mode∉{auto,direct,research}（缺省 auto）→「mode 必须为 auto/direct/research」；
role∉{student,counselor}（缺省 student）→「role 必须为 student/counselor」；session_id 缺省 "default"、
>64 字符→「session_id 过长（上限 64 字符）」。全部 422 形态 `{"detail":"<中文>"}`。
预算耗尽→429 `{"detail":"今日 token 预算已用尽（上限 2000000），请明天再试"}`（预检在 orchestrator）。
**done.latency_ms 口径（决策 B）**：t0 保持在 orchestrator 编排入口，与单体 RunChat 逐字一致，
RPC 往返不计入——旧基线报告可直接对照。

### 8.4 限流与 CORS（PARITY §12.1 逐字）

- 每 IP 令牌桶：容量=速率=RATE_LIMIT_PER_MINUTE（默认 20），线性回充 rate/60 每秒；
  IP 取 `X-Forwarded-For` 首段（去空白）否则 TCP 对端；不足→429
  `{"detail":"请求过于频繁，请 {N} 秒后重试"}` + `Retry-After: {N}`，N=`int((1-tokens)*60/rate)+1`；
  放行附 `X-RateLimit-Limit-Minute: {int(rate)}`；**中间件顺序：限流最前，再 CORS**；桶进程内不持久化。
- CORS 任意 Origin/Method/Header，OPTIONS 204。
- 评测提示：全量 26 题 ≈ 41 个 HTTP 请求，会撞 20/min 限流——评测/冒烟轮须以高
  RATE_LIMIT_PER_MINUTE 启动 gateway（对照报告沿用 10000 惯例，见 §9）。

---

## 9. 评测与基线对照（PARITY §17）

### 9.1 运行方式与指标口径

- `python3 eval/run_eval.py [--type …] [--limit N]`，`BASE_URL` 缺省 `http://127.0.0.1:8000`（指向 gateway，
  客户端零改动成立——只依赖 HTTP/SSE 契约）；第一步 GET /api/health（失败即中止）。
- 单轮（factual/multi_hop/refusal）：解析 SSE 聚合 routes/answer/cited/latency；评分=关键词命中（any，忽略
  空白差异）+引用召回（expected_docs∩cited 非空）；refusal=routes 含 refusal 或答案含「只能回答」。
- 多轮（transaction/hybrid）：每例前 POST /api/business/reset，断言 GET /api/business/overview +
  事件聚合（asked_slot/pending_tools/results/denied/conflict_recovered/bookings_count…）；
  `date_text` 期望由**客户端内置**确定性日期解析换算（与服务同规则独立实现）。
- 数据集：26 题 = factual 7 / multi_hop 8 / refusal 3 / transaction 7 / hybrid 1（eval/dataset.jsonl）。
- 零 key 模式：同客户端可跑——factual 部分通过（演示文案）、refusal 不拒答（启发式无此能力）、办理全过。

### 9.2 三份基线报告的对照条件（新报告沿用此格式：条件、口径、逐题明细）

| 报告 | 实现 | 时间 | 关键条件 |
| --- | --- | --- | --- |
| [report-20260828-1035.md](../eval/reports/report-20260828-1035.md) | Go 单体（HTTP/SSE） | 2026-08-28 10:35 | glm-5.3 + flash；26/26 首次全绿 |
| [baseline-python.md](../eval/reports/baseline-python.md) | Python `python-final` 进程内直跑 | 2026-09-03 09:14 | 同机；**仅 BM25 索引（15 篇/22 chunk，no-embed）**；无限流（进程内） |
| [rewrite-go-vs-python.md](../eval/reports/rewrite-go-vs-python.md) | Go 单体（HTTP/SSE） | 2026-09-03 10:02 | 与 Python 轮同机同日同模型**同一 index.db**；`RATE_LIMIT_PER_MINUTE=10000`；26/26 逐题一致 |

微服务各阶段验收的对照口径：与**冻结单体**同机、同模型、同索引状态（BM25-only 与带向量各一轮）、
gateway 同样调高限流；P4 要求逐题通过情况一致（延迟不比）；报告入 eval/reports/。

---

## 10. 冲突与决策清单（A~D 已于 2026-09-04 由用户拍板，全部采纳推荐方案）

### 已决策

- **A. `internal/rag` 的 LLM 依赖复用方式 → 接口化改造**：把 `NewRetriever`/`rewriter` 的 `*llm.Client`
  参数改为小接口 `interface{ HasKey() bool; Chat(...); Embed(...) }`——冻结库唯一改动、行为零变化、
  检索编排仍是同一份代码；rag 服务注入实现该接口的 generate RPC 客户端。改动记入 PARITY-MS。
- **B. `done.latency_ms` 口径 → t0 在 orchestrator**：与单体 RunChat 逐字一致，RPC 往返不计入，
  基线报告可直接对照。
- **C. 主答案 temperature → 以代码为准 temp=0**：PARITY §13 的「默认 0.2」为 Python 时代缺省，
  冻结 Go 代码直答/深研传零值 Options → 实际 temp=0，26/26 基线即此行为；微服务按 0 复刻，
  PARITY §13 措辞已同步修订（并补 §18-7 备案）。
- **D. `/api/health` 聚合失败 → HTTP 503 `{"detail":"<服务名> 不可达"}`**：诚实失败，评测/冒烟立刻暴露
  故障，不被零值字段掩盖。

### 知悉项（无需改行为，备案即可）

1. PARITY §2.4「服务内部用户标识 demo-{role}（评测直调时为 eval-user）」——`eval-user` 是 Python 时代
   进程内直跑的遗留描述；现状评测走 HTTP、请求体无 user 字段，全部为服务端派生 `demo-{role}`。建议迁移时
   顺手修订 PARITY 措辞（文档级，不改行为）。
2. `pending_action.args` 有序性必须用 proto `repeated KV` 固化（§8.2），`google.protobuf.Struct` 不可用。
3. Milvus FLAT 平局次序需 rag 服务端 `(score, chunk_id)` 稳定化重排（§7.2），ADR 记录。
4. 零 key CI 冒烟跑全量 26 题会触发 20/min 限流：冒烟/评测轮 gateway 以高 RATE_LIMIT_PER_MINUTE 启动
   （沿用对照报告 10000 惯例），或冒烟只跑办理子集 + `/api/search` A/B。
5. PARITY §4 的 NO_DATA_ANSWER 摘录与代码差「找到」二字，以代码为准逐字迁移（本文件 §3.1 已按代码记录）。
6. Makefile `API_DIR := apps/api` 为遗留变量（实际入口 `cmd/server`），P0 重写 Makefile 时清理。
7. `.env.example` 的 LLM_BASE_URL 示例是智谱编码套餐端点，与 config.Default() 的标准端点不同——
   示例值 vs 代码缺省本就允许并存，扩充服务配置时保留现状写法。
8. 现状 docker-compose.yml + docker/Dockerfile.{api,web} 为单体双服务结构，P0 重写为六服务 + pg/redis/milvus
   （apps/web 与其 Dockerfile 保留）。
9. 深研证据 blocks 用 `\n` 连接、直答用 `\n\n`（代码事实，PARITY 未细述）——迁移保持原样，勿"统一"。

---

## 11. 有意差异预告（PARITY-MS 种子，迁移中逐条落档）

| # | 差异 | 理由 | 规格出处 |
| --- | --- | --- | --- |
| 1 | 会话状态服务重启后保留（单体进程内丢失） | conversation 持久化 | P2 验收明示 |
| 2 | 摄入异步化（Redis Streams）+ 异步上传 API；CLI 保持阻塞 | 流水线解耦 | 技术栈约定 |
| 3 | 业务库 SQLite→PG；内部错误文案（internal 错误的 err.Error()）可能含 PG 驱动文案 | 存储迁移 | ADR |
| 4 | 向量存储暴力余弦→Milvus FLAT（IP+归一化，等价余弦）；pgvector 降级 | 基础设施升级 | ADR |
| 5 | generate 重试：仅连接失败且请求未发出重试 1 次，流式不重试 | 新增策略 | ADR |
| 6 | 预算持久化 usage.json→PG/Redis，分布式原子累计 | 计量集中 | ADR |
| 7 | 新增 `/admin/*` 命名空间与 `agent_config` 管理 | 配置化 | 架构规格 |
| 8 | conversation 消息落库（审计） | 新增能力 | 架构规格 |
| 9 | 内部异常的 error 事件文案为 Go/gRPC 错误字符串（Python 时代已是「类名:消息」级差异，PARITY §18.2 备案过） | 实现语言 | 沿用 §18.2 |
| 10 | 观测升级 zap + trace-id（仅内部日志，不影响事件契约） | 工程化 | 技术栈约定 |

---

## 12. 评测未覆盖路径与风险标注（迁移重点自检区）

| 路径 | 现状行为 | 26 题是否覆盖 | 迁移风险点 |
| --- | --- | --- | --- |
| 422 校验文案（chat/search 全部分支） | §8.3/§7.2 中文文案 | ✗ | gateway 重写时逐条拷贝；单测固化（现 main_test.go TestSearchValidation/TestChatValidationAndRefusalShape 可搬） |
| 预算 429 与中途耗尽 error 事件 | §4.3 | ✗ | generate 预检错误码→orchestrator 降级分支→error 文案链路需专门测试 |
| 限流 429 + Retry-After + 头 | §8.4 | ✗（评测调高限流绕开） | gateway 直接复用 internal/middleware；单测已有 |
| 流式中途断开取消 + 已产出部分入账 | llm client | ✗ | gRPC 全链路 ctx 取消 + generate 入账时序；P1 验收项（日志与预算增量佐证） |
| refusal 零 key 不拒答 | §16 | 部分（有 key 轮覆盖） | 启发式路由拷贝保真即可 |
| leave_status 读工具可读摘要 | PARITY §18.6 | ✗ | 评测集无该题；tool 迁移按 Go 版行为 |
| 深研 plan/子问题上限/证据去重顺序 | §3.3 | ✓（间接） | 编排拷贝时勿动常量与顺序 |
| 确认摘要 args 插入序 | §3.4 | ✓（tx 用例） | proto repeated KV；见 §10-知悉 2 |
| 会话 TTL 30min 惰性清理 | §12.3 | ✗（评测无 30min 间隔用例） | conversation 实现单测固化 |
| 摄入幂等（同 doc_id 重导） | §7.3 | ✗ | PG+Milvus 双写幂等；P3 A/B 隐式覆盖 |
| embed 失败整批降级 BM25 | §7.3 | ✗ | 失败判定从「进程内 err」变「generate RPC err」，语义等价但需测试 |
| Milvus 平局次序 | §7.2 | ✗ | 稳定化重排；P3 逐位一致验收兜底 |

---

*下一步：进入 P0（打 tag `go-monolith`、目录/proto+buf/docker-compose 六服务 + pg/redis/milvus、Makefile、CI 扩展、trace-id 基座），按 §10 决策执行。*
