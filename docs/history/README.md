# history/ —— 历史形态留档

gewu 经历了三次实现形态，本目录存放**已退役形态**的文档遗产；代码级回溯靠
git tag 锚点：`python-final`（v1）→ `pre-ms-removal`（微服务终态）→ `go-final`
（Go 终态）。当前形态（Python + LangGraph）见 [../architecture/](../architecture/README.md)。

| 文件 | 内容 | 形态 |
| --- | --- | --- |
| [PARITY-MS.md](./PARITY-MS.md) | 微服务形态的行为规格（六服务 PARITY） | 微服务（P0~P5） |
| [SERVICES.md](./SERVICES.md) | 六服务清单与职责 | 微服务（P0~P5） |
| [microservices-upgrade-prompt.md](./microservices-upgrade-prompt.md) | 单体→微服务升级的下发提示词（过程留档） | 微服务（P0~P5） |
| [go-notes.md](./go-notes.md) | Go 重写的设计决策与「为什么」（面试问答式） | Go 单体（P8~P13） |

退役决策：微服务 → [ADR-0009](../ADR/0009-微服务退役与单体模块化.md)；
Go → [ADR-0010](../ADR/0010-langgraph-migration.md)。
