# P15 RAG 模块对齐 WeKnora（任务书 + 执行记录）

> **背景**：毕设主线下，RAG 模块的实现方式参考腾讯开源的 WeKnora 框架
> （本地副本 `/Users/mrpwn/Project/WeKnora/`，Go 单体 + 插件式 chat pipeline）。
> 本次改造把 WeKnora 检索域最有移植价值的四块机制落到 gewu：入库 CLI + 自适应
> 切片策略链、加权 RRF 融合、精排容错、检索层独立评测——同时关掉 P14 遗留的
> 「入库侧断层」（切分代码滞留 go-final 的 Go 里，Python 主干无法重建索引）。

## 0. 拍板结论（AskUser 确认）

| # | 问题 | 拍板 | 依据 |
|---|------|------|------|
| Q1 | 改造范围 | **四方向全做**：入库 CLI + 切片策略链 / 检索融合升级 / rerank 容错 / 检索评测基建 | 入库侧是最大断层（改切片/换 embedding 的前置）；其余三项是 roadmap M1/M3 未完成项 |
| Q2 | FTS 引擎 | **保持现有 PG + 升级融合** | WeKnora 真 BM25 依赖 ParadeDB；本机 PG 是 brew 共享 postmaster（chatbi 同实例），换引擎要动环境。加权 RRF 公式本身不依赖 BM25 引擎 |
| Q3 | 切片策略 | **WeKnora 自适应策略链**（profiler 画像 → heading → recursive 兜底） | 语料为规整 md、必命中 heading 档；「画像→选型→降级」叙事完整，机制全量移植但默认参数沿用旧值保持可比 |
| Q4 | rerank 方式 | **保留 GLM flash pointwise + 抄 WeKnora 容错** | 符合「免费无阉割」选型原则，不新增外部依赖 |

**抄什么 / 不抄什么**（面试与论文叙事点）：

| WeKnora 机制 | 决策 |
|---|---|
| 切片策略链（画像→选型→校验→降级） | 抄，档位精简为 heading→recursive 两级（heuristic 档的多语言编号识别对规整中文 md 收益小） |
| 父子双层 + 标题面包屑拼入 embedding 内容 | 抄（嵌入内容 = 文档标题 + 面包屑 + 子块正文；text 列仍存纯正文，FTS 行为不变） |
| 加权 RRF（k=60、0.7/0.3、归一 [0,1]、单路退化按 max 归一） | 抄，替换等权 RRF |
| rerank 容错（阈值退化 ×0.7 / 保底 top1 / 复合分） | 抄但裁剪：单一知识库无多来源，复合分 = 0.7×模型分 + 0.3×融合分（砍掉 0.1 来源权重） |
| ParadeDB BM25 / 11 种向量引擎抽象 / chunk 问题生成 / 邻居扩展 / 插件式 pipeline | 不抄（环境约束 / 超额收益 / 入库成本 / 父子扩展已覆盖 / LangGraph 即编排层） |
| 检索指标族（Precision/Recall/NDCG/MRR/MAP） | 抄指标实现（Python 重写） |

## 1. 目标 / 非目标

**目标**
- Python 版入库全链路：md 解析（frontmatter）→ 策略链切片 → 嵌入内容拼装 →
  批量向量化（L2 归一 + 退避重试）→ `rag_upsert_doc` 幂等写入；`make ingest`。
- 加权 RRF：`score = Σ wᵢ/(k+rankᵢ) ÷ (Σw)/(k+1)`，归一 [0,1]，平局保持首现序；
  向量 0.7 / 关键词 0.3；检索参数全量进 Settings（pool_n/k/权重/阈值）。
- 精排容错：复合分排序（0.7×模型分 + 0.3×融合分）、阈值过滤（默认 2.0，全滤空
  ×0.7 退化重试、下限 1.5 保底 top1）、失败降级原序（原有）。
- 检索层独立评测：doc 级 Recall@k / MRR / NDCG@k，复用 dataset 的 expected_docs
  零重标 + flash 生成 45 条口语化变体（gold 继承）；`make retrieval-eval`。
- 全绿门禁：单测 / ruff / lint-arch / 28 题端到端 / 重建索引真跑。

**非目标**
- 不动 PG schema 与存储函数（写入继续走 rag_upsert_doc 收口）。
- 不动 apps/web 与 SSE 契约。
- 不换 FTS 引擎（Q2）、不接外部 rerank API（Q4）。
- 不做 chunk 问题生成索引增强、邻居扩展（WeKnora 机制中明确不抄的部分）。

## 2. 设计与实现

### 2.1 切片策略链（`gewu/rag/chunker.py`，新增）

- **profiler 画像**：一次扫描统计标题总数 / 密度（标题/字符）/ 最浅标题层级；
  选型规则 auto = 标题数 ≥3 且密度 >0.005 且有主标题级 → heading，否则 recursive。
- **heading 档**：ParseMarkdownTree（标题树 + 面包屑，移植 go-final）→ 按主标题级
  聚合父块（超限滑切、带【路径】前缀）→ 父块内段落聚合子块；输出父-子-子…，
  `parent_idx` 契约与 rag_upsert_doc 载荷一致。
- **recursive 档**：扁平段落聚合滑切（无父子，检索层 flat 模式天然兼容）。
- **校验降级**：validate_chunks（非空 / 无空白块 / 子块不超限 / 父块引用完整），
  不过则降下一档并记轨迹（ChunkResult.fallbacks）。
- 参数：`CHUNK_STRATEGY=auto|heading|recursive`、`CHUNK_PARENT_LIMIT=800`、
  `CHUNK_CHILD_LIMIT=200`、`CHUNK_OVERLAP=40`（缺省沿用旧值，消融可调）。

### 2.2 入库 CLI（`gewu/rag/ingest.py` + `apps/server/ingest_main.py`，新增）

- `run_ingest(store, embedder, corpus_dir, cfg, *, rebuild, no_embed)`：纯函数、
  依赖走 Protocol 注入（rag 不横向 import llm，lint-arch 合规）；`--rebuild` 先
  wipe（防语料删除文档残留）；`--no-embed` 只建 FTS（检索侧 MissingVectorsError
  明确拒绝，不静默降级——go-final 同语义）。
- **嵌入内容拼装**（WeKnora knowledge_index_content 同语义）：子块向量吃
  「文档标题 + 面包屑 + 正文」，text 列存纯正文——只有向量获得标题上下文，
  FTS 召回行为不变。
- 批量向量化：批 32、批次级退避重试（1s/2s）、L2 归一。
- 装配入口 `ingest_main.py`（角色同 main.py，合法 import config/llm/rag）；
  `make ingest [REBUILD=1] [NO_EMBED=1]`。

### 2.3 加权 RRF 与参数配置化（`store.py` / `retrieve.py` / `config.py`）

- `rrf_fuse` 升级为加权版并返回 `list[Scored]`（带归一分）；等权退化与旧版序
  完全一致（单调变换），平局首现序保留。
- 单路退化（向量路瞬时失败/无 key）：FTS 分按列表 max 归一 [0,1]，分数标尺
  不失效（WeKnora keyword-only 同款）。
- Settings 新增：`RETRIEVAL_POOL_N=20`、`RRF_K=60`、`RRF_VECTOR_WEIGHT=0.7`、
  `RRF_KEYWORD_WEIGHT=0.3`、`RERANK_THRESHOLD=2.0`；research 每路 k=5 硬编码
  统一为 `retrieval_k`（行为对齐 factual 路）。

### 2.4 精排容错（`retrieve.py`）

- 复合分 = `RERANK_MODEL_WEIGHT(0.7)×(模型分/10) + 0.3×融合归一分`，平局保持
  融合序；阈值作用于模型分（非复合分，WeKnora 同款）。
- 退化链：默认阈值 2.0 全滤空 → `max(2.0×0.7, 1.5)=1.5` 重试 → 仍空且 top1
  模型分 ≥1.5 保底一条 → 最终空则上游拒答（质量不足宁可少给）。
- `len(fused) ≤ k` / 无 key / 失败：跳过或降级原序（原行为保留）。

### 2.5 检索评测基建（`eval/run_retrieval_eval.py` + `eval/gen_query_variants.py`，新增）

- 进程内构建 Retriever 直打（不起服务）；doc 级二值相关指标：Recall@k / MRR /
  NDCG@k（WeKnora metric 族的 Python 重写）。
- 数据：dataset.jsonl 的 factual + multi_hop（15 题，复用 expected_docs）+
  变体集 retrieval-queries.jsonl（flash 生成 45 条口语化/同义/缩略变体，gold
  继承原题——小库原题全满分无区分度，变体集是真实难例来源）。
- 开关 `--no-rewrite` / `--no-rerank` / `--rounds`：改写与精排的 LLM 方差可分离
  归因（温度 0 仍非确定）；报告落 `eval/reports/retrieval-*.json`。

## 3. 评测记录（before / after）

重建前后唯一行为变量 = 嵌入内容拼装（切片参数与旧值一致，60 块 = 父 15/子 45，
全部命中 heading 档）。

| 口径（60 题 = 15 原题 + 45 变体，k=6） | before | after |
|---|---|---|
| 全管线（rewrite+rerank，2 轮均值） | R=1.0 MRR=1.0 NDCG=1.0 | R=1.0 MRR=0.9958 NDCG=0.9976 |
| 纯检索（--no-rewrite --no-rerank） | R=1.0 MRR=1.0 **NDCG=0.9937** | R=1.0 MRR=1.0 **NDCG=0.9987** |

- 纯检索 NDCG +0.005：多跳变体的次相关文档排位上升（面包屑 embedding + 加权
  RRF 双因素），是本次机制改造的直接收益。
- 全管线 after 的 MRR 0.9958：multi-004 一变体两轮各 0.5/1.0（per_round
  0.9917→1.0），确认为 GLM 非确定性方差（与 P14-1 方差基线归因一致），非系统性
  回退；端到端 28 题全绿不受影响。
- 小库天花板如实记录：15 题原题在任何口径下全满分，指标区分度依赖变体集；
  「100+ 题评测集扩充」仍是 roadmap 开放项。

报告：`eval/reports/retrieval-*-before*.json`（4 份）/ `retrieval-*-after*.json`
（2 份）；端到端 `report-20261001-0747.md`（28/28 ✓）。

## 4. 验收记录（2026-10-01）

- [x] 单测 151 全绿（新增 chunker 25 + ingest 16 + 检索管线扩 6；PG 集成真跑）
- [x] ruff check / format 全绿；lint-arch 依赖规则合规（rag 未横向 import llm）
- [x] `make ingest REBUILD=1` 真跑：15 篇 / 60 块（父 15/子 45），heading 档全覆盖，
      向量全部入库（ark 逐条 + 退避重试）
- [x] 检索评测 before/after 对比留档（§3）
- [x] `make eval` 端到端 28/28 全绿（fact 9 / multi_hop 8 / refusal 3 / tx 7 /
      hybrid 1，含 mtfact 两题）
- [x] `.env.example` 死开关清理（ROUTER_MODE/CHUNK_MODE/SESSION_STORE 注释移除）
      + CHUNK_*/RRF_*/RERANK_THRESHOLD 配置说明
- [x] roadmap M1/M3 勾选更新、architecture/04 同步

## 5. 遗留与后续

- 100+ 题评测集扩充（M1 开放项）：变体生成管线已就绪（`make variants`），人工
  校验后可持续扩。
- 切片参数消融（子块 200 vs 384、父块 800 vs 4096）：参数已全配置化 + 重建一
  键化，语料扩充后有区分度时补对比数据（论文消融素材）。
- 真BM25（ParadeDB）与专用 rerank API：明确不做/待定（Q2/Q4 拍板），如立项另
  起任务书。
