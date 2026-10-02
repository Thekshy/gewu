# P30：agent 主循环答案流式化（auto/react 逐字直出）

## 1. 背景与根因

用户反馈：问一个问题后「转圈圈到底，答案一下子全部出来」。排查结论——
前后端**都已是 SSE 流式架构**（前端 `streamChat` getReader 逐事件消费、
后端 `StreamingResponse` 分帧），classic 直答链路也早已逐 delta 发词
（`answer_direct` 走 `LLMService.chat_stream`）。

真正的短板在 **auto/react 模式（默认）**：create_agent 子图内模型调用是
同步 invoke，整段答案在 `agent_done` 节点一次性 `emit(answer_evt)`。
转圈时长 = guard（1~2s）+ 工具循环 + 最终轮整段生成；然后一帧全文弹出。
对齐参照：WeKnora 的 stream-to-SSE 体验（= classic 链路已有形态）。

## 2. 方案：StreamingAnswerMiddleware（流式代理模型）

中间件栈最内层加 `StreamingAnswerMiddleware`（mw.py）：不自己裸调
`request.model.stream()`——`ModelRequest.model` 是**未绑定 tools 的原始
模型**（tools 在 `.tools` 字段、system message 单独在 `.system_message`，
已查证 langchain 1.x types.py），裸调会丢工具绑定。改为把 `request.model`
换成 `_StreamingChatModel` 流式代理再交回官方 handler：

- 代理 `_generate` 内部跑 `inner.stream()`，文本增量 `emit(answer_delta)`，
  `AIMessageChunk` 逐块 `+` 聚合后当一条 AIMessage 返回；
- bind_tools / tool_choice / system message / model_settings 由官方 handler
  拼装后落到代理——**调用细节零漂移**；
- 聚合消息保留 usage_metadata / finish_reason / tool_calls → 外层
  UsageRecord / TruncationDefense / ModelCallLimit 全链路无感；
- `wrap_model_call` 允许直接返回 AIMessage（自动转 ModelResponse）。

### 位置纪律：栈列表最末（SummarizationMiddleware 之后）= wrap 最内层

- Summarization 压缩后的 messages 才进流式；其内部摘要小模型调用不经
  handler 链，不会误发 delta。

### 中间轮「打脸」与防重

- 中间轮模型先说话再调工具：delta 已发 → 聚合出 tool_calls → emit
  **`answer_reset`**（新事件，形状只做加法）→ 前端清空已显示文本并转存
  一条 step（思考轨迹行）；
- 最终轮文本经 `after_model` 写 `state.answer_streamed`；`agent_done`
  **等价校验**（`streamed.strip() != answer.strip()` 才 emit 全文）防重发；
- 轮起 `agent_in` 清零 `answer_streamed`（P26 citations 跨轮污染同款防御）。

### 边界 case 一览

| 场景 | 行为 |
|---|---|
| 中间轮先说话再调工具 | answer_reset 撤回 → 前端转 step |
| 写工具轮（HITL 中断） | 过渡文本 reset 清空 → PendingAction 摘要正常显示 |
| guard block/meta 短路 | 不进 model 节点 → 标志未设 → agent_done 照发拒答文案 |
| 轮次耗尽（Model call limits exceeded） | 标志误设但文本不等价 → partial_answer 照发 |
| 截断防御重调 | 重调同走代理流式；带 tool_calls 轮不设标志 |
| 上一轮残留 | agent_in 每轮清零 |

## 3. 改动清单

后端（apps/server）：
- `gewu/agent/mw.py`：`StreamingAnswerMiddleware` + `_StreamingChatModel`
  代理 + `ai_content_text`（防重两端统一口径）；
- `gewu/agent/events.py`：`answer_reset_evt`；
- `gewu/agent/state.py` + `mw.py GewuAgentState`：`answer_streamed` 字段
  （子图与外壳 schema 同名接住）；
- `gewu/agent/graph.py`：`agent_in` 清零 + status 加餐（「正在理解问题…」，
  缓解 guard 期间空转圈）；`agent_done` 等价校验防重；
- `gewu/agent/agent.py`：栈末条件装配；
- `gewu/config.py`：`STREAM_ANSWER`（默认 on；=0 紧急回退单帧全文）。

前端（apps/web）：
- `app/page.tsx` / `app/compare/page.tsx`：各加 `answer_reset` case
  （已显示文本转 step 后清空）。逐字渲染/光标/markdown 容错全复用现有链路；
- **`app/api/[...path]/route.ts`（新增）**：/api 透传 Route Handler，替代
  next.config rewrites（代理层缓冲 SSE 的修复，详见 §6）；
- `next.config.mjs`：rewrites 退役。

测试：
- `tests/agent_fakes.py`：`FakeStreamChunksModel`（按轮吐 chunk 流）+
  `FakeStreamAgentLLM`；
- `tests/test_agent_stream.py`：多帧直出 / reset / 无全文重发帧 / usage
  聚合存活 / 开关回退单帧 / 写工具轮 resume / 跨轮无残留。

## 4. 非目标

- 不改既有 SSE 事件形状与校验序列（只新增 answer_reset）；
- 不动 classic/research 链路（已流式）；
- 不做「中间轮思考」的独立展示通道（reset 转 step 即可）。

## 5. 执行记录

- 全量测试：P30 相关套件（agent 系 53 + PG 系 46 + 收紧后复跑 45）全绿；
  全量两轮各余 3F/1E 与 12F/8E 均已定位——前者为 brew PG 单实例上全量
  并发的连接池竞争（涉及 memory/search/sessions/rate-limit，与 P30 无关，
  逐个单独重跑 4/4 通过），后者为旧代码 stream 契约 bug（已修）+ 清锁误伤；
  ruff/format 全绿；前端 tsc --noEmit 与生产 build（含 /api/[...path]）通过。
  （`make test` 走 docker 5433 独立实例，无此并发竞争。）
- 撞坑一（langchain 1.x 契约）：`BaseChatModel.stream` 直接 yield
  **AIMessageChunk（消息本身）**而非带 `.message` 的 GenerationChunk——第一版
  代理写 `gen.message` 在真实模型/默认 stream 路径上 AttributeError；自写
  fake 包了层壳恰好掩盖（测试绿真跑挂），fake 必须对齐真实契约。
- 撞坑二：本地 brew PG 上连跑多个 PG 消费者（pytest×N + uvicorn）+ 中途
  kill，会留下持 advisory lock 的 idle 会话（gewu_test 库），后续套件在
  首个 PG 用例上排队假死——`pg_terminate_backend` 清残留后恢复（与 P30
  代码无关）。
- fake 注入注意：pydantic 模型字段在构造时复制列表（`rounds`/`responses`），
  HITL 测试的续轮脚本必须预置，不能事后 append。
- 事件打磨：纯 tool_calls 轮（零文本）不发 answer_reset（emitted 标志），
  避免无 delta 先行的空撤回。
- 真跑留档：见 §6。

## 6. 真跑验证（auto 模式，2026-10-02）

**后端事件流（curl 直连 8000）**：
- 「转专业条件」：259 帧 answer_delta 逐 token + answer_reset×1（中间轮
  过渡文本撤回）+ 无全文重发帧 + citations/done/follow_ups 齐全；
- 「体育馆开放时间」时间线：status → reset → status → delta×N → reset →
  status → action_result → delta×N → route → citations → done → follow_ups；
- `STREAM_ANSWER=0` 对照：1 帧、全文帧（= 改前行为），回滚开关有效。

**前端链路（浏览器实测）**：
- 会话内 DOM 采样：guard ~10s 稳定 → 2633→2702→2758→2881→2919 连续
  递增（逐字渲染实证）→ 12.6s 完整回合；「正在理解问题…」状态行在
  首包前可见（status 加餐生效）；
- 截图：`assets/p30-streaming-mid.jpg`。

**第二大发现——Next rewrites 代理缓冲 SSE（前端侧另一半根因）**：
即使后端逐帧发，经 `next.config rewrites` 的代理层到达浏览器也是整段一坨
（Next 15.3.3 实测 dev 与 `next start` 都是 reads=1、spread=0；P21 的
「透传验证」显然只断言了完整性没断言中间分布）。**修法**：P21 预留退路
转正——新增 `apps/web/app/api/[...path]/route.ts` Route Handler 直转上游
body（ReadableStream 管道 + set-cookie 逐条重放），rewrites 退役。修复后
浏览器侧 reads=92、首包 16ms、delta 从 13.6s 起渐进 2.3s。
（线上 Caddy 直接反代 /api→8000 不经过 next，本修复主要惠及 dev 与
单容器部署形态。）
