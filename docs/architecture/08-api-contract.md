# 08 · 接口契约（API 与 SSE 事件流）

接口层的唯一行为规格是 **[PARITY.md](../PARITY.md)**——它是这个项目跨实现迁移（python-final → go-final → P14 LangGraph 版）三次仍保持前端/评测零改动的关键，P21~P25 的契约演进以 §0.5~§0.8 注记追加。本文做导读：27 端点全景、十一类 SSE 事件、interrupt/resume 桥与行为开关；字段级细节以 PARITY 为准。

## 端点全景（7 路由文件 · 27 端点）

**核心问答（`routes.py` + `chat.py`）**

| 方法 | 路径 | 守卫 | 职责 |
| --- | --- | --- | --- |
| GET | `/api/health` | 无（限流豁免） | 健康：docs/chunks 计数、llm/embeddings 就绪、全局预算用量 |
| GET | `/api/docs` | 无 | 已入库文档列表（doc_id 升序，含每篇 chunk 数） |
| POST | `/api/search` | 登录 | 调试用混合检索（`{"query","k"}`，k 1~20；text 截 300 字） |
| POST | `/api/chat` | 登录 + 会话归属 | **SSE 流式问答**（主入口） |
| POST | `/api/business/reset` | admin | 清空业务运行数据（评测/演示前置） |
| GET | `/api/business/overview` | 登录 | 台账：默认本人视图，admin 可 `?all=1`（评测断言数据源） |

**认证（`auth.py`，详见 [11](11-auth.md)）**：`POST /api/auth/register`（邀请码 + 注册即登录）/ `POST /api/auth/login` / `POST /api/auth/logout` / `GET /api/auth/me`——cookie 会话（`gewu_session` httpOnly）。

**会话（`sessions.py`，P22）**：`POST /api/sessions`（显式创建，下发 token_urlsafe id）/ `GET /api/sessions`（本人列表）/ `PATCH /api/sessions/{id}`（改名）/ `DELETE /api/sessions/{id}`（三处连带删除）/ `GET /api/sessions/{id}/messages`（历史恢复，见 [07](07-state-persistence.md)）。

**记忆面板（`memory.py`，P22）**：`GET|POST|DELETE /api/memory/facts`——fact 查看/新增/删除（复合主键含 user_id 防越权）。

**管理后台（`admin.py`，P23，全部 require_admin）**：`GET /api/admin/stats|users|invites|sessions|usage`、`PATCH /api/admin/users/{email}`（角色/停用/个人限额）、`POST /api/admin/invites`、`DELETE /api/admin/sessions/{id}`——八端点支撑 /admin 页五区。

**反馈（`feedback.py`，P25）**：`POST /api/feedback`（204；`message_feedback` 表 upsert，登录 + 归属 404 防枚举 + 探测式软降级 503）。

错误体统一 `{"detail": "<中文原因>"}`；类型不符统一「请求体不是合法 JSON」（`RequestValidationError` 全局 handler 收口，**不用 pydantic 默认校验体**——PARITY §2.3 的差异决定）。chat 请求校验序列（`_parse_chat_body`）：

| 字段 | 约束 | 越界 detail |
| --- | --- | --- |
| `question` | 非空字符串，≤500 字 | 问题不能为空 / 问题过长 |
| `mode` | auto / react(=auto)（P31 收窄；classic/direct/research 422） | mode 必须为 auto/react |
| `session_id` | **必填**且为已登记属本人的会话 | 未传 422 给指引；不存在/非本人统一 404（防枚举） |
| `role` | ~~请求参数~~ | P21 废弃——服务端取 `users.role`（[11](11-auth.md)） |

## SSE 事件流（十一类）

分帧 `data: {json}\n\n`（UTF-8 原文不转义，`separators=(',', ':')` 紧凑 JSON）；响应头 `Cache-Control: no-cache` + `X-Accel-Buffering: no`。事件序（PARITY §3 逐字段契约）：

```text
route（可两段） → [status|step]* → answer_delta* → [截断 status] → citations
      → [slot_question | pending_action → (下一轮) action_result]
      → done（恰一个，reason: completed | max_tokens | error | aborted）
      → follow_ups?（P25：done 之后追发，可缺席；前端 done 即解锁输入，pills 晚到渐进渲染）
```

| 事件 | 字段 | 说明 |
| --- | --- | --- |
| `route` | route, reason, by_llm, [layer, confidence] | 意图表达，**纯观测标签**（P31 起不决定任何控制流）。两段式：guard 关键词闸在纯问候/危险词命中时发 provisional（layer=guard，chitchat/refusal；by_llm=false），收尾按工具轨迹合成 effective（layer=effective）补发——前端徽章覆盖更新、评测 set 聚合兼容 |
| `status` | text | 阶段提示（先答政策/正在拆解/调用工具…） |
| `step` | index, subquestion, sources[] | 深研逐路进度（sources 为该路 top3 标题去重） |
| `answer_delta` | text | 流式回答增量（前端拼装） |
| `citations` | items[] | 引用列表；**items 恒为数组，空也要 []**（不能 null） |
| `slot_question` | slot, question | 办理追问（slot 为槽位名） |
| `pending_action` | tool, label, args{} | 确认卡（args 为中文 label → 值的有序表） |
| `action_result` | tool, success, message, [receipt] | 执行回执（receipt: VE-XXXX/LV-XXXX） |
| `error` | message | 链路错误（随后必有 done(error)） |
| `done` | latency_ms, [reason] | 一次 chat 恰一个；SSE 端点单点发射 |
| `follow_ups` | items[] | P25 追问 pills（恰好 ≤3 条，≥2 条才发）；Q5 门=知识型路由（factual/research/hybrid）+ completed + 无 HITL 悬停 |

follow_ups 的守卫全在 `gewu/agent/followups.py`：flash 小模型生成（8s 线程超时静默降级——**主路径零延迟增量**，P24 已证串行模型调用是结构性成本）、三层代码守卫（JSON 解析失败即弃 / 逐条 6~30 字且不等于原问且保序去重 / 剩余 <2 条即弃）——GLM flash 无视否定指令必须代码兜底。

三端共用不漂移：前端（`apps/web/lib/api.ts` 手写 SSE 解析）、评测（`eval/run_eval.py` 纯标准库）、服务端（`gewu/agent/events.py` 构造器，字段序对齐 Go struct）。

一次知识型问答的实际事件流（agent 链路 + P25 追问；guard 对普通问题不发事件）：

```text
data: {"type":"status","text":"正在理解问题…"}
data: {"type":"status","text":"调用工具 search_knowledge…"}
data: {"type":"answer_delta","text":"图书馆工作日…"}
data: {"type":"citations","items":[{"n":1,"doc_id":"0007-library","title":"图书馆管理办法","source":"图书馆"}]}
data: {"type":"route","route":"factual","reason":"本轮检索作答","by_llm":true,"layer":"effective","confidence":0.9}
data: {"type":"done","latency_ms":5265,"reason":"completed"}
data: {"type":"follow_ups","items":["考试周开放时间有变化吗","周末可以去自习吗","借书最多能借几本"]}
```

## interrupt/resume 桥

thread 停在确认门（interrupt）时，用户下一条消息**照常 POST /api/chat**：端点 `get_state` 检测到 `snap.next` 非空，中断载荷（含 `action_requests`）经 `resume.py` 把用户文本翻译为 decisions（确认→approve / 取消→reject / 修改与切话题→respond，**修改不走 edit**——那会跳过二次确认直接执行）。前端与评测对 interrupt 完全无感知：

```python
run_input = state_input
snap = graph.get_state(config)
if snap.next:
    payload = find_hitl_payload(snap)          # agent HITL：action_requests 在场
    if payload is not None:
        run_input = Command(resume=hitl_decisions(payload, req["question"], llm, business))
# 消费：P31-3 外壳塌缩后无嵌套图，subgraphs 摘除——custom 事件不再包 (namespace, event) 元组
for evt in graph.stream(run_input, config, stream_mode="custom"):
    yield _sse(evt)
vals = graph.get_state(config).values           # 终态侧记（interrupt 悬停轮为当前值）
truncated, answer, hitl_paused = bool(vals.get("truncated")), vals.get("answer", ""), bool(snap.next)
```

done 单点也在这个 `generate()` 里：流正常结束 `reason = "max_tokens" if truncated else "completed"`；链路异常先 `error` 再 `done(error)`；客户端断开（GeneratorExit）记 aborted。每轮结束打一行 `[chat]` JSON 汇总日志（session/路由/步数/耗时/结局单点可见，见 [09](09-cross-cutting.md)）。

## 行为开关（现存）

| 开关 | 取值（缺省在前） | 说明 |
| --- | --- | --- |
| `RERANK_MODE` / `RERANK_THRESHOLD` | on / 2.0 | LLM 精排与阈值（全滤空自动退化） |
| `MEMORY_CONSOLIDATE` | on / off | 记忆固化线程（评测隔离用） |
| `RATE_LIMIT_PER_MINUTE` | 600 | 限流（评测建议 600） |
| `DAILY_TOKEN_BUDGET` / `DAILY_USER_BUDGET` | 200 万 / 20 万 | 全局闸 / 个人闸缺省 |
| `COOKIE_SECURE` / `CORS_ALLOW_ORIGINS` | false / 空 | https 部署开启 / 跨域白名单（空=仅同源） |

已退役：ROUTER_MODE、SESSION_STORE（checkpointer 接管）、CHUNK_MODE（hierarchical 唯一，`CHUNK_STRATEGY` 面向入库侧）、REACT_MODE（P17：react 与 auto 同路）、QUERY_REWRITE（P31-2：resolve_query 节点随 classic 退役，指代消解交主循环 messages 历史）。检索与切片参数全量见 [04](04-rag-retrieval.md)/[09](09-cross-cutting.md)。

## 相关文件

| 文件 | 职责 |
| --- | --- |
| `gewu/api/routes.py` / `chat.py` | 核心六端点 + SSE/resume 桥/done 单点/follow_ups 追发 |
| `gewu/api/auth.py` / `sessions.py` / `memory.py` / `admin.py` / `feedback.py` | 认证/会话/记忆面板/管理/反馈五组端点 |
| `gewu/agent/events.py` | 事件构造器（items/args 空时必须 []/{} 而非 null） |
| `gewu/agent/resume.py` / `followups.py` | HITL resume 翻译 / 追问生成与三层守卫 |
| [PARITY.md](../PARITY.md) | 行为规格（字段级权威；§0.5~§0.8 为 P21~P25 契约演进注记） |
| `tests/test_chat_api.py` / `test_search_api.py` / followups/feedback 专项 | 契约测试 |

---

下一篇《09 · 支撑域与横切面》覆盖配置、四层成本防线、模型分层、观测日志、依赖守护与 CI 这些不承载业务语义但决定工程质量的部分。
