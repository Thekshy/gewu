# 架构文档（architecture/）

> 当前形态：**Python 模块化单体，agent-first 单循环**（P17 起 `mode=auto` 为
> LangChain create_agent + middleware；外壳图与 classic 级联基线为 LangGraph）。
> 本系列按「总览 → 编排 → 各领域 → 横切面」组织，每篇聚焦一个架构主题：它是
> 什么、关键代码在哪、为什么这么设计。历史形态（微服务 / Go 单体）见
> [walkthrough/](../walkthrough/)（作者讲解，含取舍）与 [ADR/](../ADR/)（单点
> 决策记录）；本文系只讲**现在**。

| # | 主题 | 一句话 |
| --- | --- | --- |
| [01](01-overview.md) | 系统总览 | 定位、系统组成、模块间通信、总架构图、模块地图与依赖规则、目录导览 |
| [02](02-orchestration-graph.md) | 编排主图 | 外壳图（mode 分派）+ agent 子图装配（create_agent + 11 件中间件）+ classic 基线，两类典型请求时序 |
| [03](03-routing.md) | 意图路由 | agent 链路的 guard 安检 + effective route 两段式；cascade 三级级联（classic 基线）与误路由安全网 |
| [04](04-rag-retrieval.md) | 混合检索 | FTS + 向量 + 加权 RRF + 复合精排 + 父子块的完整漏斗，入库策略链与 DDL 关键代码 |
| [05](05-react-agent.md) | agent 主循环 | create_agent 底座、middleware 栈（guard/截断防御/HITL/压缩/检索词硬防线）、防护语义平移对照 |
| [06](06-transaction.md) | 知行执行层 | 办理流程注册表（P33 单一真相源）、run_flow/query_flows 统一入口与同构收编判据、闸动态解析、槽位元数据表、HITL 确认门与 resume 桥、回执驱动恢复与权限矩阵 |
| [07](07-state-persistence.md) | 状态与持久化 | PG checkpoints、会话资源化（P22）、业务/记忆/反馈/用量各表的生命周期与一轮会话触达图 |
| [08](08-api-contract.md) | 接口契约 | 27 端点全景（7 路由文件）、十一类 SSE 事件、follow_ups、resume 桥与错误体约定 |
| [09](09-cross-cutting.md) | 支撑域 | 配置键表、四层成本防线、模型分层、四层排障日志、lint-arch 依赖守护与 CI 门禁 |
| [10](10-evaluation.md) | 评测体系 | 五份数据集、双轨口径、检索层独立评测（Recall/MRR/NDCG）、flaky 判定与方差基线归因法 |
| [11](11-auth.md) | 用户与认证 | 邀请码封闭注册、argon2+cookie 会话、role 服务端权威、管理后台与三域迁 PG（P21~P23） |

**阅读路径**：新人从 01 → 02 → 05 顺读即可建立全景（当前默认链路是 agent-first）；写论文/找素材按主题直取，双底座对照素材从 [03](03-routing.md) 与 eval/reports/orchestration-20261001.md 进。
**代码地图**：`apps/server/gewu/`（api/agent/rag/llm/business/auth/session + 顶层支撑件），
装配入口 `apps/server/main.py`。

**成篇约定**：每篇开头一段导语概述全篇；正文以「图/表 → 代码片段 → 设计决策」
推进，代码路径均相对 `apps/server/`；篇尾附文件一览表与下一篇导航。字段级接口
行为以 [PARITY.md](../PARITY.md) 为唯一规格（§0.5~§0.8 为 P21~P25 契约演进
注记），本系列不重复其逐字段定义。
