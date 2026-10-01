# PARITY —— 格物 API 行为规格（跨实现迁移的唯一行为契约）

> 本文档最初由 v1 Python(FastAPI) 实现（`tag: python-final`）逐文件提取，作为 Go
> 重构的行为唯一标准（Go 终态 `tag: go-final`）；P14 起 LangGraph 版（apps/server）
> 第三次以本文档为唯一契约——SSE 事件字段、错误文案、降级分支逐条对齐，评测脚本
> 与 web 前端跨实现复用。标注【差异决定】的条目是框架隐式行为的等价决定（历史
> 措辞保留）。

## 0. P17 超出 Go 终态的编排演进注记

P17 起 `mode=auto`（默认）切换为 agent-first 单循环（LangChain `create_agent`+middleware），
SSE 十类事件形状不变但语义扩展：① mode 枚举扩 `classic`（cascade 分支图整体降级为实验
基线，行为零改动）；② route 事件两段式（guard 出口 provisional + 收尾 effective 补发，
新增取值 `chitchat`——前端 labels 覆盖更新、评测 set 聚合兼容）；③ `react` 与 `auto` 同路
（历史评测语义别名）；④ 写确认门从 tx_gate(interrupt 文本 resume) 平移为 HITL middleware
（resume 值翻译为 decisions，`resume.py` 收口，前端零感知）。字段级契约仍以本文为准；
classic 链路以下原文继续有效（mode=classic 时的行为规格）。

## 0.5 P21 认证契约（用户体系）

P21 起引入用户体系（邀请码封闭注册·内测），以下条目**修订**本文相应原文：

- **CORS**：`allow_origins=["*"]` 收紧为 `CORS_ORIGINS` 白名单（默认空=仅同源），
  并开 `allow_credentials`；前端改为经 next rewrites 同源代理访问（`/api/:path*`
  → `127.0.0.1:8000`），SSE 流式透传已验证（首事件 0.17s、无整段缓冲）。
- **新增认证四端点**：`POST /api/auth/register`（422 格式/长度、400 邀请码无效
  或用尽、409 邮箱已注册；成功即登录）、`POST /api/auth/login`（失败统一 401
  「邮箱或密码错误」）、`POST /api/auth/logout`、`GET /api/auth/me`。会话凭证
  =httpOnly cookie `gewu_session`（服务端 auth_sessions 表存 token sha256，
  30d 滑动续期；`COOKIE_SECURE` 控 Secure）。
- **role 参数废弃**：`POST /api/chat` 请求体 `role` 不再解析（服务端
  `users.role` 唯一权威；携带该字段不报错、不生效）；未登录 401
  `{"detail":"未登录或会话已过期"}`。`user_id` 由 `demo-{role}` 改为登录 email
  （记忆/台账真实归属）。
- **范围收紧**：`/api/search` 需登录；`/api/business/overview` 登录者本人视图
  （响应新增 `scope:"mine"|"all"`），admin 可 `?all=1`；`/api/business/reset`
  仅 admin（非 admin 403）。`/api/health`、`/api/docs` 保持公开。
- **存储注记**：business/memory 自 SQLite 迁 PG（P21-2，闭 P13 遗留），对外
  行为零变化；单号 `VE-XXXX/LV-XXXX` 形态不变。评测 harness 进程内直调编排层，
  不受 HTTP 认证影响。

## 0.6 P22 会话与记忆契约（会话资源化）

P22 起会话从「客户端自报的裸 uuid」升格为服务端资源，以下条目**修订**本文相应原文：

- **新增会话五端点**（均需登录，本人视角；越权/不存在统一 404 防枚举）：
  `POST /api/sessions`（可带 `{kind}` 缺省 chat，kind ∈ chat|compare →
  `{session_id,title,kind,created_at,updated_at}`）、`GET /api/sessions?kind=`
  （本人列表，updated_at 倒序）、`PATCH /api/sessions/{id}`（改名，title 1~60 字）、
  `DELETE /api/sessions/{id}`（三处连带：checkpointer thread + memory_episodic +
  chat_sessions 行；顺序先 cp 后业务行，悬空检查点无害）、
  `GET /api/sessions/{id}/messages`（历史恢复：checkpointer state 的 messages
  对话级提取——工具调用轮/ToolMessage/system 跳过；classic 链路不写 messages，
  提取为空时退 memory_episodic 兜底）。
- **chat session 语义变更（破坏性）**：`POST /api/chat` 的 `session_id` **必填**
  且必须为已登记属本人的会话——未传 422 `{"detail":"session_id 不能为空（请先
  POST /api/sessions 创建会话）"}`；未登记/他人会话 404 `{"detail":"会话不存在"}`。
  `"default"` 缺省值废弃。每轮刷 updated_at；title 为空时首问前 20 字回填
  （条件更新，用户改名后不再覆盖）。
- **新增记忆三端点**（本人视角）：`GET /api/memory/facts`（全量，按 kind,key
  排序）、`POST /api/memory/facts`（upsert `{kind,key,value}`，kind ∈
  profile|preference|constraint，key ≤60 字、value ≤500 字）、
  `DELETE /api/memory/facts?kind=&key=`（复合主键定位，不存在 404）。
- **compare 双轨适配**：两轨首跑前各 `POST /api/sessions {kind:"compare"}`
  取服务端下发 id（不入对话侧栏列表）。

## 1. 服务总览

- 监听端口 `:8000`(HTTP)。
- CORS:允许任意 Origin / Method / Header(公开 demo)。
- 版本号:`0.1.0`。
- 全部 JSON 端点返回 `application/json`;SSE 端点返回 `text/event-stream`。

## 2. 路由清单

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/health` | 健康检查 |
| GET | `/api/docs` | 已入库文档列表 |
| POST | `/api/search` | 调试:混合检索 |
| POST | `/api/chat` | SSE 流式问答 |
| POST | `/api/business/reset` | 清空 mock 业务数据（P21 起 admin-only） |
| GET | `/api/business/overview` | 查看当前预约与请假单（P21 起本人视图） |
| POST | `/api/auth/register` | 邀请码注册（P21，注册即登录） |
| POST | `/api/auth/login` | 登录（下发会话 cookie） |
| POST | `/api/auth/logout` | 登出（会话即失效） |
| GET | `/api/auth/me` | 当前登录用户 |
| POST | `/api/sessions` | 创建会话（P22，服务端下发 session_id） |
| GET | `/api/sessions?kind=` | 本人会话列表（P22） |
| PATCH | `/api/sessions/{id}` | 会话改名（P22） |
| DELETE | `/api/sessions/{id}` | 删会话（P22，三处连带） |
| GET | `/api/sessions/{id}/messages` | 历史恢复（P22，对话级） |
| GET | `/api/memory/facts` | 长期记忆事实列表（P22） |
| POST | `/api/memory/facts` | 新增/覆盖事实（P22，upsert） |
| DELETE | `/api/memory/facts?kind=&key=` | 删除事实（P22） |

### 2.1 GET /api/health

```json
{
  "status": "ok",
  "version": "0.1.0",
  "llm": true,             // 是否配置 LLM_API_KEY
  "embeddings": false,     // 有 key 且库里有向量时为 true
  "docs": 15,              // 文档数
  "chunks": 57,            // chunk 数
  "budget": {"used": 123, "limit": 2000000}
}
```

### 2.2 GET /api/docs

`[{doc_id, title, source, updated, chunks}]`,按 `doc_id` 升序。`chunks` 为该文档 chunk 数。

### 2.3 POST /api/search

请求 `{"query": "…"(1~200 字), "k": 5(1~20)}`,k 缺省 5。
响应 `[{doc_id, title, source, seq, text}]`,`text` 截断到前 **300** 字符。
k/query 越界返回 422。【差异决定】FastAPI 的 pydantic 校验错误体为
`{"detail":[{...loc,msg,type}]}`;Go 版统一返回 `{"detail":"<中文原因>"}`,字段名保持 `detail`。

### 2.4 POST /api/chat

请求模型:

```json
{
  "question": "…",          // 必填,1~500 字
  "mode": "auto",           // auto | direct | research,缺省 auto
  "session_id": "…",        // 必填(P22 起),≤64 字符;须为 POST /api/sessions 登记属本人的会话
  "role": "student"         // P21 起废弃忽略(服务端 users.role 权威)
}
```

- `question` 超过 `MAX_QUESTION_CHARS`(500)→ 422 `{"detail":"问题过长"}`。
- `session_id` 未传 → 422 指引「请先 POST /api/sessions 创建会话」;未登记/
  他人会话 → 404 `{"detail":"会话不存在"}`(P22,缺省值 `"default"` 废弃)。
- 预算耗尽 → 429 `{"detail":"今日 token 预算已用尽（上限 2000000），请明天再试"}`。
- 正常 → SSE 流,`Content-Type: text/event-stream`,响应头含
  `Cache-Control: no-cache`、`X-Accel-Buffering: no`。
- 每个事件格式:`data: {JSON}\n\n`(JSON 不转义非 ASCII 字符,即 UTF-8 原文输出)。
- 服务内部用户标识:`demo-{role}`(评测直调时为 `eval-user`)。P21 起为登录 email。

### 2.5 /api/business/*

- `POST /api/business/reset` → `{"status":"ok"}`(删除全部 bookings 与 leave_tickets,场馆表保留)。
- `GET /api/business/overview` → `{"bookings":[…], "tickets":[…]}`,结构见 §8.9。

## 3. SSE 事件类型(完整契约)

| type | 字段 | 说明 |
| --- | --- | --- |
| `route` | `route`, `reason`, `by_llm` | 每轮第一个事件(续轮/取消分支除外也先发 route) |
| `status` | `text` | 进度提示;主答案撞长度上限截断时,answer 流结束后追加"回答已达长度上限，可能被截断"(P10,不改 answer 正文) |
| `step` | `index`(1 起), `subquestion`, `sources`(去重后的 title 列表,≤3) | 深研子问题 |
| `answer_delta` | `text` | 答案增量,前端拼接 |
| `citations` | `items: [{n, doc_id, title, source}]` | 引用列表,直答/深研最后必发(无数据时 items=[]) |
| `slot_question` | `slot`, `question` | 办理流程追问 |
| `pending_action` | `tool`, `label`, `args`(中文标签→值) | 待确认动作摘要 |
| `action_result` | `tool`, `success`, `message`, `receipt`(可 null) | 工具执行结果 |
| `error` | `message` | 兜底错误(含预算超限),后跟 done |
| `done` | `latency_ms`(整型,本轮耗时), `reason`(可选,见 §4) | 每轮最后一个事件 |

`route` 取值:`factual | research | refusal | transaction | hybrid`。

## 4. 会话编排(chat 一轮的完整逻辑)

```
1. 若 session 处于 collect/confirm 阶段:
   classify_reply(question):
     continue → route{transaction,"继续办理：<流程label>",by_llm:false}
                → handle_reply → done
     cancel    → 清 session → route{transaction,"用户取消办理",false}
                → answer_delta "好的，已取消本次办理。有别的事随时找我。" → done
     new_topic → 清 session,继续走 2
2. 路由:mode=direct/research 时直接采用(_reason="用户指定 {mode}"_,by_llm=false);
   否则 route_question(见 §5)。发 route 事件。
3. 分发:
   refusal   → answer_delta REFUSAL_ANSWER → citations{items:[]}
   factual   → direct.answer_direct(见 §6)
   research  → research.run_research(见 §7)
   hybrid    → status "先回答你的政策问题…" → answer_direct
               → status "接下来为你办理业务…" → transaction.start_flow
   transaction → transaction.start_flow(见 §9)
   未知 route → error 事件 "未知路由：{route}"
4. done 事件(P10 起收口到 RunChat 单点发射,一轮恰一个;新增可选 `reason` 字段,
   omitempty 向后兼容):正常 `completed`;主答案流式 finish_reason=length →
   answer 后先发截断 status(见 §3)且 reason=`max_tokens`;链路错误 `error`;
   客户端断开(ctx 取消) `aborted`——此时 done 发不出去属预期,服务端静默。
异常:BudgetExceeded → error{message} → done;
     其他异常 → error{"{异常类名}: {msg}"} → done(Go 版为 Go 错误文案,见 §13)
```

固定文案(逐字保留):

- REFUSAL_ANSWER:`抱歉，我是钱塘大学的校园问答助手，只能回答与校园学习、生活相关的问题。你可以试试问我：转专业、保研、奖学金、图书馆、校历、宿舍、一卡通等话题。`
- NO_DATA_ANSWER:`知识库中暂时没有与这个问题相关的资料。如果你认为这属于校园政策/服务问题，欢迎换个说法再问一次。`
- DEMO_MODE_NOTE:`（检索演示模式：未配置 LLM_API_KEY，以下为知识库检索结果节选，不经过模型生成）`

## 5. 问题路由

### 5.1 LLM 路由(有 key)

- 模型:小模型(FLASH)。`json_mode`,temperature=0,max_tokens=200。
- 输出解析 `{"route": "...", "reason": "..."}`;route 合法则采用,reason 截断 100 字。
- 解析失败/调用异常 → 启发式。
- ROUTER_SYSTEM 提示词见 Python 版 `agent/prompts.py`(Go 版逐字复制,见 §14)。

### 5.2 启发式路由(无 key / LLM 失败降级)

按序判定:

1. 命中办理动词正则 `预约|预订|退订|取消预约|请假|事假|病假|销假|假申请|我的预约|待审批|批准`:
   - 请求词 `帮我|给我|我想|我要|麻烦|想请|想约|想订|帮我查|帮我看` 且 咨询词
     `什么|怎么|为什么|是不是|需不需要|能不能|多少|谁|规定|要求|政策|意思` → **hybrid**,"启发式：办理诉求 + 政策咨询"
   - 仅请求词或无咨询词 → **transaction**,"启发式：业务办理诉求"
   - 仅咨询 → 落入 2
2. 长度 > 32 或含信号词 `并且|同时|以及|分别|然后|还要|再加上|又想|还能|会不会|能不能|影响` → **research**,"启发式：长问题或含并列/多条件信号"
3. 否则 **factual**,"启发式：短事实型问题"

## 6. RAG 直答(factual)

1. `retriever.search(question, k=6)`(见 §7.5 检索细节)。
2. 无命中 → `answer_delta NO_DATA_ANSWER` → `citations{items:[]}`。
3. 无 key(演示模式) → 一次 `answer_delta`:
   `DEMO_MODE_NOTE\n\n` + 前 3 条命中拼 `[i] 《title》：text前180字…`(分隔 `\n\n`)→ citations(全部命中的引用)。
4. 有 key → 流式:system=ANSWER_SYSTEM,user=`参考资料：\n\n{编号上下文}\n\n问题：{question}`;
   编号上下文每条格式 `[i] 《title》（来源：source）\ntext`(完整 chunk 文本,`\n\n` 连接)。
   每个 token 增量发 `answer_delta`;结束发 citations。

## 7. 深度研究(research)

参数:MAX_SUBQUESTIONS=4,每路 k=5,MAX_EVIDENCE=12,单条证据文本截 600 字。

```
status "正在拆解问题…"
plan:无 key → [原问题];有 key → LLM(json,small,temp 0,max_tokens 400)
      取 subquestions 数组非空字符串,截前 4 个;失败 → [原问题]
逐子问题 i(1 起):
  hits = search(sub, k=5)
  step{index:i, subquestion, sources: 前三命中去重后的 title 列表}
  证据池:按 chunk_id 去重,保持到达顺序
无证据 → NO_DATA_ANSWER + citations{items:[]}
status "共检索到 {len} 条证据，正在交叉验证与综合…"
证据块(前 12 条):`[n] 《title》（来源：source）\ntext[:600]`
无 key → 一次 answer_delta:
  DEMO_MODE_NOTE + "围绕 {N} 个子问题共检索到 {M} 条相关段落，节选：\n\n" + 前 3 块
  → citations
有 key → 流式 ANSWER_SYSTEM,user=`参考资料：\n\n{blocks}\n\n问题：{question}`
  answer_delta* → citations
```

### 7.5 混合检索(Retriever.search)

1. 查询改写 `expand(query)`:有 key 时 LLM(small,temp 0,max_tokens 80)把口语改写为政策词串,
   结果 = `"{原查询} {改写}"`;改写失败或无 key → 原查询。进程内 dict 缓存。
2. BM25 取 `k*2` 条(见 §7.6)。
3. 向量路:库中有向量 **且** 有 key → embed 查询,余弦取 `k*2`;失败(异常)→ 本查询降级纯 BM25(log warning)。
4. 融合:有向量结果 → `rrf_fuse([bm_ids, vec_ids])` 取前 k;否则 `bm_ids[:k]`。
5. 组装 Hit{chunk_id, doc_id, seq, text, title, source}(doc 元信息缺失时 title=doc_id,source="")。

### 7.6 BM25 与分词

- 分词 `tokenize`:ASCII 字母数字串(正则 `[a-zA-Z0-9]+`)整词小写;汉字(`\u4e00-\u9fff`)按**字符二元语法**(相邻两字一组)。两类 token 直接拼接。
- BM25:k1=1.5,b=0.75,idf=`ln(1 + (N - df + 0.5)/(df + 0.5))`,tf 为 token 在 chunk 内计数,
  查询侧对 token **去重**后累计得分,doc_len 下限 1,按得分降序取 k。
- 索引缓存:首次查询时全量构建 postings/doc_len/idf/avgdl,写入后失效重建。

### 7.7 向量检索

- 暴力余弦:读出全部向量(chunk_id → float32 blob),L2 归一化缓存;查询向量归一化后点积,降序取 k。

## 8. 业务系统(business.service,SQLite)

- 库文件 `{DATA_DIR}/business.db`;表:venues(id,name,kind,capacity)、
  bookings(id,venue_id,date,slot,purpose,user,status('有效'|'已取消'),created_at)、
  leave_tickets(id,user,leave_type,start_date,end_date,days,reason,approver_level,status('待审批'|'已通过'),created_at)。
- 种子场馆(venues 空表时插入):
  `venue-badminton 羽毛馆 羽毛球馆 体育场馆 2`、`venue-basketball 篮球场 体育场馆 1`、
  `venue-room301 研讨间301 图书馆研讨间 1`、`venue-room302 研讨间302 图书馆研讨间 1`(按此序)。
- 时段常量 SLOTS:`08:00-10:00,10:00-12:00,14:00-16:00,16:00-18:00,19:00-21:00`。
- `approver_of(days)`:≤3 辅导员;≤7 学院;>7 教务处。
- created_at 为中国时区当前时间 ISO 格式(秒精度)。

### 8.1 book_venue(venue_id,date,slot,purpose,user)

校验顺序与返回:

1. 场馆不存在 → `{ok:false,error:"invalid",message:"场馆不存在"}`
2. slot 不在 SLOTS → `{ok:false,error:"invalid",field:"slot",message:"时段不合法"}`
3. date < 今天(CN) → `{ok:false,error:"invalid",field:"date",message:"不能预约过去的日期"}`
4. 该 user 当天有效预约数 ≥2 → `{ok:false,error:"quota",message:"每人每天最多预约 2 个时段"}`
5. 余量=capacity-该(场馆,日期,时段)有效预约数;≤0 →
   `{ok:false,error:"conflict",field:"slot",message:"{场馆名} {date} 的 {slot} 已约满",alternatives:[有余量的时段]}`
6. 成功插入 → `{ok:true,receipt:"VE-{id:04d}",message:"已预约 {场馆名} {date} {slot}"}`

### 8.2 cancel_booking(booking_id,user)

单号取数字部分解析(`VE-0003`→3,无数字→-1 查不到)。记录不存在或非"有效"→
`{ok:false,error:"not_found",message:"预约记录不存在或已取消"}`;非本人 →
`{ok:false,error:"permission",message:"只能取消本人的预约"}`;成功 →
`{ok:true,message:"预约 {原单号文本} 已取消"}`。

### 8.3 my_bookings(user)

有效预约按 date,slot 排序 → `[{booking_id:"VE-XXXX",venue,date,slot,purpose}]`。

### 8.4 remaining(venue_id,date)

`{slot: 余量}`,场馆不存在返回 `{}`。

### 8.5 submit_leave(user,leave_type,start,end,reason)

days=end-start+1;end<start 或日期非法 →
`{ok:false,error:"invalid",field:"end_date",message:"结束日期不能早于开始日期"}`;
start<今天 → `{ok:false,error:"invalid",field:"start_date",message:"开始日期不能是过去"}`;
成功 → `{ok:true,receipt:"LV-{id:04d}",days,approver,message:"请假申请已提交（{days} 天），按学校规定将由{approver}审批"}`。

### 8.6 leave_status(ticket_id,user)

不存在 → not_found "请假单不存在";非本人 → permission "只能查询本人的请假单";
成功 → `{ok:true,ticket:"LV-XXXX",leave_type,start,end,days,approver,status}`。

### 8.7 pending_leaves / approve_leave

- pending:状态"待审批"按 id 升序 → `[{ticket,user,leave_type,start,end,days,approver}]`。
- approve:不存在 → not_found;已处理 → `{ok:false,error:"invalid",message:"该请假单已处理"}`;
  成功 → `{ok:true,message:"请假单 {原单号文本} 已通过"}`。

### 8.8 leave_days(start,end) → int(end<start 或非法返回 -1)

### 8.9 调试视图

- all_bookings:有效预约按 id → `[{booking_id,venue,date,slot,user}]`
- all_tickets:全部请假单按 id → `[{ticket,user,leave_type,start,end,days,approver,status}]`

## 9. 知行执行层(transaction)

### 9.1 工具识别(启发式优先,按序首个命中)

| 顺序 | 工具 | 正则 |
| --- | --- | --- |
| 1 | cancel_booking | `取消预约|退订` |
| 2 | approve_leave | `批准|通过.*(请假|申请)` |
| 3 | pending_leaves | `待审批|审批.*(请假|申请)|谁.*请了假` |
| 4 | leave_status | `请假.*(单号|进度|状态|批了没)|LV-\d+` |
| 5 | my_bookings | `我的预约|我预约了|我订了` |
| 6 | query_venues | `(有|哪些|什么|能).*(场馆|场地|研讨间)|场馆.*(有|能|可)` |
| 7 | submit_leave | `请假|事假|病假|销假|休.*假|请.*天.*假` |
| 8 | book_venue | `预约|预订|订.*(馆|场|间)` |

启发式未识别且有 key → LLM 选工具(small,json,提示词=角色可见工具清单);仍无 → 知识兜底
(见 9.4)。

### 9.2 读工具(直接执行,无会话)

`query_venues / my_bookings / leave_status / pending_leaves` + 一切不在 FLOW_DEFS 内的工具:
组 args(query_venues 时若原句解析出日期则带 date)→ tools.call →
`action_result` → 成功时 `answer_delta{message}`,失败 `answer_delta{"办理未完成：{message}。"}`。

query_venues 的 message 格式:

```
{date(缺省今天)} 可预约场馆：
- {name}（{kind}，每时段 {capacity} 组）：{有余量时段顿号连接|（今日已约满）}
```

my_bookings 空 → `你目前没有有效预约。`;非空 → `你的有效预约：\n- VE-XXXX：{venue} {date} {slot}`。
pending_leaves 空 → `当前没有待审批的请假申请。`;
非空 → `待审批请假申请：\n- LV-XXXX：{user} {leave_type} {start}~{end}（{days} 天，{approver}审批）`。

### 9.3 写流程状态机(FLOW_DEFS)

| 工具 | label | 必填槽位 | 可选 |
| --- | --- | --- | --- |
| book_venue | 预约场馆 | venue,date,slot | purpose |
| submit_leave | 请假申请 | leave_type,start_date,end_date,reason | — |
| cancel_booking | 取消预约 | booking_id | — |
| approve_leave | 批准请假 | ticket_id | — |
| leave_status | 请假单查询 | ticket_id | — |

(注:leave_status 在 FLOW_DEFS 中但属 READ_TOOLS,实际走 9.2 直答。)

槽位元数据(ask 文案逐字保留):

| slot | label | ask | 解析 |
| --- | --- | --- | --- |
| venue | 场馆 | 想预约哪个场馆？可选：羽毛球馆、篮球场、研讨间301、研讨间302 | 名称子串匹配场馆表 |
| date | 日期 | 预约哪一天？（如：明天、周三、9月2日） | 日期解析→ISO |
| slot | 时段 | 预约哪个时段？可选：08:00-10:00 / 10:00-12:00 / 14:00-16:00 / 16:00-18:00 / 19:00-21:00（也可回复上午/下午/晚上） | 见 9.3.1 |
| purpose | 用途 | 预约用途是什么？（如：班级活动、训练） | 非空原文 |
| leave_type | 类型 | 请假类型是？（事假 / 病假 / 其他） | 含"事假/病假/其他" |
| start_date | 开始日期 | 从哪一天开始请假？（如：明天、下周一） | 日期解析→ISO |
| end_date | 结束日期 | 请到哪一天？（含当天，如：下周二） | 日期解析→ISO |
| reason | 事由 | 请简要说明请假事由 | 非空原文 |
| booking_id | 预约单号 | 要取消的预约单号是？（形如 VE-0001，可先查「我的预约」） | 正则 `VE-\d+` |
| ticket_id | 请假单号 | 请假单号是？（形如 LV-0001） | 正则 `LV-\d+` |

#### 9.3.1 时段解析 parse_slot

1. 文本含任一完整时段串或时段串前 5 字符(如 `08:00`)→ 该时段。
2. `(\d{1,2})[点:：时](\d{2})?` 取小时 h:h%12(0 视为 12)映射 {8,10,14,16,19}→时段;
   映射不到再试原始 h。
3. 含"上午/中午/下午/晚上/傍晚"且该词映射唯一时段 → 该时段
   (中午→14:00-16:00;傍晚→16:00-18:00 与 19:00-21:00 两选,不唯一,不命中)。
4. 否则 None。

#### 9.3.2 collect 阶段(_advance)

- 有 key:LLM 抽槽(SLOT_EXTRACT_SYSTEM,user 消息含今天日期/工具/字段定义/已收集/用户消息;
  small,json,temp 0,max_tokens 300)。抽出值过 `_normalize`:purpose/reason/booking_id/ticket_id
  取 strip 原文,其余过对应确定性解析器(解析不出丢弃);只填**尚未收集**的槽位。
- 无 key:上轮追问过槽位 → 只解析该槽位;首轮(last_asked 空)→ 机会性抽取:结构化槽位
  (venue/slot/leave_type;book_venue 的 date;submit_leave 的 start/end——parse_all 首末);
  自由文本槽(purpose/reason/单号)不猜测。
- 之后统一 `_apply_days_phrase`:submit_leave 且已有 start_date、无 end_date、文本含
  `([一二三四五六七八九]|\d+)天` → end_date = start + (n-1) 天。
- 缺槽 → 追问第一个缺失槽:`slot_question{slot,ask}` + `answer_delta{ask}`,记 last_asked。
- 齐全 → phase=confirm,发确认(9.3.3)。

#### 9.3.3 确认摘要(_emit_confirm)

args = {槽位中文label:值}(必填+已收集的可选,按 FLOW_DEFS 顺序)。submit_leave 追加:
`共: "{days} 天"`、`审批: "{approver}（按学校规定）"`;病假且 days>3 时 note=
`病假超过 3 天建议附医院证明。`。book_venue 的"场馆"值显示场馆名(非 ID)。
事件:`pending_action{tool,label,args}` + `answer_delta`
`请确认{label}信息——{k1}：{v1}；{k2}：{v2}…。{note}回复「确认」提交，或直接告诉我需要修改的地方。`

#### 9.3.4 confirm 阶段回复(handle_reply)

1. 先尝试修改:遍历 SLOT_META 全部键(跳过 purpose/reason),命中
   "该工具必填或已收集"的槽位且能从回复解析出**与现值不同**的值 → 更新,
   有任一修改 → `status{已更新，请重新确认：}` + 重发确认摘要。
2. 确认词 `确认|确定|好的|可以|提交|是的|对` → 执行(_execute)。
3. 取消词 `取消|算了|不办|不要` → 清 session,answer_delta
   `好的，已取消本次办理。有别的事随时找我。`
4. 其他 → answer_delta
   `没太听懂——请回复「确认」提交，或「取消」放弃，也可以直接告诉我需要修改的日期、时段等信息。`

#### 9.3.5 执行与失败恢复(_execute)

tools.call(全部已收槽位) → 成功:清 session,`action_result{success:true,...}` +
`answer_delta "办理成功：{message}（凭证号：{receipt}）"`(无 receipt 时省略括号段)。
失败:返回含 field 且 field 在 SLOT_META → 回到 collect,删该槽位,last_asked=field,
发 `action_result{success:false}` + `slot_question{field, ask+("可选时段：a、b" 若有 alternatives)}`
+ `answer_delta "{message}。{question}"`。
其他失败 → 清 session,`action_result{success:false}` +
`answer_delta "办理未完成：{message|未知错误}。如需继续请重新发起。"`。

### 9.4 工具未识别兜底

`answer_delta "这个问题我理解为你想咨询校园信息，为你转知识库检索："` → 走 §6 直答。

### 9.5 续轮意图(classify_reply)

有 key:LLM(small,json,max_tokens 60),system 提示含阶段(确认/补充信息)、已收集槽位、
三选一 continue/cancel/new_topic。失败或无 key → 启发式(按序):

1. `取消|算了|不办了|不要了` → cancel
2. `确认|确定|好的|可以|提交` → continue
3. `什么|怎么|为什么|几点|哪|谁|吗` → new_topic
4. last_asked 槽位能解析出值 → continue
5. 任一已收集结构化槽位(非 purpose/reason)能解析出值 → continue
6. 长度 ≤12 且无中英文问号 → continue
7. 否则 new_topic

## 10. 工具层与权限矩阵(tools)

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

tools.call 返回:未知工具 → `{ok:false,error:"unknown_tool",message:"未知工具：{name}"}`;
越权 → `{ok:false,error:"permission",message:"当前身份（学生|辅导员）无权执行「{label}」"}`;
缺参数(KeyError)→ `{ok:false,error:"missing_arg",message:"缺少参数：{字段名}"}`。
tool_descriptions(role) 只列该角色工具:`- {name}：{description}` 换行连接(给 LLM)。

## 11. 确定性日期解析(dates)

统一中国时区 UTC+8。给定 today(缺省中国时区今天):

1. 全日期 `(\d{4})[-/年.](\d{1,2})[-/月.](\d{1,2})[日号]?`(如 2026-09-02/2026/9/2/2026年9月2日)
2. 月日 `(\d{1,2})月(\d{1,2})[日号]?`:先按今年,若早于 today 顺延明年
3. 周几 `(下?)(周|星期)(一二三四五六日天)`:`周X`=最近将来的周X(含当天);
   `下周X`=下一周的周X(基于下周一,即 delta=(7-curWd)%7+target)
4. 相对词:今天/今日=+0,明天/明日=+1,后天=+2(全文多次出现全部收集)

全部命中按**出现位置**排序;`parse` 取第一个,`parse_all` 返回全部。识别不到 → nil。

## 12. 防线

### 12.1 限流(每 IP 令牌桶)

- 桶容量=速率=RATE_LIMIT_PER_MINUTE(默认 20),按时间线性回充(rate/60 每秒)。
- 客户端 IP:取 `X-Forwarded-For` 首段(去空白),否则 TCP 对端地址。
- 令牌不足 → 429 `{"detail":"请求过于频繁，请 {N} 秒后重试"}`,
  `Retry-After: {N}`,N=`int((1-tokens)*60/rate)+1`。
- 放行时响应头 `X-RateLimit-Limit-Minute: {int(rate)}`。
- 桶表进程内存储,不持久化;限流在 CORS 之前(最先执行)。

### 12.2 每日 token 预算

- 持久化 `{DATA_DIR}/usage.json`,格式 `{"date":"YYYY-MM-DD","tokens":N}`,**UTC 日期**滚动。
- ensure:used ≥ limit → BudgetExceeded `今日 token 预算已用尽（上限 {limit}），请明天再试`;
  在 chat 入口与每次 LLM/embedding 调用前检查。
- 计量:非流式按 usage.total_tokens;流式按字符数/2(下限 1);embedding 按 usage,缺省文本长度和/2。
- add 加锁写文件;文件损坏/不存在 → 视为 0。

### 12.3 会话状态

- 进程内 dict,session_id → {role,user,phase(idle|collect|confirm),tool,slots,last_asked,updated};
- TTL 30 分钟,get/ensure 时惰性清理;ensure 会刷新 role/user;clear 移除。

## 13. LLM 访问层

- OpenAI 兼容端点:`{LLM_BASE_URL}/chat/completions`、`/embeddings`(base_url 以 / 结尾拼接)。
- 模型分层:主答案/嵌入=LLM_MODEL(默认 glm-5.3);辅助调用(路由/拆解/抽槽/改写/意图/选工具,
  即 small=true)=LLM_SMALL_MODEL(默认 glm-5.3-flash)。
- chat:temperature 取调用点显式值——主答案(直答/深研)传零值 Options 即 **0**(26/26 基线行为,
  见 §18-7),辅助调用显式 0;max_tokens=2048;json_mode → `response_format:{"type":"json_object"}`;
  LLM_DISABLE_THINKING=true → 追加智谱私有参数 `thinking:{"type":"disabled"}`。
- chat_stream:SSE 流式,逐 delta.content 产出;结束后预算入账 max(1, chars/2)。
- embed:批量输入,按 index 排序返回向量。
- 无 key:has_key()=false,一切调用前置检查;未配 key 直接调用 → 运行时错误
  `未配置 LLM_API_KEY，无法调用模型`。
- 超时/网络错误向上抛,由调用方各自降级(路由→启发式;改写→原查询;向量路→纯 BM25;抽槽→启发式)。

## 14. 配置(环境变量,仓库根 .env 提供缺省,进程环境优先)

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| LLM_API_KEY | 空 | 空=零 key 演示模式 |
| LLM_BASE_URL | `https://open.bigmodel.cn/api/paas/v4/` | OpenAI 兼容 |
| LLM_MODEL | `glm-5.3` | 主答案模型 |
| LLM_SMALL_MODEL | `glm-5.3-flash` | 辅助调用模型 |
| EMBED_MODEL | `embedding-3` | 向量模型 |
| LLM_DISABLE_THINKING | false | 关闭 glm 推理模式 |
| DATA_DIR | `<仓库根>/data` | 数据目录 |
| CORPUS_DIR | `{DATA_DIR}/corpus` | 语料目录 |
| INDEX_PATH | `{DATA_DIR}/index.db` | 索引库 |
| RATE_LIMIT_PER_MINUTE | 20 | 限流 |
| DAILY_TOKEN_BUDGET | 2000000 | 每日 token 上限 |
| RETRIEVAL_K | 6 | 默认检索条数 |
| MAX_QUESTION_CHARS | 500 | 问题长度上限 |

## 15. 入库 CLI(ingest)

`数据流:corpus/*.md → frontmatter 解析 → 分块 →(可选)向量化 → SQLite`

- frontmatter:`---\n…\n---\n` 之间 `key: value` 逐行解析,键小写;缺省
  title=文件名去扩展、source=钱塘大学、updated=""。
- 分块:空行分段,段聚合 ≤450 字;超长单段 450 硬切、保留末 80 字重叠。
- 向量化:批 32,L2 归一化后 float32 blob 入库;失败一次 → 全程降级仅 BM25(不再重试)。
- 幂等:同 doc_id 重导先删旧 chunks+vectors 再插。doc_id=文件名去扩展。
- CLI 参数 `--no-embed`(仅 BM25)/`--rebuild`(先删库文件)。Go 版:
  `go run ./cmd/server -ingest [-no-embed] [-rebuild]`(或 make ingest)。

## 16. 零 key 模式(离线可跑)

- /health 正常(llm=false,embeddings=false)。
- 路由:启发式(§5.2);改写:直通;直答/深研:检索演示文案(§6.3/§7);交易链路:完整确定性
  (启发式选工具 + 机会性抽槽 + 追问解析 + 确认流 + 执行),refusal 类问题**不**拒答(启发式无此能力)。

## 17. 评测客户端约定(eval/run_eval.py,P5 改造)

- 评测改为 HTTP 客户端:`BASE_URL` 指向运行中的服务;`POST /api/chat` 解析 SSE 事件流,
  按 §3 聚合(routes/answer/cited/asked_slot/pending_tools/results/errors/latency);
  多轮用例每例前 `POST /api/business/reset`,断言用 `GET /api/business/overview`;
  `date_text` 期望换算在客户端内置(与 §11 相同规则的本地实现,评测脚本保留 Python)。
- 评分规则不变:单轮=关键词命中(any)+引用召回(交集非空);refusal=routes 含 refusal 或答案含
  "只能回答";多轮=expect 全部子断言 + 无 error 事件。

## 18. 【差异决定】已知有意差异(不追求逐字节复刻)

1. **校验错误体**:FastAPI/pydantic 的 422 错误体为机器格式数组;Go 版统一
   `{"detail":"<中文原因>"}`(与手写 HTTPException 的 422/429 格式一致)。评测不依赖此差异。
2. **内部异常文案**:Python `error` 事件 message 形如 `ValueError: xxx`(类名:消息);
   Go 版为 Go 错误字符串。语义等价,仅调试可见。
3. **流式预算计量**:两侧都按"字符数/2"估算,但 Unicode 拆分差异可忽略不计;
   Go 版在流式中途断开时也会对已产出部分入账(Python 版中断不记账,属修复,见 go-notes §10-4)。
4. **sqlite 驱动**:Python stdlib sqlite3 → modernc.org/sqlite(纯 Go)。数据文件格式兼容
   (同一 index.db/business.db 可互读);并发写策略 Go 侧单写连接 + 锁,见 go-notes。
5. **请求日志**:uvicorn access log 不在契约内;Go 版 gin 默认日志保留,不影响行为。
6. **leave_status 读工具返回**:Python 版业务层返回的 dict 无 `message` 键,tools 层
   `result["message"]` 抛 KeyError → 整轮变 error 事件(用户问「我的请假单批了吗」
   必然报错,属原设计缺陷;评测集未覆盖)。Go 版返回可读的请假单状态摘要。
7. **主答案 temperature**:Python 版 chat 缺省 0.2(直答/深研未显式覆盖);Go 版调用点传零值
   Options → temperature=0。26/26 基线在 temp=0 下取得,微服务迁移以此为准
   (2026-09-04 决策,§13 措辞已同步修订)。
