# 02 · 编排主图（LangGraph StateGraph）

全部会话编排收敛在一张图（`gewu/agent/graph.py` 的 `build_graph`）：入口分派 →
指代补全 → 路由 → 六条链路 → 终态。图结构即文档——每个节点是一个可单测的函数，
每条条件边是一次显式的分支决策。

## 图结构

```mermaid
flowchart TB
    START((START)) --> EG{entry_gate}
    EG -->|办理收集中| TXR[tx_resume<br/>续轮推进]
    EG -->|新会话| RES[resolve_query<br/>指代补全+记忆装配]
    RES --> RT[route<br/>cascade 级联路由]
    RT -->|factual| RET[retrieve] --> ANS[answer_direct]
    RT -->|refusal| REF[refusal]
    RT -->|research| RES2[research<br/>拆解→多路检索→综合]
    RT -->|transaction| TX[transaction<br/>工具识别→读执行/槽位收集]
    RT -->|hybrid| HY[hybrid<br/>政策先答] --> RET2[retrieve→answer] --> HYT[hybrid_tx] --> TX
    RT -->|react / mode=react| RE[react 子图<br/>见 05]
    ANS --> E((END))
    REF --> E
    RES2 --> E
    TX -->|槽位齐| CONFIRM[tx_confirm<br/>确认摘要] --> GATE[tx_gate<br/>interrupt()]
    TX -->|问槽位/读执行| E
    RE -->|写操作转确认| CONFIRM
    RE -->|最终回答| E
    GATE -->|确认/取消/修改| E
    TXR -->|补齐槽位| CONFIRM
    TXR -->|切话题| RT
```

## 共享状态（ChatState）

图的状态是跨节点传递的唯一媒介（`gewu/agent/state.py`，TypedDict）——**所有
跨节点字段必须显式声明**（LangGraph 会静默丢弃 schema 外的 key，这是 P14 调试
中两度撞上的坑）。三类字段：

- **请求上下文**：question（原话，记忆存档用）/ resolved（补全后，贯通路由与检索）
  / mode / role / user / session_id；
- **回答侧记**：hits / citations / answer / truncated（done.reason=max_tokens 的依据）；
- **办理流程**：tx_tool / tx_phase（collect|confirm）/ tx_slots / tx_last_asked
  ——由 checkpointer 持久化，跨请求、跨进程存活（见 [07](07-state-persistence.md)）。

`thread_id = session_id`：一个会话一个 thread，interrupt 恢复与状态续办都以此为锚。

## 关键设计

**entry_gate（会话优先解释续轮）**。Go 时代 RunChat 的第一条规则原样平移：办理
流程进行中（tx_phase=collect）时，用户消息优先解释为对流程的回应（补槽位/取消/
切话题由 tx_resume 分类处理），不走正常路由。「确认」这类短句因此不会被路由器
当成新问题——这是多轮办理体验正确的关键。

**Command(goto) 跨链跳转**。hybrid 链「先答政策再办业务」用两次跳转实现：
hybrid 节点置 `hybrid_then_tx=True` 后 goto retrieve；answer_direct 的条件边看到
标记则 goto hybrid_tx → transaction。工具未识别时 transaction 也会 goto retrieve
（转知识库兜底）。跳转与条件边并存：**链路内的固定顺序用边，语义分叉用条件边，
跨链复用用 Command**。

**一次「深度研究」的生命周期**（research 节点，对应 PARITY §7）：

1. 指代补全（resolve_query，多轮时）→ 2. cascade 路由判 research →
3. LLM 拆解 2~4 个自包含子问题 → 4. 逐路混合检索，每路 emit 一个 `step` 事件 →
5. 证据按 chunk_id 跨子问题去重，取前 12 条统一编号 → 6. LLM 只依据编号证据
   综合作答（流式 `answer_delta`）→ 7. `citations` + `done`。

**事件发射（custom stream writer）**。节点内经 `gewu/agent/emitter.py` 的 `emit()`
把 PARITY 十类事件送入 custom 流；脱离图运行（单测直调节点）时安全跳过。SSE
契约因此与 Go 版逐字段一致（见 [08](08-api-contract.md)）。

**done 单点**。done 事件（含 reason: completed/max_tokens/error）只在 SSE 端点
（`gewu/api/chat.py`）发射一次——图节点只更新 `truncated` 等侧记，不 emit done。
一次 chat 恰一个 done，与 Go RunChat 单点语义对齐。

## 相关文件

`gewu/agent/graph.py`（主图与全部节点工厂）、`gewu/agent/state.py`、
`gewu/agent/emitter.py`、`gewu/api/chat.py`（stream 消费与 done）。
