# P6 本地真跑通 · 改造提示词 Runbook（GLM chat + 火山 embed / 单体 SQLite / 去无 key）

> 与 `P6-mainstream-upgrade.md`（设计原理 + 完整参考代码）配套：**这份只讲"按什么顺序、把哪段提示词喂给编程助手、跑什么命令验证"**。
>
> **本轮前提（已据此重写，覆盖 P6 旧前提）**：
> 1. 一定有 key：chat 用智谱 GLM（已验证 glm-5.3 / glm-5.3-flash 可用），embedding 用火山方舟；
> 2. **去掉无 key / 演示模式 / 启发式兜底主链路**（启发式规则保留，但重新定位为级联路由的 L0）；
> 3. **先只跑单体 `cmd/server` + SQLite（data/index.db）**，PG / 微服务 / docker / Redis / Milvus 全部以后再迁，本轮不碰；
> 4. 目标是本地真实跑通：`make ingest` 真调火山出向量、`curl /api/chat` 真走 GLM 出答案。

---

## 0. 开工前：embedding 通道现状（本轮确定用 vision 多模态）

最新实测（ark key 鉴权有效）：

| 通道 | 状态 | 说明 |
|---|---|---|
| 多模态 `doubao-embedding-vision-251215` → `/embeddings/multimodal` | ✅ **已开通、可用，2048 维** | 非标准：`data` 是对象（`data.embedding`）、**不支持批量**，P0 已让代码兼容 |
| 文本 `doubao-embedding-large` 等模型名直调 → `/embeddings` | ❌ `NotFound` | 控制台已开通**额度**，但文本模型必须先创建「推理接入点 `ep-xxx`」才能调 |

**本轮决定：就用 vision 多模态**——未来文档是图文混排（二维图像 + 文本），多模态模型把两者编进同一向量空间，可平滑扩展。`.env` 已配好 `EMBED_MODEL=doubao-embedding-vision-251215`、`EMBED_MODE=ark_multimodal`，**P0 代码改完即可直接 ingest，无需再做任何控制台操作**。

vision 通道自测（应返回 `data.embedding` 数组）：
```bash
curl -s https://ark.cn-beijing.volces.com/api/v3/embeddings/multimodal \
  -H "Content-Type: application/json" -H "Authorization: Bearer $ARK_KEY" \
  -d '{"model":"doubao-embedding-vision-251215","encoding_format":"float","input":[{"type":"text","text":"转专业绩点要求"}]}' | head -c 300
```

> **可选优化（以后再说，不阻塞本轮）**：若想要标准批量、ingest 更快，去「在线推理 → 推理接入点」给 `Doubao-embedding-large` 创建接入点拿到 `ep-xxx`，把 `.env` 切到 `EMBED_MODE=text` + `EMBED_MODEL=ep-xxx` 即可——P0 两种模式都支持，切换无需改代码，切换后 `make ingest REBUILD=1` 重建。

---

## 1. 执行顺序与每阶段门禁

| 顺序 | 阶段 | 依赖 | 跑通的直观标志 |
|---|---|---|---|
| **0** | 双 provider 客户端 + 去无 key | 火山模型开通 | `make ingest REBUILD=1` 打印「BM25+向量」；`/api/search` 有向量命中 |
| **1** | 结构切分 + 父子块（SQLite） | 阶段0 | ingest 后每个条款有父块+子块，检索命中子块回取父块 |
| **2** | 级联路由（GLM 概率分类） | 阶段0 | route 事件能看到 layer=L0/L1/L2 |
| **3** | GLM rerank + 父子扩展 | 阶段1 | 开/关 rerank 命中顺序变化且更聚焦 |
| **4** | 长期记忆（SQLite + GLM 抽取） | 阶段0 | 告诉它事实后下一轮能引用 |
| **5** | 通用 ReAct 引擎 | 阶段2/3 | 路径不定的问题能自主多步调工具收敛 |

**每阶段统一门禁**：`go build ./...` 通过 → `go test ./...` 全绿 → 按该节命令本地验证 → 再进下一阶段。任何一步先保证能 `make run` 起来，不积累"编译不过的中间态"。

---

## 2. 阶段 0：双 provider LLM 客户端 + 去无 key（前提中的前提）

**现状（已逐行核对，精确到行）**
- `internal/config/config.go`：`Settings`（16–33 行）只有一套 `LLMKey/LLMBaseURL/EmbedModel`；环境变量白名单在 `applyOSEnv`（104–108 行）、字段映射在 `setField`（115–152 行 switch），三处都要加新字段；
- `internal/llm/client.go`：`url()`（133–135 行）**写死 `LLMBaseURL`**、`newRequest()`（303 行）**写死 `LLMKey`**；`Embed`（240–268 行）打 `/embeddings`、按 `data[].index/embedding` 数组解析；只改 Embed 请求体而不改这两个硬编码，请求仍会打到智谱；
- `internal/rag/ingest.go`：`EmbedBatched`（95–121 行）按 32 一批调 `client.Embed`；`Ingest`（148、165–173 行）向量化失败会**静默降级成仅 BM25**（要去掉，否则会"假成功"）；
- `cmd/server/main.go`：flag 是 `-ingest/-no-embed/-rebuild`（38–40 行），装配在 44–46 行；`internal/agent/direct.go` 39–49 行是无 key 演示分支，演示常量 `DemoModeNote` 在 `internal/agent/prompts.go`；
- `Makefile` 14–15 行单体 `ingest` target **没有透传 REBUILD/NO_EMBED**（所以现在 `make ingest REBUILD=1` 对单体无效），要顺手修。

### 提示词 P0（复制喂给编程助手）
> **P0 双provider（GLM chat + 火山多模态 embed）+ 去无 key，只改单体，不碰 internal/services 微服务。请严格按下列已核对的文件与行位改：**
>
> **(1) config 增加独立 embed 配置** —— `internal/config/config.go`：
> - `Settings` 增加 `EmbedAPIKey string / EmbedBaseURL string / EmbedMode string`（`EmbedModel` 已存在），对应环境变量 `EMBED_API_KEY/EMBED_BASE_URL/EMBED_MODE`；
> - 把这三个 key 加进 `applyOSEnv` 的白名单切片，并在 `setField` switch 增加三个 case；`Default()` 不用给 EmbedBaseURL 默认值（回退逻辑放 client）。
>
> **(2) client 拆分 chat / embed 端点** —— `internal/llm/client.go`：
> - 新增三个私有解析方法：`embedBase()`=`EMBED_BASE_URL` 非空则用它、否则回退 `LLMBaseURL`；`embedKey()` 同理回退 `LLMKey`；`embedModel()` 用 `EmbedModel`；
> - 现在 `url()` 写死 LLMBaseURL、`newRequest()` 写死 LLMKey：把它们参数化（例如 `endpointURL(base, path)`、`newRequestWith(ctx, method, url, key, body)`），**chat 路径（Chat/ChatStream）继续传 LLMBaseURL/LLMKey 保持不变**，embed 路径传 embedBase()/embedKey()；
> - **重写 `Embed(ctx, texts []string) ([][]float64, error)`，对外签名与"按输入顺序返回"保持不变**（这样 `EmbedBatched`、`ingest.go` 零改动）：
>   - `EmbedMode=="ark_multimodal"`：该接口**不支持批量**（实测传多个 text 只返回一个联合向量），所以在 Embed **内部**对 texts 逐个发请求：POST `{embedBase}/embeddings/multimodal`，请求体 `{"model":embedModel,"encoding_format":"float","input":[{"type":"text","text": s}]}`；**响应结构是非标准的 `data.embedding`（data 是对象不是数组，实测 2048 维）**，定义专用结构体解析；用 4 并发的 errgroup/信号量 + 保序收集，单条失败即整体返回错误；每条 usage 累加进 budget；
>   - 其他（`text`/空）：保持现状一次请求 `{embedBase}/embeddings`、`{model,input:[]string}`、按 `data[].index` 解析；
>   - embed 请求体**绝不带**智谱私有的 `thinking` 字段。
>
> **(3) 去掉静默降级与无 key 主链路（单体范围）**：
> - `internal/rag/ingest.go`：删掉 165–173 行"向量化失败→useEmbed=false 继续建纯 BM25"的分支，**非 `-no-embed` 时向量化失败直接 return error**；`-no-embed` 仅作为手动显式选项保留；
> - `cmd/server/main.go`：在 config.Load() 之后（46 行后）加启动校验——`LLM_API_KEY` 为空时 `log.Fatal`；`-ingest` 且非 `-no-embed` 时 embedKey() 为空也 fatal；
> - 删 `internal/agent/direct.go` 39–49 行的演示模式分支（无命中仍返回 NoDataAnswer，有命中直接走 ChatStream），并清理 `prompts.go` 中因此不再被引用的 `DemoModeNote`；
> - `internal/rag/retrieve.go`：索引里没有向量时不再"静默只走 BM25"，返回明确错误提示「向量索引缺失，请配好 EMBED_* 后用 -rebuild 重建」；
> - `router.go` 的 `HeuristicRoute` 函数**保留**（阶段 2 用作级联 L0 规则），但删掉 67–69 行"无 key 就直接启发式"的早退——无 key 已在启动阶段拦下。
>
> **(4) 修 Makefile**：单体 `ingest` target 改为 `go run ./cmd/server -ingest $(if $(REBUILD),-rebuild,) $(if $(NO_EMBED),-no-embed,)`，让 `make ingest REBUILD=1` 真正生效。
>
> **(5) 单元测试**（不发真实网络，用 `httptest.Server`）：① text 模式断言打到 `/embeddings`、批量数组解析、按 index 归位；② ark_multimodal 模式断言每个输入各发一次到 `/embeddings/multimodal`、请求 input 是 `[{type:text,text}]`、解析 `data.embedding`、多个输入保序；③ EMBED_* 缺省时回退 LLM_*；④ 不引入第三方 SDK。
> 改完先保证 `go build ./... && go vet ./... && go test ./...` 全绿，再告诉我验证步骤。

### 验证（看到这些算成功）
```bash
go build ./... && go test ./...
rm -f data/index.db && make ingest REBUILD=1   # 每篇文档必须显示 [BM25+向量]；若向量化失败应直接报错退出，而不是悄悄变"仅BM25"
curl -s localhost:8000/api/health | python3 -m json.tool   # "llm":true 且 "embeddings":true
make run &
curl -s localhost:8000/api/search -H 'Content-Type: application/json' \
  -d '{"query":"转专业绩点要求","k":3}' | python3 -m json.tool   # 有命中
curl -N localhost:8000/api/chat -H 'Content-Type: application/json' \
  -d '{"question":"转专业有什么绩点要求？"}'                    # 真实流式答案 + citations 事件
```
> 报错速查：`ModelNotOpen` → 模型名/接入点错（回第 0 节）；向量检索全空或"维度不一致跳过" → 旧索引维度不同，`make ingest REBUILD=1`；401 → EMBED_API_KEY 错；URL 还是打到 bigmodel → (2) 的 `url()/newRequest()` 硬编码没改干净。

---

## 3. 阶段 1：结构切分 + 父子块（SQLite，不碰 PG）

**与 P6 文档的差异（重要）**：P6 写的是 PG `ALTER TABLE`；本轮**只改单体 SQLite**（`internal/rag/store.go`，表名 `chunks/docs/vectors`）。SQLite 加列用 `ALTER TABLE chunks ADD COLUMN`，在 `execSchema` 里用"先 PRAGMA table_info 判断、缺列再 ADD"的幂等迁移（SQLite 不支持 IF NOT EXISTS 加列）。

### 提示词 P1
> **P1 父子块（SQLite 单体版）**
> Go 项目 gewu，单体用 internal/rag/store.go 的 SQLite（表 chunks(id,doc_id,seq,text)、vectors(chunk_id,dim,data)、docs），切分在 internal/rag/ingest.go 的 ChunkText（空行聚合 450 字符单层）。请实现 Markdown 结构切分 + 父子块，**只动 internal/rag 与 ingest 入口，不碰 internal/services 微服务**：
> 1) 新建 internal/rag/hierarchical.go：正则按 `#{1,6} 标题` 切 section，维护标题栈生成 breadcrumb（"A > B > C"）；父块=一个 section（聚合上限 1000 rune，超长复用 ChunkText 滑切，overlap 40），子块=父块内按段落聚合到 260 rune；定义 `Chunk{Text,SectionPath,IsParent,ParentKey}`，中文一律按 rune 计数、顺序确定可复现。
> 2) 表结构在 `internal/rag/schema.go` 的 `execSchema`（chunks 建表在 18–25 行）：**新建库直接在 chunks 建表语句里加** `parent_id TEXT / section_path TEXT / is_parent INTEGER NOT NULL DEFAULT 0`；同时写老库幂等迁移——`PRAGMA table_info(chunks)` 探测缺列再 `ALTER TABLE chunks ADD COLUMN`（SQLite 的 ADD 不支持 IF NOT EXISTS）。改造 `store.go::UpsertDoc`（91 行）：不要再用 `chunkTexts []string + vectors [][]float64` 两个平行切片，定义一个 `ChunkRecord{Text,SectionPath,ParentID,IsParent,Vec []float64}` 切片入参以避免错位；**父块 is_parent=1 且不写 vectors**；子块 is_parent=0、写向量。关键：`store.go::ensureBM25`（184 行 `SELECT id,text FROM chunks`）必须加 `WHERE is_parent=0`，否则父块也进倒排会被直接命中；向量表本来就只给子块写、天然过滤。新增"按 parent_id 批量取父块、同父多子去重"的读取方法。
> 3) ingest.go：用配置 `CHUNK_MODE=hierarchical（默认）|flat` 切换，hierarchical 时只对子块 EmbedBatched；flat 保持现有 ChunkText(450,80) 逐字一致，保证旧测试不回归。
> 4) 单测：三级标题 breadcrumb 正确；子块 parent_id 指向存在的父块；父块无向量；flat 与改造前输出一致。
> 注意 ingest 现在要把"哪些是子块、各自向量"对齐传给 UpsertDoc，请设计清晰的数据结构而不是错位传参。

### 验证
```bash
make ingest REBUILD=1
sqlite3 data/index.db "SELECT is_parent,count(*) FROM chunks GROUP BY is_parent;"  # 应有 0 和 1 两组
go test ./internal/rag/
```

---

## 4. 阶段 2：级联路由（GLM 概率分类，本轮不需要 embedding 语义路由）

> P6 里的"L1 embedding 语义路由"是给**无 key / 想省 LLM** 场景的备选；本轮**强制有 GLM key，L1 直接用 glm-5.3-flash 输出五类概率即可，删掉 embedding 路由这条分支**，更简单。L0 用现有 `HeuristicRoute` 升级成"高置信精确快路径"，L2 用主模型 glm-5.3 灰度兜底。

### 提示词 P2
> **P2 级联路由（有key版，单体）**
> 在 internal/agent 把 router.go 升级为三级级联，保留旧 RouteQuestion 但默认走新链路，配置 ROUTER_MODE=cascade（默认）|classic：
> 1) 定义 RouteDecision{Route,Confidence,Layer,Reason,PreRAG,Toolset,ModelTier}；L0：把 HeuristicRoute 中"几乎不会错"的强信号（正则：^(帮我|我要|我想)…(预约|请假|退订)）提为精确快路径，命中直接返回、Layer=L0-rule、Confidence=1，省一次 LLM。
> 2) L1：用 glm-5.3-flash（Options.Small=true、JSONMode）分类，prompt 要求输出 `{"scores":{"factual":..,"research":..,"transaction":..,"hybrid":..,"refusal":..},"reason":..}`，五类概率和为 1、拿不准就分散；解析 top1/top2，用双阈值 high=0.80/low=0.55 与 top1-top2 margin=0.15 判定。
> 3) L2：L1 低于 low 或 margin 不足时，用主模型 glm-5.3 few-shot 二次判定；仍不确定则路由到 factual 并把 ModelTier 提到 flagship、标记 Layer=L2-uncertain（不做"直接自由回答"）。
> 4) fillPolicy 把 route 映射到 PreRAG/Toolset/ModelTier，集中维护；pipeline.go 路由处改用 RouteDecision 并在 route 事件里带 layer/confidence。
> 5) 单测覆盖 L0 命中、L1 高置信直路由、margin 不足升 L2、低置信兜底四条路径（LLM 用接口 mock，不发真实请求）。

### 验证
```bash
for q in "帮我预约明天晚上的羽毛球馆" "转专业和保研分别有什么要求" "今天天气怎么样"; do
  curl -N localhost:8000/api/chat -H 'Content-Type: application/json' -d "{\"question\":\"$q\"}" | grep -m1 '"event":"route"'
done   # 依次应看到 L0-rule / L1-llm(research) / L2 或 refusal
```

---

## 5. 阶段 3：GLM rerank + 父子扩展

### 提示词 P3
> **P3 rerank + 父子扩展（单体，有key）**
> internal/rag/retrieve.go 现在两路各 k*2、RRFFuse(k=60) 后直接返回。升级为漏斗：BM25/向量各取 20 → RRF 粗排 topN=20 → 用 **glm-5.3-flash 一次批量调用**对候选打 0~10 相关性分（把所有候选拼进一个 prompt、输出 JSON 分数数组，只 1 次 LLM 调用；解析失败则退回 RRF 顺序且不阻断）→ 精排到 topK；随后按阶段1的 parent_id 回取父块、同父多子去重、按该父下最高子序排序，进上下文用父块文本、引用保留 section_path。定义 Reranker 接口便于以后换 cross-encoder。flat 模式（parent_id 空）保持与现在逐字一致。为 rerank 打分失败、同父去重、平局确定性次序写单测。BM25 中文二元与 RRF k=60 不变。

### 验证：`/api/search` 同一 query，临时关 rerank（配置开关）对比 top3 文本，开 rerank 后应更贴条款；`go test ./internal/rag/`。

---

## 6. 阶段 4：长期记忆（SQLite + GLM 抽取 + 分层装配）

> 单体没有 `services/rag/memory.go`（那是微服务、本轮不碰）。在单体用 SQLite 新建记忆表，复用同一个 data/index.db 或单独 data/memory.db 均可（建议单独文件，职责清晰）。

### 提示词 P4
> **P4 长期记忆（单体 SQLite + GLM）**
> 为单体 cmd/server 增加长期记忆，不引入 PG/Redis：
> 1) 新建 internal/agent/memory.go 与 SQLite 表 memory_episodic(id,session_id,user_id,kind,text,created_at)、memory_fact(user_id,kind,key,value,updated_at, PRIMARY KEY(user_id,kind,key))；向量非必须（单体量小，事实表结构化查询 + 关键词即可，**不要**为此引 pgvector）。
> 2) Consolidate：每轮会话结束后异步（不阻塞回答）用 glm-5.3-flash 从本轮对话抽取稳定事实/偏好 [{kind,key,value}]，UPSERT 进 memory_fact（新值覆盖旧值），失败只 log。
> 3) 装配：直答/研究组装消息时按固定顺序 system → 长期记忆(fact + 本会话相关 episodic TopK) → RAG 上下文 → 近 N 轮历史 → 当前问题；记忆每轮会话只加载一次。无记忆数据时消息结构与现在一致。
> 4) 单测：事实覆盖更新（同一 key 只留最新）、记忆注入、无记忆回退；Consolidate 用 mock LLM。

### 验证：第一轮"我是计算机专业的，绩点 3.8"，第二轮"我符合转专业要求吗"，答案应能引用专业/绩点；查 memory_fact 应只有一条最新值。

---

## 7. 阶段 5：通用 ReAct 引擎（亮点，最后做）

### 提示词 P5
> **P5 ReAct（单体，与现有 workflow 并存）**
> internal/agent/pipeline.go 现在是 route→固定处理器的 workflow。新增单主体 ReAct 而不改默认链路：新建 react.go，定义 AgentTool 接口(Name/Schema/Run)，把混合检索、场馆预约、请假、日期解析封装成工具；RunReAct 每轮让 GLM 输出 {thought,action,action_input} 或 {final}，工具结果以 observation 回填，**final 是唯一终止条件**；防护：相同(action,input)指纹出现 3 次提示换路、maxTurns 上限、复用 internal/budget 熔断、工具错误回填不中断；可用工具集来自阶段2 RouteDecision.Toolset。新增一类"目标明确但路径不定"的问题走 ReAct，其余仍走原 workflow，保证现有用例默认不变。单测覆盖两轮工具后 final、重复指纹收敛、超 maxTurns 兜底、工具报错回填。

---

## 8. 「去无 key」专项：到底删什么、留什么

| 位置 | 处理 |
|---|---|
| `cmd/server` 启动 | LLM_API_KEY 空 → `log.Fatal`（强制有 key，不再演示模式启动） |
| `agent/direct.go` 39–49 行 DemoMode 节选 | **删** |
| `agent/router.go::RouteQuestion` 的 `!HasKey→Heuristic` | 主路径删；`HeuristicRoute` 函数保留给阶段2 当 L0 |
| `rag/retrieve.go` "无向量只走 BM25" | 非常态兜底，改为"索引无向量→明确报错提示重建" |
| `rag/ingest.go` 165–173 行向量化失败静默降级 BM25 | **删降级**：非 `-no-embed` 失败即 error，避免"以为有向量其实没有" |
| `Makefile` 单体 ingest target 不透传 REBUILD | 改为带 `$(if $(REBUILD),-rebuild,)`，否则 `make ingest REBUILD=1` 无效 |
| 微服务 `internal/services/**` | **本轮不动**（PG/Redis 以后迁时再同步去无 key） |
| `eval/` 26 题里"零 key 确定性链路"用例 | 改为需要 key 跑，或给这几题加 build tag/标记 skip，别让它阻塞 `make eval` |

> 说明：去无 key 后 `make eval` 全部走真实 GLM/火山，结果不再逐字确定——这是你选择"业务真跑"的必然结果；**回归保障改为**：结构性断言（事件序列、引用存在、业务库终态、路由类别）而非逐字相等。

## 9. 常见报错速查
- `ModelNotOpen / NotFound`：火山模型没开通或模型名写错 → 第 0 节；
- 向量"维度不一致跳过/检索为空"：换过 embedding 模型，`make ingest REBUILD=1`；
- 火山多模态报请求体格式错：确认 `EMBED_MODE=ark_multimodal` 且 input 是 `[{type:text,text}]`、路径是 `/embeddings/multimodal`；
- GLM 报 thinking 参数错：thinking 只在打智谱 chat 时带，embed/火山不带；
- `SQLITE_BUSY`：单体已 SetMaxOpenConns(1)，别在 ingest 时并发写。

## 10. 诚实边界（面试口径不变）
本地单体真跑通的是：双 provider、父子块混合检索、GLM 级联路由与 rerank、长期记忆、ReAct。**微服务/PG/HNSW 仍是后续演进**，讲到哪必须仓库真有代码+测试+运行截图；没做的就说"设计/进行中"。
