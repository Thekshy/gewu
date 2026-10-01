# 架构文档（architecture/）

> 当前形态：**Python + LangGraph 模块化单体**（P14 起）。本系列按「总览 → 编排图 →
> 各领域 → 横切面」组织，每篇聚焦一个架构主题：它是什么、关键代码在哪、为什么
> 这么设计。历史形态（微服务 / Go 单体）见 [walkthrough/](../walkthrough/)（作者
> 讲解，含取舍）与 [ADR/](../ADR/)（单点决策记录）；本文系只讲**现在**。

| # | 主题 | 一句话 |
| --- | --- | --- |
| [01](01-overview.md) | 系统总览 | 定位、系统组成、模块间通信、总架构图、模块地图与依赖规则、目录导览 |
| [02](02-orchestration-graph.md) | 编排主图 | LangGraph StateGraph：节点/条件边/共享状态与两类典型请求的完整生命周期（时序图） |
| [03](03-routing.md) | 意图路由 | cascade 三级级联（规则 → 小模型概率 → 主模型复核）、常量阈值表与误路由安全网 |
| [04](04-rag-retrieval.md) | 混合检索 | FTS + 向量 + RRF + 精排 + 父子块的完整漏斗，读写收口 PG 存储函数（含 DDL 关键代码） |
| [05](05-react-agent.md) | agent 主循环 | create_agent 底座、middleware 栈（guard/截断防御/HITL/压缩）、防护语义平移对照 |
| [06](06-transaction.md) | 知行执行层 | 工具识别、槽位元数据表、interrupt() 确认门、失败恢复与权限矩阵 |
| [07](07-state-persistence.md) | 状态与持久化 | PG checkpointer / 业务与记忆表 / usage.json 各类状态的生命周期与一轮会话的触达图 |
| [08](08-api-contract.md) | 接口契约 | 六端点、SSE 十类事件字段表、实际事件流样例、interrupt/resume 桥与错误体约定 |
| [09](09-cross-cutting.md) | 支撑域 | 配置键表、三层成本防线、模型分层、lint-arch 依赖守护与 CI 双 job |
| [10](10-evaluation.md) | 评测体系 | 数据集样例、运行口径、flaky 判定与方差基线归因法 |
| [11](11-auth.md) | 用户与认证 | 邀请码封闭注册、argon2+cookie 会话、role 服务端权威、三域迁 PG（P21） |

**阅读路径**：新人从 01 → 02 顺读即可建立全景；写论文/找素材按主题直取。
**代码地图**：`apps/server/gewu/`（api/agent/rag/llm/business + 顶层支撑件），
装配入口 `apps/server/main.py`。

**成篇约定**：每篇开头一段导语概述全篇；正文以「图/表 → 代码片段 → 设计决策」
推进，代码路径均相对 `apps/server/`；篇尾附文件一览表与下一篇导航。字段级接口
行为以 [PARITY.md](../PARITY.md) 为唯一规格，本系列不重复其逐字段定义。
