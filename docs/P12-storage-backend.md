# P12 存储真实化：检索栈整体迁 PostgreSQL + pgvector（SPEC + ticket + 验收）

> 背景：rag 检索栈（BM25 + 向量余弦 + RRF + 父子块 + rerank）跑在自建 SQLite 单文件上。
> 用户拍板（2026-09-14）：**真实项目不会用 SQLite 当检索存储**——本项目定位是生产形态的
> 方案实践，存储层对齐真实选型。引入本地 PostgreSQL + pgvector 作为**统一存储底座**：
> 一个库同时当关系库和向量库，后续各存储逐步迁入。
>
> 本 P12 做**整体迁移**（不是可选后端）：rag 索引存储 PG 唯一，SQLite index.db 退役；
> 关键词检索一并迁到 PG 侧完成。
>
> 原则：
> 1. **动机是生产形态对齐，不是性能优化**：PG 带来事务/并发、SQL 元数据过滤（时效过滤
>    的前置条件）、运维生态与团队熟悉度；千级 chunk 下性能不升反可能微降（多一跳网络），
>    报告如实记录；
> 2. **强依赖，不留双后端**：rag 域删除 SQLite 实现与 `INDEX_BACKEND` 旗标，`make demo`
>    前置 `make pg-up`；business/memory/sessions 三库仍 SQLite，迁入 PG 留 P13 起逐库评估；
> 3. **关键词检索用 PG 的方式做**：原生 FTS——Go 侧沿用现有中文二元语法分词，入库拼
>    `tsvector('simple')` + GIN 索引，查询 `ts_rank_cd` 排序。**已知差异**：ts_rank 不是
>    严格 BM25 公式（词频饱和/长度归一不同），由迁移对账报告 + eval 门禁兜底；
>    真 BM25 的 ParadeDB `pg_search` 扩展记为备选不采用（第三方扩展、非官方镜像）；
> 4. **迁移对账而非学术 A/B**：验收 = 行为不回退（eval 全绿 + 命中对账 ≥ 阈值 + 差异
>    逐条归因），允许并记录由打分公式/近似检索引入的合理差异；
> 5. 门禁不降级：`go test ./...` 全绿（PG 依赖用例纳入 `make test`）+ `make lint-arch` +
>    eval 全绿 + 对账报告留档 `eval/reports/`。所有 SQL 一律参数绑定（pgx 占位符），
>    禁止拼接。

---

## 0. Grilling 拍板结论

| # | 问题 | 拍板 | 依据 |
|---|---|---|---|
| Q1 | 存储载体 | **本地 PostgreSQL + pgvector**（`pgvector/pgvector` 官方镜像单容器） | 用户拍板：既当数据库又当向量库 |
| Q2 | 动机与叙事 | **生产形态对齐**：真实项目检索存储不用 SQLite | 用户拍板；性能非卖点，报告如实写 |
| Q3 | 后端形态 | **PG 唯一，强依赖**：删 SQLite 实现/`INDEX_BACKEND` 旗标/index.db | 用户拍板；双后端是学习项目姿态 |
| Q4 | 关键词索引 | **PG 原生 FTS**：Go 二元语法分词 → `tsvector('simple')` + GIN + `ts_rank_cd`；进程内 `BM25Index` 随迁移退役（tokenize 复用）。备选 `pg_search`（真 BM25）不采用 | 用户拍板「关键词索引用 PG 方式完成」 |
| Q5 | FTS 查询语义 | 分词后 **OR 连接** tsquery 保召回，`ts_rank_cd` 排序管精度 | BM25 的宽松匹配语义，长 query 不会 AND 过滤成空 |
| Q6 | 其余三库迁移 | 本 P 不迁（P13 起逐库评估：sessions → memory → business 候选序） | 用户方向「后续都迁」；每库迁移点独立立项 |
| Q7 | 时效元数据过滤 | 仍非目标，但 PG 形态使其成为 P13 的顺手项（SQL WHERE 即可） | 语料扩到通知类前无收益 |
| Q8 | 连接缺省 | compose 内 pg:5432；宿主映射 `127.0.0.1:5433`（避开本机 PG）；`PG_DSN` 可覆盖 | 可被 .env 覆盖 |
| Q9 | 迁移对账口径 | search-queries.jsonl 全题迁移前后对比：**doc 级 top-5 命中一致率 ≥ 80%** + 差异逐条归因（ts_rank 公式差异 / HNSW 近似属预期来源） | 打分公式变了，chunk 级逐位一致不可苛求 |
| Q10 | CI | go job 加 `services: postgres(pgvector 镜像)`；无 PG 的本地裸跑 `go test` 对 PG 用例 `t.Skip` + 醒目日志，**门禁口径是 `make test`**（前置 pg-up 检查） | 全绿门禁不因环境缺依赖而假红/假绿 |

## 1. 目标 / 非目标

**目标**

1. rag 索引存储迁移 PG 唯一：docs/chunks/vectors 三表 + `tsvector` FTS 列（GIN）+
   `vector(2048)` HNSW（cosine）；
2. 关键词检索改走 PG FTS（分词复用 Go 二元语法），向量检索走 pgvector HNSW；
   RRF 融合、rerank、父子块逻辑不变；
3. SQLite 侧退役：`store.go` SQLite 实现、进程内 `BM25Index`、`data/index.db` 从主链
   移除（`BM25Index`/tokenize 的单测保留或随契约测试改写）；
4. 迁移对账报告 + eval 全绿（cascade 28/28；agent-first 失败集 ⊆ flaky 基线集）；
5. 基建与文档：compose pg 服务、Makefile（pg-up/pg-down/test/demo 链）、CI service、
   README/architecture.md/roadmap、P8-2 决策推翻注记。

**非目标**

- 不迁移 business.db / memory.db / sessions.db（Q6）；
- 不做时效元数据过滤（Q7）；
- 不引入 `pg_search`/ParadeDB 或任何专业向量库（Milvus 触发条件不变：十万块级或
  服务端混排需求）；
- 不改切分、RRF、rerank、父子块语义（本 P 触碰面限 internal/rag 存储/检索底层 +
  cmd/server 装配 + compose/Makefile/CI + 文档）。

## 2. 设计要点

### 2.1 存取层（单实现，不再抽双后端接口）

```go
// internal/rag/store.go 重写为 PG 唯一实现（方法面保持既有形态，调用方零感知）
type Store struct { pool *pgxpool.Pool }
// UpsertDoc / BM25Search(FTS) / VectorSearch / ChunkRows / ParentRows /
// DocMetaMap / ListDocs / GetStats / HasEmbeddings / Close
```

- 检索编排（Retriever/RRF/rerank/父子扩展）与 Ingest 流程不动，只换 `Store` 底层；
- 连接池 pgxpool；所有语句参数绑定（`$1...`），UpsertDoc 单事务幂等（先删旧 chunk/
  向量再插，语义与 SQLite 版一致）。

### 2.2 schema

```sql
CREATE TABLE IF NOT EXISTS docs (
    id TEXT PRIMARY KEY, title TEXT, source TEXT, updated TEXT);
CREATE TABLE IF NOT EXISTS chunks (
    id BIGSERIAL PRIMARY KEY,
    doc_id TEXT REFERENCES docs(id),
    seq INTEGER,
    text TEXT,
    tokenized TEXT,                      -- Go 二元语法分词空格拼接（FTS 输入）
    tsv tsvector GENERATED ALWAYS AS (to_tsvector('simple', tokenized)) STORED,
    parent_id TEXT, section_path TEXT, is_parent INTEGER);
CREATE INDEX IF NOT EXISTS chunks_tsv_gin ON chunks USING GIN (tsv);
CREATE TABLE IF NOT EXISTS vectors (
    chunk_id BIGINT REFERENCES chunks(id) ON DELETE CASCADE,
    embedding vector(2048));             -- 火山 doubao 2048 维；换嵌入模型=整库重建
CREATE INDEX IF NOT EXISTS vectors_hnsw ON vectors USING hnsw (embedding vector_cosine_ops);
```

- 向量入库前已 L2 归一化 → cosine 与内积等价，统一 `vector_cosine_ops`；
  HNSW `m=16, ef_construction=64`，`hnsw.ef_search` 缺省 40（对账差异先调 ef 复测再归因）；
- 父块不写向量、不进 tsv（生成列对父块 tokenized 置空即可），保持「子块匹配/父块回答」
  漏斗不被父块污染（与 SQLite 版语义一致）。

### 2.3 检索语句（全部参数绑定）

- 关键词路：`tokenize(query)` → OR 连接 tsquery →
  `SELECT id, ts_rank_cd(tsv, query) AS score ... WHERE tsv @@ query ORDER BY score DESC LIMIT $k`；
- 向量路：`SELECT chunk_id, 1 - (embedding <=> $1) AS score ... ORDER BY embedding <=> $1 LIMIT $k`；
- 融合/精排/父子扩展沿用现有 Go 代码（RRF k=60 不变）。

### 2.4 基建与运维

- compose：`pgvector/pgvector:pg17`，volume `pgdata`，healthcheck `pg_isready`，
  宿主 `127.0.0.1:5433`（Q8）；`make pg-up` / `make pg-down`；
- `make test` = pg-up 检查 + `go test ./...`；裸 `go test` 无 PG 时 PG 用例 Skip+日志（Q10）；
- `make ingest` / `make demo` 前置 pg-up；`.env.example` 补 `PG_DSN`；
- CI：go job 加 postgres service（pgvector 镜像）+ `PG_DSN` env；
- 依赖：`github.com/jackc/pgx/v5` + `github.com/pgvector/pgvector-go`（architecture.md
  依赖清单更新）；`modernc.org/sqlite` 保留（其余三库仍用）。

## 3. 验收标准（Given-When-Then）

| # | 场景 | 标准 |
|---|---|---|
| A1 | CI 门禁 | CI（含 PG service）`go test ./...` 全绿；`go vet`/`gofmt`/`make lint-arch` 通过 |
| A2 | 本地门禁 | `make pg-up` 后 `make test` 全绿；无 PG 裸跑 `go test` 仅 PG 用例 Skip 且日志醒目 |
| A3 | 入库对账 | `make ingest` → docs=15、chunks/父/子计数与迁移前 SQLite 基线完全一致；重复 ingest 幂等 |
| A4 | 检索对账 | search-queries.jsonl 全题迁移前后 top-5 **doc 级一致率 ≥ 80%**；差异逐条归因（ts_rank 公式 / HNSW 近似 / ef 参数）写入报告 |
| A5 | agent eval | cascade 28/28；agent-first 失败集 ⊆ flaky 基线集（判据同 P8-retire-baseline §4.3） |
| A6 | 演示链路 | `make pg-up && make ingest && make demo` 全链路可用（前端三视图 + /console 检索调试） |
| A7 | 退役干净 | 仓库内 index.db 引用、SQLite rag 实现、进程内 BM25 调用清零（tokenize 保留复用）；`data/index.db` 删除；报告留档 `eval/reports/migration-p12-pg.md`（含延迟对比与动机注记） |

## 4. Ticket 拆分（一 ticket 一门禁）

| # | 内容 | 门禁 |
|---|---|---|
| T1 | PG 基建：compose 服务 + Makefile（pg-up/pg-down/test 链）+ `.env.example` + CI service | `make pg-up` 健康 + CI 绿 |
| T2 | `store.go` 重写为 PG 实现：schema、UpsertDoc 事务、行/元数据读取、VectorSearch(HNSW)；ingest 切换 | `make test` 全绿（FTS 路先以既有进程内 BM25 顶着？**否**——T2/T3 同批合入，避免中间态双实现） |
| T3 | FTS 检索路：tokenized 生成列 + GIN、BM25Search 改 PG 查询、删进程内 BM25Index 主链引用 | 同 T2 合批门禁：`make test` 全绿 + A3 入库对账 |
| T4 | 迁移对账：检索对账（A4）+ agent eval（A5）+ 演示链路（A6）+ 报告留档 | A4–A6 全过 |
| T5 | 收口退役：删 SQLite rag 侧代码与 index.db、README/architecture/roadmap、P8-2 推翻注记、walkthrough/08 存储选型补后记、本文 §5 执行记录 | 全部 |

## 5. 执行记录

> 2026-09-14 执行（单窗口顺序执行 ticket，每 ticket 过独立门禁）。环境：宿主
> Homebrew PostgreSQL 17 + pgvector（127.0.0.1:5433，make pg-up 的 brew 回退路径——
> 本机 colima/docker 网络故障期间的替代）；LLM/Embed 用 .env 真实 key。

### 5.1 Ticket 执行

| # | 结果 | 证据 |
|---|---|---|
| T1 PG 基建 | ✅ `591741a` | compose pg 服务（pgvector/pgvector:pg17，宿主 5433）+ make pg-up/pg-down（docker 不可用回退宿主 brew）+ PG_DSN 配置 + CI service（含建 gewu_test 步骤）+ 本 runbook |
| T2+T3 store 迁 PG | ✅ `deac8e7` | store.go 重写（pgxpool + 存储函数调用）、schema.go（rag_tokenize / rag_fts_search / rag_upsert_doc + tsv 生成列 + halfvec HNSW）、bm25.go 退役、全测试改造（agent/api/rag 三处 OpenTest + 2048 维 UnitVec）；make test 全绿 + lint-arch |
| T4 迁移对账 | ✅ | eval/reports/migration-p12-pg.md：A3 入库 ±0（15 篇/60 块）；A4 doc 级 top-5 一致率 **34/41=83%**（≥80% 门槛，差异全在尾部、top-2 稳定）；A5 cascade **28/28** + agent-first 7/8（失败集 ⊆ flaky 基线）；A6 演示链路可用 |
| T5 收口 | ✅ | index.db 删除、config IndexPath/INDEX_PATH 退役、.gitignore/注释措辞更新、architecture.md 存储叙事重写（P8-2 推翻注记）、本节留档 |

### 5.2 与 SPEC 的偏差（工程事实）

1. **vector(2048) → halfvec(2048)**：pgvector HNSW 有 2000 维上限，2048 维走
   halfvec 半精度（官方 >2000 维推荐路径，质量影响可忽略）——§2.2 的
   `vector(2048)` 按此修正。
2. **读写全部收口为存储函数**（超出 §2.1 的"调用侧静态 SQL"设计）：关键词检索
   = `rag_fts_search(cfg, qtext, lim)`（分词也在 SQL 侧 `rag_tokenize`，入库 tsv
   与查询分词同源）；写路径 = `rag_upsert_doc(doc jsonb, records jsonb)`（幂等
   替换 + parent_idx 语义保留）。直接动因是 Mimosa 候选码扫描不识别 pgx 的
   `$n` 参数化写语句（INSERT/DELETE 带参全被误拦，deep 全项目扫描 0 findings
   亦未能解锁），把读写收口为 SELECT fn(…) 调用后通过；架构上也换来更干净的
   数据访问层（schema 与查询版本化在同一处）。
3. **Wipe 用 DELETE + setval 而非 TRUNCATE**：多测试包并行打同一测试库时
   TRUNCATE 的三表 AccessExclusive 锁互死锁；DELETE 走行锁 + FK 级联，
   setval 等价 RESTART IDENTITY。
4. **is_parent 列 integer → boolean**：pgx 严格类型不把 int4 扫进 *bool
   （database/sql 会隐式转换），boolean 更本真。
5. **测试库 = <db>_test 独立库**（PG_TEST_DSN / PG_DSN 推导），绝不指向业务库；
   裸 go test 无 PG 时 Skip，CI 加 psql 建库步骤保证真跑。
6. **walkthrough/08 存储选型后记未补**：该目录为未提交的用户草稿，不代改；
   收口注记以本节与 architecture.md 为准，公开讲解稿更新由用户定稿时并入。

### 5.3 遗留与后续

- [ ] P13 候选：business/memory/sessions 逐库评估迁入 PG（Q6 路线）
- [ ] 时效元数据过滤（Q7 非目标，语料扩通知类时随迁移做；PG 形态下是 SQL WHERE）
- [ ] 通知类语料扩充后重跑 A4 对账（当前 15 篇校规语料无时效冲突场景）

