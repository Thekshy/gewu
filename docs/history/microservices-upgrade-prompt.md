# 任务：gewu 微服务架构升级（单体 Go → 1 网关 + 5 gRPC 服务，行为不变）

> 用法：把本文件整段投给一个新会话的 coding agent，按阶段执行。
> 每阶段过验收门才进下一阶段；发现本规格与现状冲突，停下来问我，不自行改行为。

## 背景与目标

本仓库现有实现为 **Go 单体**：gin + 纯 Go SQLite，`cmd/server` 入口 +
`internal/{agent,budget,business,config,dates,llm,middleware,rag}`（约 6k 行）。
核心能力 = 五分类意图路由（factual/research/transaction/hybrid/refusal）+
混合检索（BM25 字符二元语法 + 向量余弦 + RRF）+ Deep Research（拆解→多路检索→交叉综合）+
知行执行层（槽位收集/确认流/工具调用/mock 业务系统）+ 成本防线（限流+每日 token 预算）+
26 题离线评测（当前与冻结基线同为 26/26）。

现在在**同一仓库、同一 Go module** 内，把这套行为升级部署为
「1 个 HTTP 网关 + 5 个 gRPC 微服务」。目的：架构升级 + 工程展示。

**铁律：**

- 行为唯一标准 = [docs/PARITY.md](../PARITY.md) + 冻结单体 + 26 题评测（[eval/run_eval.py](../../eval/run_eval.py)，HTTP/SSE 契约）。
- 迁移开始前（P0 第一个动作）给当前单体打 tag `go-monolith` 并冻结：
  `internal/` 作为共享库被各服务复用，默认只读（确需改动逐条记录原因与影响）；
  `cmd/server` 保持可构建可运行，作为 A/B 对照与回退。
- 迁移期间**不并入** [docs/agent-projects-report.md](../research/agent-projects-report.md) 的改进项
  （打断恢复、并行检索、引用后处理等）——单移动基线，迁移完再议。
- 评测客户端 `eval/run_eval.py` 与前端 `apps/web` **零改动**（它们只依赖 HTTP/SSE 契约）。
- 零 key 演示模式（无 LLM_API_KEY 全链路可跑，启发式降级）必须完整存活——它是无密钥 CI 冒烟的底座。

## 第一步（必须先做，做完停下来给我看）

通读现有单体与规格，写 `docs/SERVICES.md` 服务拆分映射：

- `internal/` 每个包 → 归属哪个服务 / 以何种方式复用（import 共享 or 拷贝）
- 五分类路由：类别定义、LLM 判定（json_mode/temp/max_tokens）、无 key 与失败时的
  启发式降级全分支（PARITY §5）
- 检索管线各环节与参数：查询改写（LLM small + 进程缓存）、BM25（字符二元语法、
  k1/b/idf 公式、k*2 召回）、向量暴力余弦（L2 归一化）、RRF 融合（PARITY §7.5~7.7）
- 业务系统表结构、每个工具的校验顺序与错误文案、单号/时区/种子数据（PARITY §8）；
  工具层权限矩阵与越权文案（PARITY §10）
- 预算计量口径（流式按字符/2、UTC 日期滚动、入口 429 文案）与限流参数（PARITY §12）
- 26 题评测的运行方式与指标口径（PARITY §17），三份基线报告的对照条件
  （[baseline-python.md](../../eval/reports/baseline-python.md)、
  [rewrite-go-vs-python.md](../../eval/reports/rewrite-go-vs-python.md)）
- 本提示词架构规格与现状的冲突清单——发现冲突停下来问我，不要自行改行为

## 架构规格（六进程，单 module 多 cmd）

- **gateway**（gin，对外 :8000，端口/路径/SSE 帧格式与现单体一致）：
  SSE 流式透传、按 IP 令牌桶限流（可迁 Redis）、身份上下文注入
  （role → `demo-{role}` 服务端派生，**不信任客户端传 user**）；
  `/admin/*`（新增命名空间，配置管理）转发 orchestrator。
  **不做真实认证**——现状是公开 demo（PARITY §1/§2.4），加认证属行为变更，
  如需另立 ADR 且默认关。
- **orchestrator**：编排管线（PARITY §4 逐字迁移：续轮意图→路由→五分支分发）+
  五分类路由 + 工具调度（现状全串行，保持串行；不引入 ReAct/Function Calling 循环——
  现设计是「一次识别 + 确定性状态机」，这是行为不是实现细节）；
  自有 `agent_config` 表（系统提示词/工具集/模型参数；**默认行 = 现
  internal/agent/prompts.go 逐字快照**），读走 Redis 缓存，热路径零配置 RPC。
- **conversation**：办理会话状态（phase/slots/last_asked，TTL 30 分钟惰性清理，
  语义按 PARITY §12.3 逐字）+ 消息落库（新增，仅审计/前端展示）；
  **上下文组装 API 预留但默认不接入答案生成**——现行为各轮独立作答，
  接入历史会改变答案内容，26 题必挂。接入与否迁移完单独评估。
- **generate**：LLM 网关。provider 接口（同步/流式两方法，取消走 context）+ 工厂，
  OpenAI 兼容协议多 provider（PARITY §13 全参数复刻：json_mode、
  `thinking:{"type":"disabled"}`、temperature/max_tokens 缺省、主/flash 模型分层）；
  **每日 token 预算计量集中在此服务**（流式按字符/2、embedding 按用量，
  PG/Redis 持久化、UTC 滚动、原子累计），orchestrator 在 chat 入口预检
  （耗尽 → 429 `{"detail":"今日 token 预算已用尽…"}` 文案逐字）；
  超时/网络错误向上抛，**降级决策留在调用方**（orchestrator/rag，与现状一致）；
  重试默认仅「连接失败且请求未发出」重试 1 次，流式不自动重试
  （防降级链路与延迟变形，ADR 记录）。
- **tool**：工具注册中心 + 执行器 + 权限矩阵（PARITY §10：角色可见性、越权/缺参/未知
  工具文案逐字；敏感参数 `user` 由服务端从身份注入，模型与客户端不可传）+
  **mock 业务系统宿主**：venues/bookings/leave_tickets 由 SQLite 迁 PostgreSQL，
  PARITY §8 逐字复刻（校验顺序、错误文案、单号 `VE-{id:04d}`/`LV-{id:04d}`、
  created_at 中国时区秒精度、种子场馆、reset 语义）；
  `/api/business/reset|overview` 由 gateway 转发至此（评测断言依赖）。
- **rag**：混合检索 + 摄入 + 长期记忆（P5）。knowledge/memory 两组 API
  （检索/摄入、Recall/Put），共用 embedding 管线，知识/记忆 collection 分离；
  检索实现**复用 internal/rag**（tokenize/BM25/RRF/截断参数原样，行为对齐靠同一份代码）；
  查询改写（LLM small + 进程内缓存）留在 rag 服务内部，LLM 调用走 generate；
  向量存储走接口：默认 Milvus standalone（**FLAT 精确索引**，L2 归一化 + IP 度量
  = 余弦，保证与现暴力余弦结果一致），pgvector 为降级实现（ADR）。

## 技术栈约定

- Go 1.25，沿用根 module `gewu`（单 go.mod 多 cmd）：
  `cmd/{gateway,orchestrator,conversation,generate,tool,rag}/` 薄入口 +
  `internal/services/<svc>/` 实现 + `internal/`（冻结共享库）+
  `proto/`（buf 生成到 `pkg/gen`）。
- 不用任何 Agent 框架（langchaingo 等）；编排沿用现管线结构。
- 流式：客户端 SSE ← gateway ← orchestrator（gRPC server-streaming）← generate ← provider。
  SSE 帧 `data: {JSON}\n\n`、JSON 不转义 UTF-8/HTML、`Cache-Control: no-cache` +
  `X-Accel-Buffering: no`、事件序与 `done.latency_ms` 口径与现单体一致。
  取消：context 全链路贯通，客户端断开 → provider HTTP 请求取消（不烧 token，
  这是 README 宣示的现网优势，不能在拆分中丢失）。
- 存储：PostgreSQL（业务/会话/配置/chunk 元数据）、Milvus standalone（向量；
  FLAT；资源受限降级 pgvector，ADR）、Redis（配置缓存/限流/预算/MQ）。
- 摄入流水线：上传→解析→切分→embedding→入库 走 Redis Streams（consumer group）；
  `make ingest` CLI 保持**阻塞语义**（发布后等待完成回执，超时报错）——
  评测与 A/B 对照需要确定性的索引状态；异步上传 API 是新增演示面。
  embed 失败一次 → 本批全程降级仅 BM25（PARITY §15 的降级语义保留）。
- 观测：zap 结构化日志 + trace-id 中间件贯穿全链路；密钥只走环境变量，
  `.env.example` 扩充服务端口/DSN/MQ 配置。
- CI：六服务构建 + buf lint + `go test ./...` 必绿；零 key 冒烟
  （办理类用例 + `/api/search` A/B）无密钥可跑。

## 分阶段（每阶段过验收门才进下一阶段，每阶段一个 commit，commit message 注明阶段与验收结果）

- **P0 脚手架**：打 `go-monolith` tag；目录/proto+buf/docker-compose（pg/redis/milvus
  及其依赖，或 profile）/Makefile/CI 扩展/trace-id 基座。
  验收：buf lint + generate 通过；六个空服务 compose 起来；make build/test 绿；
  单体 `cmd/server` 仍可构建运行。
- **P1 最小闭环**：gateway SSE → orchestrator 裸管线 → generate（1 provider）流式回传。
  验收：curl 打 `/api/chat` 收到流式 `answer_delta`；SSE 帧编码（前缀/换行/
  不转义/逐帧 flush）与现单体 golden 用例一致；客户端断开后 provider 请求取消
  （以日志与预算增量佐证）。
- **P2 状态与配置**：conversation 服务 + `agent_config` 表 + Redis 缓存。
  验收：办理会话状态语义迁移后，现有事务链路测试改造全绿（零 key 确定性：
  collect/confirm/修改/取消/冲突恢复序列）+ 跨进程集成测试；
  `agent_config` 默认行与 prompts.go 逐字一致；服务重启后会话保留
  （记为有意差异，入 PARITY-MS.md）。
- **P3 RAG**：摄入流水线（Redis Streams + 阻塞 CLI）+ 混合检索接入
  direct/research 链路；`/api/search`、`/api/docs` 上线。
  验收：同语料同固定查询集（26 题问题 + 补充政策词查询，集入库 eval/），
  `/api/search` top-k 结果与冻结单体**逐位一致**（BM25-only 与带向量两种索引各跑）；
  26 题中 factual 7 + multi_hop 8 + refusal 3 全过（有 key：同机同模型对照；
  无 key：检索 A/B + 零 key 冒烟替代）；报告入 eval/reports/。
- **P4 工具与知行层**：tool 服务（业务系统迁 PG + 权限矩阵 + 参数注入）接入编排，
  transaction/hybrid 链路迁移。
  验收：26 题全量 26/26；与冻结单体同机同模型同索引对照一轮，逐题通过情况一致
  （延迟不比）；`/api/business/reset|overview` 支撑评测断言。
- **P5 记忆 + 观测 + 压测**：memory Recall/Put API（**不接入答案生成**，26 题行为不变）；
  k6 压测 SSE 对话路径，产出真实 TTFT/P95 报告（注明环境与口径）；
  补全 README（架构图 + 服务职责表 + 启动方式）与 ADR 全集；
  全量 26 题复跑确认无回归。

## 诚实红线

- 所有性能/质量数字只能来自本仓库真实运行，禁止估算编造；报告注明环境与口径。
- 不确定现行为的，查代码 / PARITY.md，或问我，不猜。
- 与单体的有意行为差异（会话持久化、摄入异步化、错误内部文案等）全部列入
  `docs/PARITY-MS.md`，逐条给理由；评测覆盖不到的路径在 SERVICES.md 标注风险。

## 交付物

- docker-compose 一键起全链路（含零 key 模式可跑）；
  `README.md` 增补微服务章节（架构图 + 服务职责表 + 启动方式），单体入口保留说明。
- `docs/ADR/*.md`：服务拆分边界、Milvus（FLAT）选型与 pgvector 降级、MQ 选型
  （Redis Streams）、generate 重试与降级策略、conversation 历史不接入生成、
  业务系统归属 tool 服务、预算分布式计量、存储迁移（SQLite→PG）。
- `docs/SERVICES.md`（第一步产出）、`docs/PARITY-MS.md`（有意差异清单）。
- `eval/reports/` 各阶段对照报告（沿用现有报告格式：条件、口径、逐题明细）。
