# docs/ 文档地图

> 快速导航：想找什么 → 去哪。各子目录内部有自己的索引。

| 想看什么 | 去哪 |
| --- | --- |
| **架构（当前形态：agent-first 单循环 + LangGraph 外壳/基线）**：总览 / 编排 / 各领域 / 横切 | [architecture/](./architecture/README.md)（01~11 编号系列） |
| **行为规格**：API/SSE 事件契约（跨实现迁移的唯一标准） | [PARITY.md](./PARITY.md)（§0.5~§0.8 为 P21~P25 演进注记） |
| **单点设计决策**（10 篇，含 LangGraph 迁移） | [ADR/](./ADR/) |
| **作者讲解系列**：为什么这么设计、取舍与边界（Go 时代，决策原样平移） | [walkthrough/](./walkthrough/README.md)（01~10 编号系列） |
| **任务执行留档**：P 系列任务书（Grilling→SPEC→ticket→执行记录，全真跑） | [runbooks/](./runbooks/)：P6 主流升级 · P6 本地真跑 · P7 审查修复 · P8 微服务退役 · P10 finish_reason · P11 前端 · P12 存储迁 PG · P14 LangGraph 迁移 · P15 RAG 对齐 WeKnora · P16 前端 UI 现代化 · P17 agent-first 编排 · P18~P20 设计语言（Claude 风→Operate 化） · P21 用户体系 · P22 会话与记忆 · P23 管理后台 · P24 观测与检索调优 · P25 对话体验 |
| **竞品深研**：8 个开源 Agent 项目源码级对照报告 | [research/](./research/agent-projects-report.md)（含研究提示词） |
| **历史形态留档**：微服务时代（PARITY-MS/SERVICES）+ Go 时代（go-notes/微服务升级提示词） | [history/](./history/)；形态锚点 git tag `python-final` → `pre-ms-removal` → `go-final` |
| **路线图与进行中实验** | [roadmap.md](./roadmap.md) |
