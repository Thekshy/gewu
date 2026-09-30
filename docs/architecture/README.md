# 架构文档（architecture/）

> 当前形态：**Python + LangGraph 模块化单体**（P14 起）。本系列按「总览 → 编排图 →
> 各领域 → 横切面」组织，每篇聚焦一个架构主题：它是什么、关键代码在哪、为什么
> 这么设计。历史形态（微服务 / Go 单体）见 [walkthrough/](../walkthrough/)（作者
> 讲解，含取舍）与 [ADR/](../ADR/)（单点决策记录）；本文系只讲**现在**。

| # | 主题 | 一句话 |
| --- | --- | --- |
| [01](01-overview.md) | 系统总览 | 定位、技术栈、总架构图、模块地图与依赖规则 |
| [02](02-orchestration-graph.md) | 编排主图 | LangGraph StateGraph：节点/条件边/共享状态与一次问答的生命周期 |
| [03](03-routing.md) | 意图路由 | cascade 三级级联（规则 → 小模型概率 → 主模型复核）与误路由安全网 |
| [04](04-rag-retrieval.md) | 混合检索 | FTS + 向量 + RRF + 精排 + 父子块，读写收口 PG 存储函数 |
| [05](05-react-agent.md) | ReAct 子图 | 原生 tool-calling 循环、截断防御与指纹去重 |
| [06](06-transaction.md) | 知行执行层 | 槽位收集、interrupt() 确认门、失败恢复与权限矩阵 |
| [07](07-state-persistence.md) | 状态与持久化 | PG checkpointer / SQLite / usage.json 三类状态的生命周期 |
| [08](08-api-contract.md) | 接口契约 | 六端点、SSE 十类事件、interrupt/resume 桥与错误体约定 |
| [09](09-cross-cutting.md) | 支撑域 | 配置、预算与限流、模型分层、依赖守护与 CI |
| [10](10-evaluation.md) | 评测体系 | 数据集、评测口径、flaky 判定与方差基线归因法 |

**阅读路径**：新人从 01 → 02 顺读即可建立全景；写论文/找素材按主题直取。
**代码地图**：`apps/server/gewu/`（api/agent/rag/llm/business + 顶层支撑件），
装配入口 `apps/server/main.py`。
