# 08 · 接口契约（API 与 SSE 事件流）

接口层的唯一行为规格是 **[PARITY.md](../PARITY.md)**——它是这个项目跨实现迁移（python-final → go-final → P14 LangGraph 版）三次仍保持前端/评测零改动的关键。本文做导读：六端点、十类 SSE 事件、interrupt/resume 桥与行为开关；字段级细节以 PARITY 为准。

## 六端点

| 方法 | 路径 | 职责 |
| --- | --- | --- |
| GET | `/api/health` | 健康：docs/chunks 计数、llm/embeddings 就绪、预算用量 |
| GET | `/api/docs` | 已入库文档列表（doc_id 升序，含每篇 chunk 数） |
| POST | `/api/search` | 调试用混合检索（`{"query","k"}`，k 1~20；text 截 300 字） |
| POST | `/api/chat` | **SSE 流式问答**（主入口） |
| POST | `/api/business/reset` | 清空业务运行数据（评测/演示前置） |
| GET | `/api/business/overview` | 台账：全部有效预约与请假单（评测断言数据源） |

错误体统一 `{"detail": "<中文原因>"}`；类型不符统一「请求体不是合法 JSON」（`RequestValidationError` 全局 handler 收口，**不用 pydantic 默认校验体**——PARITY §2.3 的差异决定）。chat 请求校验序列（`_parse_chat_body`，顺序对照 Go 版）：

| 字段 | 约束 | 越界 detail |
| --- | --- | --- |
| `question` | 非空字符串，≤500 字 | 问题不能为空 / 问题过长 |
| `mode` | auto / react(=auto) / classic / direct / research | mode 必须为 auto/direct/research/react/classic（P17 扩 classic；react 与 auto 同路） |
| `role` | student / counselor | role 必须为 student/counselor |
| `session_id` | 字符串 ≤64 字符 | session_id 过长（上限 64 字符） |

## SSE 事件流（十类）

分帧 `data: {json}\n\n`（UTF-8 原文不转义，`separators=(',', ':')` 紧凑 JSON）；响应头 `Cache-Control: no-cache` + `X-Accel-Buffering: no`。事件序（PARITY §3 逐字段契约）：

```text
route（可两段） → [status|step]* → answer_delta* → [截断 status] → citations
      → [slot_question | pending_action → (下一轮) action_result]
      → done（恰一个，reason: completed | max_tokens | error | aborted）
```

| 事件 | 字段 | 说明 |
| --- | --- | --- |
| `route` | route, reason, by_llm, [layer, confidence] | 意图表达。P17 agent 链路**两段式**：guard 出口发 provisional（layer=guard，route 可为 chitchat），收尾按工具轨迹合成 effective（layer=effective）补发——前端徽章覆盖更新、评测 set 聚合兼容；classic 链路单发（layer=L0-rule/L1-llm/L2-main） |
| `status` | text | 阶段提示（先答政策/正在拆解/调用工具…） |
| `step` | index, subquestion, sources[] | 深研逐路进度（sources 为该路 top3 标题去重） |
| `answer_delta` | text | 流式回答增量（前端拼装） |
| `citations` | items[] | 引用列表；**items 恒为数组，空也要 []**（不能 null） |
| `slot_question` | slot, question | 办理追问（slot 为槽位名） |
| `pending_action` | tool, label, args{} | 确认卡（args 为中文 label → 值的有序表） |
| `action_result` | tool, success, message, [receipt] | 执行回执（receipt: VE-XXXX/LV-XXXX） |
| `error` | message | 链路错误（随后必有 done(error)） |
| `done` | latency_ms, [reason] | 一次 chat 恰一个；SSE 端点单点发射 |

三端共用不漂移：前端（`apps/web/lib/api.ts` 手写 SSE 解析）、评测（`eval/run_eval.py` 纯标准库）、服务端（`gewu/agent/events.py` 构造器，字段序对齐 Go struct）。

一次办理类对话的实际事件流（三轮节选）：

```text
data: {"type":"route","route":"transaction","reason":"规则快路径：明确办理指令","by_llm":false,"layer":"L0-rule","confidence":1.0}
data: {"type":"slot_question","slot":"slot","question":"预约哪个时段？可选：08:00-10:00 / …"}
data: {"type":"citations","items":[]}
data: {"type":"done","latency_ms":842,"reason":"completed"}

（下一轮「晚上七点的」）
data: {"type":"status","text":"已更新，请重新确认："}
data: {"type":"pending_action","tool":"book_venue","label":"预约场馆","args":{"场馆":"羽毛球馆","日期":"2026-10-02","时段":"19:00-21:00"}}
data: {"type":"answer_delta","text":"请确认预约场馆信息——…"}
data: {"type":"done","latency_ms":1204,"reason":"completed"}

（下一轮「确认」——resume 桥续跑）
data: {"type":"action_result","tool":"book_venue","success":true,"message":"预约成功：羽毛球馆 2026-10-02 19:00-21:00","receipt":"VE-0003"}
data: {"type":"answer_delta","text":"办理成功：…（凭证号：VE-0003）"}
data: {"type":"done","latency_ms":951,"reason":"completed"}
```

## interrupt/resume 桥

thread 停在确认门（interrupt）时，用户下一条消息**照常 POST /api/chat**：端点 `get_state` 检测到 `snap.next` 非空，按中断载荷分派——agent 链路的 HITL 中断（payload 含 `action_requests`）经 `resume.py` 把用户文本翻译为 decisions（确认→approve / 取消→reject / 修改与切话题→respond，**修改不走 edit**——那会跳过二次确认直接执行）；classic tx_gate 维持 `Command(resume=文本)` 原语义。前端与评测对 interrupt 完全无感知：

```python
run_input = state_input
snap = graph.get_state(config)
if snap.next:
    payload = find_hitl_payload(snap)          # agent HITL：action_requests 在场
    if payload is not None:
        run_input = Command(resume=hitl_decisions(payload, req["question"], llm, business))
    else:                                      # classic tx_gate
        run_input = Command(resume=req["question"])
# 消费：subgraphs=True 冒泡 agent 子图的 custom 事件（P17 必需，否则徽章/状态静默丢失）
for chunk in graph.stream(run_input, config, stream_mode="custom", subgraphs=True):
    yield _sse(chunk[-1] if isinstance(chunk, tuple) else chunk)
vals = graph.get_state(config).values           # 终态侧记（interrupt 悬停轮为当前值）
truncated, answer = bool(vals.get("truncated")), vals.get("answer", "")
```

done 单点也在这个 `generate()` 里：流正常结束 `reason = "max_tokens" if truncated else "completed"`；链路异常先 `error` 再 `done(error)`；客户端断开（GeneratorExit）done 已无法送达（语义上记 aborted）。

## 行为开关（现存）

| 开关 | 取值（缺省在前） | 说明 |
| --- | --- | --- |
| `RERANK_MODE` | on / off | LLM 精排 |
| `QUERY_REWRITE` | on / off | 多轮指代消解补全 |
| `MEMORY_CONSOLIDATE` | on / off | 记忆固化线程（评测隔离用） |
| `RATE_LIMIT_PER_MINUTE` | 600 | 限流（评测建议 600） |

已退役：ROUTER_MODE、SESSION_STORE（checkpointer 接管）、CHUNK_MODE（hierarchical 唯一）、REACT_MODE（P17：react 与 auto 同路，信号词拦截随旧引擎退役）。

## 相关文件

| 文件 | 职责 |
| --- | --- |
| `gewu/api/routes.py` | 5 端点 + 全局校验错误 handler |
| `gewu/api/chat.py` | SSE + resume 桥 + done 单点 + 流后记忆固化 |
| `gewu/agent/events.py` | 事件构造器（items/args 空时必须 []/{} 而非 null） |
| `gewu/agent/resume.py` | HITL resume 翻译（用户文本 → decisions） |
| [PARITY.md](../PARITY.md) | 行为规格（字段级权威） |
| `tests/test_chat_api.py` / `test_search_api.py` | 契约测试 |

---

下一篇《09 · 支撑域与横切面》覆盖配置、成本防线、模型分层、依赖守护与 CI 这些不承载业务语义但决定工程质量的部分。
