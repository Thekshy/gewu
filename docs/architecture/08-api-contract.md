# 08 · 接口契约（API 与 SSE 事件流）

接口层的唯一行为规格是 **[PARITY.md](../PARITY.md)**——它是这个项目跨实现迁移
（python-final → go-final → P14 LangGraph 版）三次仍保持前端/评测零改动的关键。
本文只做导读，字段级细节以 PARITY 为准。

## 六端点

| 方法 | 路径 | 职责 |
| --- | --- | --- |
| GET | `/api/health` | 健康：docs/chunks 计数、llm/embeddings 就绪、预算用量 |
| GET | `/api/docs` | 已入库文档列表（doc_id 升序） |
| POST | `/api/search` | 调试用混合检索（text 截 300 字） |
| POST | `/api/chat` | **SSE 流式问答**（主入口） |
| POST | `/api/business/reset` | 清空业务运行数据（评测/演示前置） |
| GET | `/api/business/overview` | 台账：全部有效预约与请假单 |

校验语义（问题非空/≤500 字、mode ∈ auto/direct/research/react、role ∈
student/counselor、session_id ≤64）：422 错误体统一 `{"detail": "<中文原因>"}`；
类型不符统一「请求体不是合法 JSON」（全局 exception handler 收口，不用 pydantic
默认校验体）。

## SSE 事件流（十类）

分帧 `data: {json}\n\n`，UTF-8 原文不转义；响应头 `Cache-Control: no-cache` +
`X-Accel-Buffering: no`。事件序（PARITY §3 逐字段契约）：

```
route → [status|step]* → answer_delta* → [截断 status] → citations
      → [slot_question | pending_action → (下一轮) action_result]
      → done（恰一个，reason: completed | max_tokens | error | aborted）
```

- **route**：路由决策（含 layer/confidence，级联层级可观测）；
- **step / status**：深研子问题进度 / 阶段提示；
- **answer_delta**：流式回答增量（前端拼装）；
- **citations**：引用列表（items 恒为数组，空也要 []）；
- **slot_question / pending_action / action_result**：办理三件套（追问/确认卡/回执，
  见 [06](06-transaction.md)）；
- **done**：一次 chat 恰一个，SSE 端点单点发射。

三端共用不漂移：前端（`apps/web/lib/api.ts` 手写 SSE 解析）、评测
（`eval/run_eval.py` 纯标准库）、服务端（`gewu/agent/events.py` 构造器）。

## interrupt/resume 桥

thread 停在确认门（interrupt）时，用户下一条消息**照常 POST /api/chat**：
端点 `get_state` 检测到 `snap.next` 非空 → 以 `Command(resume=question)` 续跑，
tx_gate 拿到 resume 值分类处理。前端与评测对 interrupt 完全无感知。

## 行为开关（现存）

| 开关 | 取值（缺省在前） | 说明 |
| --- | --- | --- |
| `RERANK_MODE` | on / off | LLM 精排 |
| `REACT_MODE` | off / on | 路径不定的办理问题自动转 ReAct |
| `QUERY_REWRITE` | on / off | 多轮指代消解补全 |
| `MEMORY_CONSOLIDATE` | on / off | 记忆固化线程（评测隔离用） |
| `RATE_LIMIT_PER_MINUTE` | 600 | 限流（评测建议 600） |

已退役：ROUTER_MODE（cascade 唯一）、SESSION_STORE（checkpointer 接管）、
CHUNK_MODE（hierarchical 唯一，flat 为冻结基线不再可切）。

## 相关文件

`gewu/api/routes.py`（5 端点）、`gewu/api/chat.py`（SSE + resume 桥 + done 单点）、
`gewu/agent/events.py`（事件构造器）；契约测试 `tests/test_chat_api.py` /
`tests/test_search_api.py`；行为规格 [PARITY.md](../PARITY.md)。
