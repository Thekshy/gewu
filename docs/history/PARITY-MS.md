# PARITY-MS —— 微服务版与冻结单体的有意行为差异清单

> 冻结单体：`tag go-monolith`（commit `96c5a18`）。行为唯一标准仍是
> [PARITY.md](../PARITY.md)；本文只登记微服务部署形态下**有意**的差异与理由，
> 每条给出影响面与验证方式。评测覆盖不到的路径风险另见
> [SERVICES.md §12](./SERVICES.md)。
>
> 阶段推进中逐条落档；迁移完成后与冻结单体做 26 题逐题对照（P4）。

## 差异清单

| # | 差异 | 理由 | 影响面 | 验证 | 引入阶段 |
| --- | --- | --- | --- | --- | --- |
| 1 | **会话状态服务重启后保留**（单体进程内 map，重启即失；conversation 服务 PG 持久化） | 架构升级目标之一 | 仅影响「重启后 30 分钟内继续办理」——单体此场景直接丢流程，微服务可续；评测不覆盖重启 | TestPGStoreRestartRetention + 实机重启续办（P2 验收记录） | P2 |
| 2 | **消息落库（tx_messages 表，新增）** | 审计/前端展示预留 | 不参与答案生成，各轮独立作答不变（26 题前提） | 单测 + SQL 抽查 | P2 |
| 3 | **每日 token 预算持久化从 `data/usage.json` 迁 PG（budget_usage 表）** | 计量集中到 generate，跨进程原子累计 | `usage.json` 不再更新（单体入口仍用它）；`/api/health` 的 budget 口径不变（UTC 滚动、流式字符/2 下限 1、中断入账） | provider 计量单测 + health 对照 | P2 |
| 4 | **POSTGRES_DSN/REDIS_ADDR 未配置时的降级模式**（conversation/generate 内存计量、agent_config 内存） | 本机开发与无依赖演示的可用性 | 降级语义：会话/计量重启清零（等同单体的进程内行为）；compose/生产部署均配置 DSN 不触发 | 日志 WARN 可见 | P2 |
| 5 | **新增 `/admin/*` 命名空间**（GET/PUT /admin/config → agent_config 管理） | 配置化演示 | 不触碰既有 `/api/*`；评测与前端零依赖 | e2e 实测（P2 验收记录） | P2 |
| 6 | **错误事件内部文案为 Go/gRPC 错误串**（如 `rpc error: code = ...`） | 实现语言与传输层差异 | 仅调试可见的内部错误路径（PARITY §18.2 同级差异，评测无 error 事件用例） | 单测文案断言的是契约错误，非内部错误 | P1 |
| 7 | **极罕见路径：orchestrator 中途崩溃时 gateway 合成 error+done 事件的 latency_ms 为网关侧近似值** | gRPC 流中断的兜底 | 正常编排自身兜底的 done.latency_ms 仍为编排入口口径（决策 B） | 代码审查 | P1 |
| 8 | **generate 重试：仅 TCP 拨号失败重试 1 次，流式不重试**（单体零重试） | 故障路径鲁棒性（ADR 备案） | 正常路径零影响；拨号失败场景延迟 +1 次尝试 | isDialFailure 单测 | P1 |
| 9 | **摄入流水线异步化（Redis Streams）+ 异步上传 API**；`make ingest` 保持阻塞语义 | 流水线解耦 | CLI 行为不变（发布→等回执→超时报错） | P3 验收 | P3（预登记） |
| 10 | **业务系统 SQLite→PostgreSQL（tool 服务宿主）**；序列语义一致（DELETE 不复位） | 存储统一 | 单号继续增长与 SQLite AUTOINCREMENT 一致；评测断言不含单号字面值（已核对 dataset） | P4 A/B | P4（预登记） |
| 11 | **向量存储暴力余弦→Milvus FLAT（IP + L2 归一化 = 余弦）**；pgvector 降级 | 基础设施升级 | 数学等价；平局次序需 rag 服务端 (score, chunk_id) 稳定化重排 | P3 /api/search 逐位对照 | P3（预登记） |
| 12 | **conversation 历史不接入答案生成（仅审计）**；记忆 Recall/Put 不接入生成 | 26/26 行为前提，接入与否迁移完单独评估（ADR-0005） | 无（预留能力） | 26 题全量复跑 | P2/P5 |
| 13 | **检索 RPC 失败时编排按「无命中」降级**（单体语义：Search 错误中止整轮→error 事件） | 基础设施抖动不应放大为整轮失败；评测不覆盖 rag 宕机场景 | 仅 rag 不可用时的问答轮：NO_DATA 而非 error 事件（SERVICES §12 风险表备案） | 日志可见 + 单测 | P3 |
| 14 | **摄入异步化（Redis Streams）与异步上传 API**；`make ingest-ms` 保持阻塞语义（发布→回执→超时报错），单体 `make ingest`（同步直写 SQLite）保留为 A/B 路径 | 流水线解耦（ADR-0003） | CLI 终态语义一致；内部路径不同 | ingest-ms 实跑 + 逐位 A/B | P3 |
| 15 | **查询改写的缓存为 rag 服务进程内**（单体重试/重启后缓存丢失同理）；key 状态经 BudgetStatus 惰性同步 | 单实例部署语义等价 | 无可观察差异（改写结果不进事件流） | 代码审查 | P3 |

## 已知等价说明（不构成差异，备案备查）

- `done.latency_ms` 的 t0 = 编排入口（决策 B），与单体 RunChat 口径一致；gRPC 往返不计入。
- 零 key 演示模式全链路存活：路由/意图/抽槽走启发式，直答/深研为空库语义（P3 接检索后恢复演示文案）。
- 422/429 文案、SSE 帧编码、事件序、确认摘要有序 args、权限矩阵文案：逐字复刻，golden 单测锁定。
