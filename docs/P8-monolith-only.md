# P8 微服务退役 · 单体模块化收口（技术方案 + 分阶段提示词）

> 背景：P0–P5 完成单体→微服务迁移并逐题 PARITY（26/26 等价证明）；P6 起主线回到单体，
> 微服务冻结在 P5 形态。本项目目的是学习与展示：微服务形态的设计/拆分/对照已经做完并
> 留有完整证据（PARITY-MS.md、六服务代码、评测报告），继续维护双形态只剩成本没有收益。
>
> **决策：退役微服务，收口为模块化单体。**退役不是删除学习成果——代码历史在 git、
> 决策记录进 ADR、PARITY 报告留档；微服务特有的**能力缺口**（会话持久化）先吸收进单体，
> 纯分布式管道（gRPC/Redis Streams/网关）直接删除。
>
> 原则：
> 1. **先吸收、后删除**（阶段顺序不可调换）：微服务里单体没有的能力先搬进单体并过评测，
>    再删源，避免能力丢失；
> 2. 不推翻已验证行为：每阶段门禁 `go build ./... && go vet ./... && go test ./...` 全绿
>    → `make eval` 28 题 + agent-first 8 题无回归 → 报告留档；
> 3. 删除为主、吸收为辅：吸收项只有"真实能力缺口"，不为搬运而搬运；
> 4. 单体模块化 = 显式模块地图 + 依赖规则 + 零依赖的 lint 守护，不做包结构大手术。

---

## 0. 现状盘点：微服务资产决策表

| 微服务侧资产 | 单体现状 | 决策 | 理由 |
|---|---|---|---|
| conversation 服务：办理流程状态（TxSession）PG 持久化 | `SessionStore` 进程内 map + TTL，**重启即丢** | **吸收（阶段1）** | 真实能力缺口：办理到一半重启，流程蒸发 |
| conversation 服务：跨重启会话历史 | episodic（memory.db）已覆盖近期对话 | 不吸收 | 单体记忆层已等价 |
| store_pg.go：kb_chunks 的 PG 存储 | SQLite（data/index.db） | **可选吸收（阶段2）** | 存储可换是模块化叙事的一部分；默认仍 SQLite |
| vec_milvus.go：Milvus 向量后端 | 进程内暴力余弦 | **可选吸收（阶段2）** | 同上，`VEC_BACKEND` 三选一（P6 文档 §7 的设计） |
| 业务系统 PG 版（tool 服务） | SQLite business.db | 不吸收 | 业务库随主库走，双后端只留知识库侧即可 |
| Redis Streams 摄入管线 | 直接入库 | 删除 | 流式解耦没有服务边界就无意义 |
| gateway / gRPC 六服务 / proto / pkg/gen | 单进程函数调用 | 删除（阶段3） | 纯分布式管道 |
| agent_config 表 | 编译期常量（路由阈值等） | 不吸收 | 如需热调再做，记录决策即可 |
| 预算持久化 | usage.json 写穿 | 已等价 | — |
| 观测（traceid 中间件等） | svcbase/traceid.go，未接 | **小吸收（并入阶段4）** | 单体加 X-Trace-Id 响应头 + 日志字段，零依赖 |
| k6 压测（eval/k6-chat.js） | 直打 :8000 | 保留 | 本来就不分形态 |
| PARITY 工具（compare_search.py、PARITY-MS.md、compose-monolith :8001 对照） | — | 归档进 ADR 引用后删除 | 双形态消失后无对照对象 |
| DemoModeNote 常量（prompts.go，仅为 orchestrator 保留） | 单体已无引用 | 删除（阶段3） | 最后一处"去无 key"残留 |

## 1. 目标架构：单体模块化

```
cmd/server（唯一二进制 :8000）
 └─ internal/
     ├─ agent      编排域：pipeline/triage/cascade_router/react/transaction
     │             /memory/query_rewrite/tools（权限矩阵）
     ├─ rag        检索域：hierarchical(父子块)/bm25/retrieve/rerank
     │             /store(sqlite[, pg]) /vecstore(cosine[, milvus])
     ├─ llm        模型访问域：chat/stream/embed 双 provider/tool-calling
     ├─ business   业务域：预约/请假（只经 agent.Tools 出口访问）
     ├─ budget / config / dates / middleware  支撑域
     └─ （删除 svcbase、services）
```

依赖规则（阶段 4 固化 + lint 守护）：
- `agent → rag/llm/business`（经接口：Retriever/LLMer/Tools）；`rag/llm/business ↛ agent`（反向禁止）
- `business` 只经 `agent.Tools`（权限矩阵单一出口）被触达；
- 支撑域可被任何域用，但不 import 业务域；
- `cmd/server` 只做装配（flag/env/依赖注入），不含业务逻辑。

## 2. 阶段总览

| 顺序 | 阶段 | 动作 | 依赖 |
|---|---|---|---|
| 0 | 基线冻结与依赖盘点 | 确认单体不 import services；记录基线评测 | — |
| 1 | 会话持久化吸收 | TxSession 落 SQLite，重启可续 | 0 |
| 2 | （可选）存储后端模块化 | rag 的 pg/milvus 后端 + 开关 | 0（须在 3 前） |
| 3 | 微服务删除 | 六服务/proto/compose/依赖/Makefile | 1（2 如做） |
| 4 | 文档与架构收口 | ADR、README/architecture、trace-id、lint-arch | 3 |

---

## 3. 阶段 0：基线冻结与依赖盘点

### 提示词 P8-0
> 在 gewu 仓库执行退役前置盘点，只读不改代码：
> 1) `go list -deps ./cmd/server` 与 `grep -r "internal/services" cmd/server internal/agent internal/rag internal/llm internal/business internal/config internal/budget internal/dates internal/middleware` 确认单体链路零 import 微服务包，把结果贴出来；若发现引用，列出引用点先停下（不许直接删）；
> 2) `grep -rn "svcbase\|DemoModeNote" --include="*.go" cmd internal | grep -v internal/services` 找出微服务之外对 svcbase/DemoModeNote 的引用，作为阶段 3 的删除清单输入；
> 3) 盘点 docker/ 与 docker-compose.yml：列出哪些 service/profile 属于微服务（含 milvus profile）、哪些是单体（monolith profile / api 镜像），写成清单；
> 4) 跑基线评测留档：`RATE_LIMIT_PER_MINUTE=600` 起单体，`python3 eval/run_eval.py --tag p8-baseline`（28 题）与 `ROUTER_MODE=agent-first` 起服务跑 `--dataset eval/dataset-agent.jsonl --tag p8-baseline-agent`（8 题），报告存 eval/reports/。
> 产出：本节盘点结果写进 eval/reports/P8-retire-baseline.md。

## 4. 阶段 1：会话持久化吸收（唯一强制吸收项）

现状：`agent.SessionStore` 进程内 map，办理流程（PhaseCollect/Confirm）中途重启即丢；
微服务 conversation 形态当年靠 PG 表跨重启续流程。吸收方案：同一 `SessionStore` 语义
（Get/Ensure/Clear + TTL 惰性清理）加 SQLite 实现，`SESSION_STORE=sqlite（默认）|memory`。

### 提示词 P8-1
> 给 gewu 单体补办理流程状态持久化，不引入新依赖（复用 modernc.org/sqlite）：
> 1) internal/agent/session.go 保持现有接口与内存实现不动；新增 session_sqlite.go：
>    `OpenSessionStore(path string)`（建议 data/sessions.db 独立文件），表
>    `tx_sessions(session_id PK, role, user, phase, tool, slots JSON, last_asked, updated_at)`，
>    DDL 为静态字面量、幂等；Get/Ensure/Clear/TTL 行为与内存版逐条对齐（TTL 30 分钟惰性清理，
>    测试可注入 clock——沿用现有 SessionStore 的 clock 注入模式）；
> 2) cmd/server 装配：`SESSION_STORE=memory|sqlite`（缺省 sqlite），启动打印会话后端；
> 3) 单测：①跨"重启"续办——写入 collect 阶段会话后关闭再打开 store，Get 能取回并可继续
>    advance；②TTL 过期清理；③slots JSON 往返无损；④memory 后端行为不变（现有测试全绿）；
> 4) 真跑验证：起服务，"帮我预约明天晚上的羽毛球馆"走到追问槽位 → kill 服务 → 重启 →
>    同 session_id 发"明天晚上七点"→ 能继续收集并到确认（SSE 抓事件流贴进报告）。
> 门禁：build/vet/test 全绿 → 28+8 评测无回归 → 留档 eval/reports/P8-session-persist.md。

## 5. 阶段 2（可选）：存储后端模块化

价值：`DB_BACKEND=sqlite|postgres`、`VEC_BACKEND=cosine|milvus` 的可切换后端是
"单体模块化"的存储叙事，且代码已存在于 services/rag（store_pg.go / vec_milvus.go），
搬运成本主要是装配。**不做此阶段不影响退役**；做了，知识库可独立于主库选型。

### 提示词 P8-2
> 把微服务 rag 的 PG/Milvus 存储移植为单体的可选后端（搬运 + 适配，不新写 SQL）：
> 1) 将 internal/services/rag/store_pg.go 移植为 internal/rag/store_pg.go：实现与 Store
>    相同的使用面（UpsertDoc([]ChunkRecord)/BM25 数据源/向量读写/ChunkRows/ParentRows/
>    DocMetaMap/Stats），schema 用 P6 文档 §3.3 的幂等迁移（parent_id/section_path/is_parent）；
>    BM25 倒排仍用共享 BM25Index 进程内构建（PG 只做持久化，不引 tsvector——中文二元零依赖是既定决策）；
> 2) vec_milvus.go 移植为 internal/rag/vec_milvus.go，与现有 VecStore 接口对齐；
> 3) 配置：`DB_BACKEND=sqlite（默认）|postgres`、`VEC_BACKEND=cosine（默认）|milvus`，
>    连接串走 PG_DSN/MILVUS_ADDR 环境变量；postgres/milvus 后端允许编译期裁剪：
>    用 build tag（`//go:build pg` / `//go:build milvus`）隔离驱动依赖，默认构建零新依赖；
> 4) 单测：PG 路径用接口级测试 + `pg` tag 下以 `TEST_PG_DSN` 存在才跑（缺 DSN 自动 skip）；
>    SQLite 默认路径现有测试全绿即为验收；
> 5) 文档：架构图标注双后端与"何时必须换"（chunk 万级/多副本，口径同 P6 §7）。
> 门禁同上，留档 eval/reports/P8-storage-modular.md（如做了）。

## 6. 阶段 3：微服务删除（主手术）

### 提示词 P8-3
> 执行微服务退役删除，逐项进行，每删一组立即 `go build ./... && go test ./...`：
> 1) 删目录：cmd/{gateway,orchestrator,conversation,generate,tool,rag}、internal/services/、
>    pkg/gen/、proto/、buf.yaml、buf.gen.yaml；
> 2) 删 internal/svcbase/ 中仅服务用的文件（grpc.go 等），traceid/logger 若单体阶段 4 要用则先留；
> 3) Makefile：删除 build-ms/run-ms/ingest-ms/compose-ms/compose-milvus/compose-monolith/
>    buf-lint/buf-generate 与 MS_SERVICES 变量；`make ingest/run/test/eval` 保持不变；
> 4) docker-compose.yml 与 docker/：删除微服务 service、profiles（milvus/pg/redis/monolith 对照）；
>    若存在单体镜像 Dockerfile 则保留并挪到仓库根或 docker/ 精简；全删则 compose 文件一并删除；
> 5) 代码清理：prompts.go 删 DemoModeNote 及注释；全仓 grep `internal/services|pkg/gen|zerolog|grpc`
>    确认无残留 import；
> 6) `go mod tidy` 后 go.mod 中 grpc/protobuf/buf 相关依赖应消失（若阶段 2 未做或未启用 build tag，
>    pgx/milvus 依赖也应消失）——把 go.mod 前后 diff 贴进报告；
> 7) eval/compare_search.py 删除（PARITY 对照对象已不存在）。
> 门禁：build/vet/test 全绿（此时 `go test ./...` 应只剩单体包）→ 28+8 评测无回归 →
> 留档 eval/reports/P8-ms-removal.md（含删除清单与 go.mod diff）。

## 7. 阶段 4：文档与架构收口（单体模块化固化）

### 提示词 P8-4
> 收口文档与模块边界守护：
> 1) 新增 docs/ADR/000X-微服务退役与单体模块化.md：背景（学习目的已完成 P0–P5 迁移与
>    26/26 PARITY）、决策（退役理由：量级不匹配+主线专注，参照 eval/reports 报告索引）、
>    后果（能力吸收清单=阶段1/2、删除清单=阶段3、未来若需分布式按 git 历史与 ADR 复活路径）；
> 2) README.md / docs/architecture.md 重写为单体模块化架构：模块地图（§1 的图）、依赖规则、
>    双链路（cascade 默认 / agent-first 灰度）、开关清单（ROUTER_MODE/CHUNK_MODE/RERANK_MODE/
>    REACT_MODE/QUERY_REWRITE/SESSION_STORE[/DB_BACKEND/VEC_BACKEND]）；docs/SERVICES.md、
>    PARITY-MS.md 移入 docs/history/ 并在 ADR 中引用（不删除，是学习成果证据）；
> 3) 零依赖 lint：新增 Makefile target `lint-arch`，用 `go list -f '{{.ImportPath}} {{.Imports}}' ./...`
>    + grep 断言依赖规则（rag/llm/business 不得 import agent；business 不得 import rag/agent；
>    cmd/server 只 import 装配所需），违规即非零退出；写一条 Makefile CI 自测（make lint-arch 自身通过）；
> 4) 观测小吸收：/api/chat 响应头加 X-Trace-Id（复用 svcbase/traceid 逻辑或内联，uuid v4 零依赖实现），
>    gin 日志与 [agent] 日志行带同一 trace-id；
> 5) docs/roadmap.md 收尾：P8 章节标注完成与报告链接。
> 门禁：全绿 + 28+8 评测 + `make lint-arch` 通过 → 留档 eval/reports/P8-wrapup.md（终版架构图
> + 全部 P8 报告索引 + git 提交建议：按 P8-0~P8-4 分五个 commit）。

---

## 8. 统一门禁与回退

- 每阶段：`go build ./... && go vet ./... && go test ./...` → `RATE_LIMIT_PER_MINUTE=600`
  起服务 → `python3 eval/run_eval.py --tag p8-<阶段>`（28）→ `ROUTER_MODE=agent-first`
  起服务 → `--dataset eval/dataset-agent.jsonl --tag p8-<阶段>-agent`（8）→ 报告入 eval/reports/。
- 重置记忆/会话库先停服务再 rm（既有运维注意）。
- 回退：删除阶段（3）之前的任何时点都只是"增加了能力"，可整体放弃；阶段 3 本身依赖
  git revert 回滚，删除前打 tag `pre-ms-removal`。

## 9. 面试口径（诚实边界）

- 讲过：为什么先单体后拆服务（可复现基线）→ 怎么保证拆不坏（26/26 PARITY 契约测试）→
  为什么退役（学习目标已完成；量级下微服务收益不成立、成本照付）→ 退役怎么做到不丢能力
  （先吸收会话持久化再删源、ADR 留决策、git 留历史）。
- 单体模块化要能说出三条：模块地图与依赖规则（lint-arch 守护）、存储可换的触发线
  （chunk 万级/多副本才换 pg/milvus）、双链路并存按 ROUTER_MODE 灰度。
- 不宣称：分布式追踪/服务网格/多环境部署——没做就是没做。
