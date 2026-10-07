# P42 provider 内容审查拒绝兜底（任务书）

> **背景**：provider（智谱 GLM 主/小模型）的内容安全审查是平台基建行为，触发
> 时主循环被拒会以「服务内部错误」漏出（P36 笼统文案），小模型族则存在
> P40 实证的「错误文案被当改写结果消费」污染（GLM 403 撞坑，挂账于
> [P41 任务书](P41-bailian-rerank.md) §6）。本票做**反应式兜底**：识别
> provider 拒绝 → 分类归一 → 各链路优雅降级。**红线约束**：全程不让任何
> 真实敏感词进入代码/测试/日志/上下文——识别只按官方错误契约形态与审查类
> 通用词面（审查「动作/结论」表述，非具体词条），测试一律自造占位 payload；
> 只做识别与降级，不做任何绕过审查的尝试。真实拒绝负例无法安全构造（构造
> 即违背红线），拒绝路径验证以 mock 端到端为准，本任务书留档说明。
>
> 开源对照（2026-10-06 调研）：WeKnora 0.8.2 本地副本 + Dify/Open WebUI/
> new-api/FastGPT 官方文档与源码，吸收清单见 §2.6。

## 0. 拍板结论

| # | 问题 | 拍板 | 依据 |
|---|---|---|---|
| Q1 | 范围 | 反应式兜底四件套（分类器 / 200 形态拦截 / 主循环优雅拒答 / finish 收口），不做本地词表预检 | Q2 用户拍板；provider 即输出侧防线，本地词表重复设审（Dify 对标结论） |
| Q2 | 主循环被拒的用户形态 | 优雅拒答走正常回答流 + `done(completed)`，SSE 契约零变化 | Q2 用户拍板；WeKnora observe.go 同款「按正常完成流关闭」 |
| Q3 | done reason 是否新增 refused 枚举 | 不新增，拒答轮 reason=completed（与 guard block 轮一致），refusal 语义由 route 事件承载 | 前端零改动；trace 按 route=refusal 可统计 |
| Q4 | 拒答形态识别依据 | 智谱官方契约：1301（HTTP 400）+ `contentFilter[]` 字段 + 流式非标准 `finish_reason=sensitive`；词面兜底仅 400/403 启用 | docs.bigmodel.cn cn/faq/api-code、cn/guide/platform/securityaudit |
| Q5 | 审查类失败是否重试 | 不重试（openai SDK 对 400 本就不重试；WeKnora retry.go 同款「非 2xx 是 vendor 在回答你」） | 重试结果相同，纯烧配额 |
| Q6 | 开关 | `CONTENT_FILTER_FALLBACK` 布尔，缺省开，`=0` 紧急回退（STREAM_ANSWER「缺省开」idiom），门禁四处全链 | 行为开关核心域惯例（AGENTS.md） |

## 1. 目标 / 非目标

**目标**：
1. provider 拒绝（异常形态 + 200 形态）全链路可识别、可分类、可观测；
2. 主循环被拒：用户看到优雅拒答文案（正常回答流），trace 记 refusal/provider；
3. 小模型族被拒：既有降级链接住统一异常，降级日志区分审查成因（P40 污染根治）；
4. `finish_reason=sensitive|content_filter` 按截断语义收口（P10 契约补位）；
5. 全量可一键回退。

**非目标（不夹带）**：
- 本地敏感词预检/词表外部化/AC 自动机（挂账，路线见 §6）；
- guard 关键词闸与 `guardrails.py` 词表不动（该文件本票未读未改——红线）；
- bailian rerank / embed / IQS 既有降级链不动；
- 输出侧逐 delta 巡检（Dify 300 字符缓冲式输出审核，明确不吸收，见 §2.6）；
- `contentFilter[].level` 分级差异化动作；文案 env 化。

## 2. 设计与实现

### §2.1 分类器 `apps/server/gewu/llm/safety.py`（新模块）

归一化思想取自 WeKnora（各家审查信号 → canonical 形态），四层识别：

- `ContentFilterError(RuntimeError)`：本系统统一异常（LLMService 200 形态
  拦截抛出；SSE 层 isinstance 命中）；
- `is_content_filter_error(exc)`：异常形态判定——① isinstance 本模块异常；
  ② duck-typing 读 `status_code`/`body`（openai.APIStatusError 有前者，
  httpx.HTTPStatusError 挂 `.response.status_code`）：`error.code=="1301"`
  或 body 含顶层 `contentFilter` 键即命中；③ 消息词面兜底（敏感/违规/不合规/
  安全审核/审核未通过/安全策略/内容安全/content filter/sensitive/moderation，
  **仅 status ∈ {400, 403} 启用**防误判）；④ 无状态码的普通异常一律 False。
  1302-1306（限流/并发）、1000-1005（鉴权）、429 明确不命中（测试断言）；
- `is_filter_finish(reason)`：`reason in ("content_filter", "sensitive")`
  双 canonical 值——智谱流式分批检测、命中在末条 chunk 以非标准
  `sensitive` 体现（官方文档明示），只按 OpenAI 标准值匹配会漏；
- `looks_like_error_content(text)`：200 文案形态防御，从严两条——JSON
  error 对象形状；「拒绝动作词（检测到/无法/抱歉/对不起/已拦截/被拦截）×
  审查话题词」同时命中。单项命中放行（「违规用电处分规定」类合法政策术语、
  「无法按时注册」类追问不误伤）；小模型族四类合法输出（改写串/精排分数
  JSON/追问列表/记忆摘要）不可能同时命中两类词（测试覆盖）。

模块内只有审查「动作/结论」类通用表述，无任何具体敏感词条；错误码集与
词面正则均为模块级常量，新 provider 形态加码值即可。

### §2.2 LLMService.chat 200 形态拦截（修 P40 污染挂账）

`chat()` 两条返回路径统一走 `_text_guarded(resp)`：
`is_blocked_completion(parse_finish_reason(resp), text)` 命中 → 打印
`[llm] 内容审查拒绝（200 形态拦截）` → raise `ContentFilterError`。
六个小模型调用点（改写/flash 精排/追问/深研拆解/续轮意图/记忆固化）的既有
`except Exception` 降级**一行不改**自动接住——官方「勿把被拦截内容回传
重试」我们天然满足（不新增任何重试）。`Rewriter.expand` 与
`_rerank_or_keep` 的降级打印用 `is_content_filter_error` 区分一版
「被内容审查拒绝」文案，线上日志可辨识成因。开关=0 时原样返回文本
（回退改前行为）。`chat_full`/`chat_stream`/`chat_with_tools` 无生产
调用方，不加护栏（留档）。

### §2.3 主循环被拒 → 优雅拒答（api/chat.py）

`generate()` 的通用 `except Exception` 前插分类分支（开关门禁 +
`is_content_filter_error` 命中）：

1. 事件序：`answer_reset`（仅当本轮已流出 answer_delta——流中途被拒时
   已发文本转存为 step，P30 既有语义）→ `route_decision(refusal,
   layer=provider, by_llm=False)` → `answer_delta(PROVIDER_FILTER_ANSWER)`
   → `done(completed)` 后 return；
2. 不发 follow_ups（route=refusal 不在 Q5 白名单）、不固化记忆——与
   guard block 轮同语义；
3. trace 记 `route=refusal / route_layer=provider / reason=completed`，
   `make trace-query` 按 route 聚合即可统计被拒轮次（对标 WeKnora
   `content_filter_stop` 管道事件的观测位）；
4. 拒答文案 `PROVIDER_FILTER_ANSWER` 入 `agent/prompts.py`：与
   GUARD_BLOCK_ANSWER 语气一致、成因不同（provider 侧审查触发，本系统
   不预判不复制其词表）；
5. 未命中分类的异常走既有通用 error 路径，一字不动。

SSE 契约零变化：不新增事件类型、不新增 done reason。

### §2.4 finish 收口（AgentDoneMiddleware，补 P10 欠账）

P10 契约文档列了 `content_filter` 位但代码只认 `length`——补齐：
`after_agent` 中 `finish==length` 分支旁加 `is_filter_finish(finish)`
分支（构造参数 `finish_guard` 门禁，agent.py 传
`settings.content_filter_fallback`）→ `truncated=True` + status 事件
「回答被安全策略中断，内容可能不完整」，done reason 沿用 truncated 的
`max_tokens` 既有映射（契约零变化；status 行已说明真实成因）。内容为空时
answer 走既有 partial_answer 兜底。流式路径若 SDK 对非标准 finish 解析
抛错，则落入 §2.3 异常分支兜住——两条路都闭合。

**已知留档**：`finish=sensitive` 且带 tool_calls 的罕见形态不做专门处理
（TruncationDefense 只拦 length；轮次上限 8 兜底有界），线上出现再议。

### §2.5 开关与配置

`CONTENT_FILTER_FALLBACK`（config.py，缺省开：`env.get(..., "1").lower()
not in ("0","false","no","off")`，STREAM_ANSWER 同款「缺省开」idiom），
门禁 §2.2/§2.3/§2.4 全链。`.env.example` agent 节加行。服务器零新增
必需 env。

### §2.6 开源对照吸收清单（2026-10-06 调研）

**已印证**（拍板方向与开源实践一致）：
- WeKnora `internal/agent/observe.go:380-395`：content_filter 命中 →
  终止 agent 循环（防被过滤响应在上下文累积死循环）、部分输出沿用/为空用
  固定兜底文案、**按正常完成流关闭**（EventAgentFinalAnswer，非报错）——
  Q2「优雅拒答+done(completed)」的同构先例；
- WeKnora `internal/models/api/retry.go`：只重试 TransportError，
  「非 2xx 是 vendor 在回答你，重试既不会成功也不给运维新信息」——Q5；
- WeKnora 输入侧只防 XSS/注入（`internal/utils/security.go` ValidateInput），
  不做内容审查——「provider 即输出侧防线」的立场印证（Q1）。

**吸收进设计**：
- 智谱官方契约（docs.bigmodel.cn）：1301=HTTP 400 唯一内容安全码；
  响应体 `contentFilter[]`（role=user/assistant 区分输入/输出侧、level
  0-3）；流式命中以末条 chunk 非标准 `finish_reason:"sensitive"` 体现；
  官方建议勿把被拦截内容回传模型重试——Q4/Q5 直接依据；
- WeKnora 归一化层（anthropic `refusal`→content_filter、google
  `SAFETY/BLOCKLIST/PROHIBITED_CONTENT`→content_filter 的映射函数组）——
  safety.py 即本系统的对应角色，双 canonical finish 值设计由此而来。

**明确不吸收**（有证据的放弃）：
- Dify 输出侧审核（`api/core/moderation/output_moderation.py`：300 字符
  缓冲边流边审 + 命中整体替换前端内容）：需本地词表/审核模型，与「provider
  即防线」重复设审；且逐 delta 缓冲改变流式语义。Dify 对 provider 审查
  错误本身零分类处理；
- new-api AC 自动机本地词表（`service/sensitive.go` + 管理端 UI 词表）：
  本轮范围拍板不做本地词表（挂账留路线）；
- `contentFilter[].level` 分级文案（0 最严重 3 最轻）：无差异化动作可执行，
  单一优雅拒答已覆盖；
- FastGPT 为反面教材：issue #1566 输出侧流式被审截断暴露为模糊内部错误
  ——本票 §2.4 正是补这个坑；issue #5020 证实其核心产品不内置审核。

**挂账新增**：见 §6。

## 3. 测试

新增 20 例，全自造占位 payload（零真实敏感词）：

- `tests/test_llm_safety.py`（14 例）：分类器真阳（1301 码/contentFilter
  形状/400·403 词面）与真阴（1302 限流、1000 鉴权、429、普通异常、无
  status 异常）；`is_filter_finish` 双值与排除集；`looks_like_error_content`
  三类正例 + 五类合法输出负例；`is_blocked_completion` 或逻辑；
  LLMService.chat 拦截三形态（sensitive finish/content_filter finish/
  错误文案）+ 开关=0 回退 + 正常文本透传；
- `tests/test_config.py`（1 例）：缺省开 + 四种关闭取值；
- `tests/test_chat_api.py`（5 例）：审查异常 → 优雅拒答全事件断言
  （route(refusal/provider)+拒答文案+done(completed)+无 error+无
  follow_ups）；流中途被拒 → answer_reset 转存 + 拒答全文；开关=0 →
  既有 error 路径回归；`sensitive`/`content_filter` 双 finish → status
  行 + max_tokens 截断语义（参数化）。

## 4. 验收

1. `make test` 全绿（347 passed，含新增 20 例）；`make lint` 过；
2. 开关=0 行为与改前逐点一致：LLMService 原样返回可疑文本（测试断言）、
   SSE 走既有 error 路径（测试断言）；
3. 真网冒烟：主/小模型 chat + 向量三通道 OK；走护栏 `chat()` 正常文本
   透传；应用启动 health OK。**真实拒绝负例无法安全构造**（构造即违背
   「不让敏感词进上下文」红线）——拒绝路径以 mock 端到端为准，留档说明；
4. 部署后 `make trace-query-remote` 验 refusal 轮可按 route 统计。

## 5. 上线与回退

- 部署：服务器 `git pull` + `systemctl restart gewu-api` 即可——零新增
  必需 env（开关缺省开）；
- 回退：服务器 `.env` 加 `CONTENT_FILTER_FALLBACK=0` + 重启（一行回退
  改前行为）；或直接 `git revert` 本票。

## 6. 注意与挂账

- **红线纪律**：真实敏感词永不进代码/测试/日志/上下文；分类只认契约形态
  与通用词面；`guardrails.py` 词表文件本票未读未改。
- 词面正则是「从严不贪全」：`looks_like_error_content` 靠「动作×话题」
  双词面防误伤，若线上出现漏判新文案形态，按「加码值/加词面 + 补测试」
  扩展，不做泛化放宽。
- 挂账（不本票）：
  - `user_id` 终端用户隔离进 LLM 请求（智谱平台可封违规终端用户而非
    企业 key，游客通道下有价值；接线方式待一次真网 curl 验证后接入）；
  - 主循环 200 错误文案形态不逐 delta 巡检（=不吸收 Dify 输出审核；
    实测智谱主形态是 1301 异常或 sensitive finish，出现再议）；
  - 本地词表若将来做：pyahocorasick（BSD+PD，AC 自动机原语）+ 自建
    归一化层，策略清单参照 houbb/sensitive-word（Apache-2.0）；
    guard 词表外部化候选（`GUARD_EXTRA_WORDS_FILE`）同挂；
  - finish=sensitive 带 tool_calls 形态（§2.4 已留档）。
- P40 撞坑 #1（rewriter fail-open 被错误文案污染）本票 §2.2 闭线，
  P41 任务书 §6 挂账已注记。

## 7. 执行实况（2026-10-07）

**实现**：`llm/safety.py` 分类器（1301/contentFilter/双 canonical finish/
双词面从严四层）+ `LLMService.chat` 200 形态拦截（`_text_guarded` 单点，
六调用点既有降级零改动接住）+ `api/chat.py` 优雅拒答分支（answer_seen
跟踪 + 部分流 reset 转存 + refusal/provider 进 trace）+ AgentDone
`finish_guard` 收口 + `Rewriter`/`_rerank_or_keep` 降级文案区分 +
`CONTENT_FILTER_FALLBACK` 开关。PARITY 新增 §0.13 + §14 配置表行，
roadmap 挂账对齐，P41 任务书 §6 挂账注记闭线。

**测试**：新增 20 例（分布见 §3），`make test` **347 passed 全绿**，
`make lint` 全过（ruff check + format）。

**真网冒烟**：`scripts/smoke_chat.py` 三通道 OK（main/small 模型
finish=stop + embed dim=2048）；走 P42 护栏 `chat()` 真网调用正常文本
透传（`[guarded-chat] 通过 P42 拦截层`）；uvicorn 起服 health OK
（llm=true/embeddings=true/219 文档/1171 块）。拒绝路径 mock 端到端
5 例全过（§3），真实负例因红线不构造（§4.3 留档）。

**验收判定**：①347 passed + lint 过 ✓；②开关=0 两处回归断言过 ✓；
③真网冒烟三通道 + 应用启动 ✓；④refusal 可统计性待部署后
`make trace-query-remote` 复核。
