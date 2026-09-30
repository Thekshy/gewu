# 01 · 系统总览

「格物」= 高校场景的校园制度智能问答与业务执行 Agent：事实问题走 RAG 直答、
复合政策问题走 Deep Research、办理诉求走知行执行层（槽位 → 确认 → 执行 → 回执）、
范围外问题礼貌拒答——全部收敛在同一条 SSE 事件流下，前端与评测共用同一契约。

## 技术栈

| 层 | 选型 | 说明 |
| --- | --- | --- |
| 编排 | **LangGraph**（StateGraph + interrupt + checkpointer） | P14 起核心；主图见 [02](02-orchestration-graph.md) |
| 接口 | FastAPI + uvicorn | :8000，SSE 流式；装配工厂 `gewu/api/app.py` |
| 模型 | langchain-openai（glm-5.3 / glm-5.3-flash 双模型） | OpenAI 兼容协议，换端点即换供应商 |
| 向量/关键词 | PostgreSQL + pgvector（halfvec HNSW + tsvector GIN） | 读写收口存储函数，见 [04](04-rag-retrieval.md) |
| 会话持久化 | LangGraph PostgresSaver | 同一 PG 实例，checkpoint 表族 |
| 业务/记忆 | SQLite（business.db / memory.db） | mock 业务与长期记忆，量级未触发迁移 |
| 工具链 | uv + ruff + pytest | CI 双 job（server + web） |

## 总架构图

```mermaid
flowchart TB
    subgraph Web[apps/web · Next.js]
        UI[聊天 / 对比实验台 / 控制台]
    end
    subgraph Server[apps/server · FastAPI :8000]
        MW[中间件：限流 · X-Trace-Id]
        API[api 层：chat SSE / search / docs / business / health]
        GRAPH[agent 编排：LangGraph 主图]
    end
    subgraph Domains[gewu/]
        ROUTE[routing 级联路由]
        RAG[rag 混合检索]
        REACT[react 子图]
        TX[tx 知行执行层]
        LLM[llm 模型访问]
        BIZ[business mock 业务]
        MEM[memory 长期记忆]
    end
    PG[(PostgreSQL<br/>docs/chunks/vectors<br/>+ checkpoints)]
    SQ[(SQLite<br/>business.db · memory.db)]
    EXT[[LLM / Embedding API<br/>OpenAI 兼容]]

    UI -->|HTTP/SSE| MW --> API --> GRAPH
    GRAPH --> ROUTE & REACT & TX & MEM
    ROUTE & REACT & TX --> LLM --> EXT
    GRAPH --> RAG --> PG
    GRAPH -.checkpointer.-> PG
    TX --> BIZ --> SQ
    MEM --> SQ
```

## 模块地图与依赖规则

| 域 | 位置 | 职责（一句话） |
| --- | --- | --- |
| 接口 | `gewu/api/` | 6 端点 + SSE 写出 + interrupt/resume 桥；只做 HTTP 语义 |
| 编排 | `gewu/agent/` | 主图（graph.py）、ReAct 子图（react.py）、路由（routing.py）、执行层（tx.py）、深研（research.py）、工具权限（tools.py） |
| 检索 | `gewu/rag/` | 混合检索管线（retrieve.py）、PG 存取（store.py）、DDL 权威（schema.py） |
| 模型访问 | `gewu/llm/` | ChatOpenAI 工厂 + 自定义 Embeddings + 流式/工具调用封装 |
| 业务 | `gewu/business/` | mock 场馆预约 + 请假审批（对角色无感知） |
| 支撑 | `gewu/` 顶层 | config / budget / memory / middleware / dates / jsonx |

**依赖规则**（`make lint-arch`，grep 断言零依赖守护）：

1. `agent → rag/llm/business`：经注入接口（Retriever / LLMService / tools 表）；
2. `rag`、`llm`、`business` ↛ `agent`（反向禁止）；三者之间禁止横向 import；
3. `business` 只经 `agent.tools`（权限矩阵单一出口）被触达；
4. 支撑域（budget/memory/middleware）不 import 业务域；
5. `main.py` 只做装配（仅 import `gewu.api` / `gewu.config`；`app.py` 是可测试
   的装配工厂，等价于 Go 时代的 `cmd/server/main.go`，享有豁免）。

## 为什么是模块化单体

微服务形态（P0~P5）与单体（P8 起）在这个项目里做过完整的实践对比：26/26 逐题
PARITY 迁移到六服务再退役回来，结论是**当前量级下单体 + 清晰依赖规则**是正确
形态——进程内函数调用替代 RPC，边界靠 lint 守护而不是网络。历史证据：
[ADR-0009](../ADR/0009-微服务退役与单体模块化.md)、[docs/history/](../history/)、
tag `pre-ms-removal`。
