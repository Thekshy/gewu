# P6 对齐业界主流方案改造指南（级联路由 / 父子块 RAG / Rerank / Memory 接入 / ReAct）

> 目标：在 P0~P5 已有的「五分类路由 + BM25/向量混合检索 + RRF + Deep Research + 办理层 + 26 题评测」基础上，
> 对照 2025–2026 业界主流 Agent/RAG 工程实践补齐短板。本文给出：**现状差距矩阵 → 参考 Go 代码 → 分步改造 → 可直接复制给编程助手的提示词**。
>
> 原则：**不推翻 PARITY 契约**。每项改造都保留旧实现作为可回退基线，用开关（env/config）控制，并在 `eval/` 26 题上证明无回归后再默认开启。

---

## 0. 现状评估：gewu 已经做到哪了

先明确：**当前水平已经超过绝大多数校招项目**，下列能力是现成的、面试要主动讲：

- 五分类路由（小 LLM JSON 分类 + 无 key 启发式降级），主/辅模型分层（glm-5.3 / flash）；
- 混合检索：BM25（中文二元、零分词依赖）+ 向量余弦，**RRF 融合 k=60（与业界一致）**，query 改写；
- Deep Research 固定管线（子问题拆解 → 多路检索 → 跨路去重 → 交叉综合 + 引用）；
- 办理层状态机（槽位/确认/回执/冲突恢复）+ 工具层权限矩阵单一出口；
- 预算集中计量、限流、SSE 取消传播、确定性（平局次序/键序构造性保证）、26 题离线评测、单体/微服务逐题 PARITY。

**因此本次不是"从零补能力"，而是把几个"做到 60 分的通用件"升级到"业界 90 分形态"，并补一个真正的 Agent Loop。**

---

## 1. 差距矩阵（按优先级）

| # | 环节 | 现状（文件） | 业界主流 | 差距 | 优先级 |
|---|---|---|---|---|---|
| 1 | 意图路由 | `internal/agent/router.go`：有 key 每问调一次小 LLM，输出 `{route,reason}`；启发式仅作**降级**；无置信度 | **级联**：L0 规则快路径 → L1（embedding 语义路由/小模型，带置信度+margin 双阈值）→ L2 大模型灰度兜底；输出**路由决策包** | 规则没用来省钱、无置信度/兜底、route 只是 label | **P0** |
| 2 | 切分 | `internal/rag/ingest.go::ChunkText`：空行聚合到 450 字符 + 80 overlap，单层 | **结构切分（标题层级+breadcrumb）+ 父子块**（子块检索/父块回答） | 不按标题切、单层折中块、无 parent_id/breadcrumb | **P0** |
| 3 | 检索精排 | `internal/rag/retrieve.go`：两路各 k*2 → RRF → 直接返回 k | 粗排召回 topN(20~40) → **cross-encoder/LLM rerank** → topK；命中子块**回取父块**；metadata 过滤 | 无 rerank（最大精度增量缺失）、召回漏斗浅、无父子扩展 | **P0** |
| 4 | Memory | `services/rag/memory.go`：建了 `kb_memory`+向量、有 put/recall，但 **ADR-0005 明确不接入生成**；仅 session 级原始文本 | 短期(窗口+摘要)+长期(episodic/semantic)，异步 **consolidation 固化事实**，**分层装配进上下文** | 记忆只存不用、无事实抽取、无 user 级、无分层装配 | **P0** |
| 5 | Agent Loop | `pipeline.go`：route→switch 到**固定处理器**（workflow），工具调用硬编码 | 通用 **ReAct 循环**：LLM 出 tool_calls → 执行回填 → 再 LLM → 无 tool 即结束；指纹去重/maxTurns/预算熔断 | 现在是 workflow 不是 autonomous agent（**这不是缺陷，但应补一个 ReAct 引擎做对照与亮点**） | P1 |
| 6 | 向量存储 | `services/rag/vec_pg.go`：BYTEA 存 float32 + **进程内暴力余弦**；BM25 自实现 | pgvector `vector` 类型 + **HNSW**；`tsvector`+GIN 全文，SQL 内 RRF | 小数据量暴力更快更简单，**当前不必改**；要能讲清何时必须换 | P2（认知） |

> 诚实边界：15 个合成语料、chunk 量级很小，#6 的暴力余弦在内存里是毫秒级、比引 pgvector 更简单，**保留是合理工程权衡**；#1~#4 是"机制升级"，#5 是"能力补全"。面试统一口径：**workflow 是确定性基线（所以 26/26 可复现），ReAct 是自主能力扩展，两者并存、按场景路由。**

---

## 2. 改造一：级联意图路由（P0）

### 2.1 目标形态

```text
query
 ├─ L0 规则快路径（现 HeuristicRoute 升级：只接高置信精确 case，命中直接返回，省一次 LLM）
 ├─ L1 小 LLM 分类（prompt 增加 confidence；解析后做双阈值 + top1-top2 margin）
 │    └─ 无 key / 想省成本：embedding 语义路由（每类若干示例向量，最近邻 + 相似度阈值）
 └─ L2 灰度兜底：L1 落灰度区 → 主模型 few-shot 二次判定；仍低置信 → 通用 agent 或反问澄清
输出：路由决策包 RouteDecision（不只是 label）
```

### 2.2 参考代码（新建 `internal/agent/cascade_router.go`）

```go
package agent

import (
	"context"
	"regexp"

	"gewu/internal/llm"
)

// RouteDecision 路由决策包：下游编排据此决定 agent/工具集/是否前置RAG/模型档。
type RouteDecision struct {
	Route      string  // factual|research|transaction|hybrid|refusal
	Confidence float64 // 0~1，L0=1.0；L1 来自模型/相似度；L2 来自主模型
	Layer      string  // L0-rule | L1-llm | L1-embed | L2-main | fallback
	Reason     string
	PreRAG     bool              // 是否前置检索
	Toolset    []string          // 允许的工具子集（空=不限制）
	ModelTier  string            // small | standard | flagship
}

// 双阈值（生产应来自 config/agent_config，可热调）。
const (
	confHigh = 0.80 // ≥ 且 margin 足够 → 直接路由
	confLow  = 0.55 // < → 不相信 L1，走 L2/澄清
	marginMin = 0.15 // top1-top2 概率间隔，小于则视为"类别纠缠"
)

// 高置信精确规则：只接"几乎不可能错"的 case（办理强动词+第一人称、明确命令）。
var exactTxRe = regexp.MustCompile(`^(帮我|我要|我想|给我|麻烦).*(预约|预订|请假|销假|退订|取消预约)`)

// CascadeRoute 三级级联路由。
func (d *Deps) CascadeRoute(ctx context.Context, question string) RouteDecision {
	// L0：规则快路径（毫秒、零成本、高置信）
	if exactTxRe.MatchString(question) {
		return RouteDecision{Route: "transaction", Confidence: 1.0, Layer: "L0-rule",
			PreRAG: false, Toolset: []string{"venue_book", "leave_apply"}, ModelTier: "small",
			Reason: "规则快路径：明确办理指令"}
	}

	// 无 key：L1 退到 embedding 语义路由（见 2.3），再退启发式
	if d.LLM == nil || !d.LLM.HasKey() {
		if dec, ok := d.embedRoute(ctx, question); ok {
			return dec
		}
		h := HeuristicRoute(question)
		return RouteDecision{Route: h.Route, Confidence: 0.5, Layer: "fallflow-heuristic",
			PreRAG: h.Route == "factual" || h.Route == "research", ModelTier: "small", Reason: h.Reason}
	}

	// L1：小 LLM 分类，要求输出概率分布（返回决策与 top1/top2 概率）
	dec, top1, top2 := d.l1Classify(ctx, question)

	// 双阈值 + margin 判定
	margin := top1 - top2
	if dec.Confidence >= confHigh && margin >= marginMin {
		dec.fillPolicy() // 按 route 填 PreRAG/Toolset/ModelTier
		dec.Layer = "L1-llm"
		return dec
	}
	if dec.Confidence < confLow || margin < marginMin {
		// L2：主模型二次判定（灰度区，只吃少量流量）
		if l2, ok := d.l2Arbitrate(ctx, question); ok {
			return l2
		}
		// 仍不确定：交通用 agent 边做边判，或让编排层反问
		return RouteDecision{Route: "factual", Confidence: dec.Confidence, Layer: "L2-uncertain",
			PreRAG: true, ModelTier: "flagship", Reason: "灰度未决，转通用链路并提高模型档"}
	}
	dec.fillPolicy()
	return dec
}

// l1Classify 让小模型输出各类概率，而不是单个 label；返回决策与 top1/top2 概率。
func (d *Deps) l1Classify(ctx context.Context, q string) (RouteDecision, float64, float64) {
	raw, err := d.LLM.Chat(ctx, []llm.Message{
		{Role: "system", Content: RouterSystemCascade}, // prompt 见 2.4
		{Role: "user", Content: q},
	}, llm.Options{JSONMode: true, Temperature: 0, MaxTokens: 200, Small: true})
	if err != nil {
		return RouteDecision{Route: "factual", Confidence: 0}, 0, 0
	}
	// 解析 {"scores":{...},"reason":..}：按概率排序取前二，Confidence=top1
	dec, top1, top2 := parseScores(raw)
	return dec, top1, top2
}

// fillPolicy 路由类别 → 处理策略（决策包的"策略"部分，集中维护，别散落 switch）。
func (r *RouteDecision) fillPolicy() {
	switch r.Route {
	case "factual":
		r.PreRAG, r.ModelTier = true, "standard"
	case "research":
		r.PreRAG, r.ModelTier = true, "flagship"
	case "transaction", "hybrid":
		r.Toolset, r.ModelTier = []string{"venue_book", "leave_apply"}, "small"
	case "refusal":
		r.ModelTier = "small"
	}
}
```

### 2.3 embedding 语义路由（无 key / 省成本的 L1）

```go
// 每个意图准备若干示例，启动时 embed 一次缓存；新 query 取最近邻。
func (d *Deps) embedRoute(ctx context.Context, q string) (RouteDecision, bool) {
	protos := d.routeProtos() // map[route][]string，示例来自 eval/dataset 归纳
	qv, err := d.LLM.Embed(ctx, []string{q})
	if err != nil || len(qv) == 0 { return RouteDecision{}, false }
	best, bestSim, second := "", 0.0, 0.0
	for route, examples := range protos {
		pv, _ := d.LLM.Embed(ctx, examples) // 生产：启动时预算并缓存，别每问算
		for _, v := range pv {
			sim := cosine(qv[0], v)
			if sim > bestSim { second, bestSim, best = bestSim, sim, route }
		}
	}
	if bestSim < 0.75 { // 低于阈值不硬路由，交回上层走启发式/L2
		return RouteDecision{}, false
	}
	return RouteDecision{Route: best, Confidence: bestSim, Layer: "L1-embed", PreRAG: true}, true
}
```

### 2.4 配套：路由 prompt 增加概率输出（改 `prompts.go`）

```go
const RouterSystemCascade = `你是高校问答系统的问题分类器。类别：
- factual：单一事实/政策查询；research：需多步、多条件综合；transaction：明确要办理（预约/请假）；
- hybrid：既问政策又要办理；refusal：与本校服务完全无关。
只输出 JSON：{"scores":{"factual":0.0,"research":0.0,"transaction":0.0,"hybrid":0.0,"refusal":0.0},"reason":"一句"}
scores 为各类概率，和为 1；拿不准就把概率分散，不要给某类虚高置信。`
```

### 2.5 改造步骤与验收
1. 新增 `cascade_router.go`，**保留** `router.go`（用 config `ROUTER_MODE=classic|cascade` 切换）。
2. `pipeline.go` 第 87–95 行路由处改为按开关调 `CascadeRoute`，下游用 `dec.PreRAG/Toolset/ModelTier` 替代硬编码。
3. 把 `eval/dataset.jsonl` 每题的期望 route 作为**路由评测集**，输出每类 P/R、混淆矩阵、L0 命中率、L2 升级率（新增 `eval/route_eval.py`）。
4. 验收：26 题行为不回归；统计"若全走 LLM 的调用次数 vs 级联后次数"，给出 L0/L1-embed 省掉的 LLM 调用比例。

### 2.6 复制给编程助手的提示词
> **提示词 P-路由**
> 我在 Go 项目 gewu（路径 internal/agent）里要把问题路由器升级成三级级联。现状：router.go 里 RouteQuestion 有 key 就调小 LLM 做五分类（输出 {route,reason}），无 key 降级 HeuristicRoute；pipeline.go 用 RouteResult.Route 做 switch 分发。请：1) 新建 cascade_router.go，实现 L0 高置信规则快路径 → L1 小 LLM 分类（prompt 输出五类概率 scores，解析 top1/top2，用双阈值 high=0.8/low=0.55 和 top1-top2 margin=0.15 判定）→ L2 主模型 few-shot 二次判定兜底；2) 定义 RouteDecision{Route,Confidence,Layer,Reason,PreRAG,Toolset,ModelTier} 决策包，并写 fillPolicy 把类别映射到处理策略；3) 无 key 时 L1 用 embedding 语义路由（每类示例向量最近邻+相似度阈值 0.75，示例向量启动时缓存）；4) 用配置 ROUTER_MODE 开关保留旧实现，不破坏现有 PARITY 契约和测试；5) 为 cascade_router 写单元测试覆盖：L0 命中、L1 高置信直路由、margin 不足升级 L2、低置信兜底四类路径。不要引入新第三方依赖，LLM 走现有 internal/llm.Client。

---

## 3. 改造二：结构切分 + 父子块（P0）

### 3.1 目标
- 按 Markdown 标题（`#`/`##`/`###`）切出**父块**（一个完整 section，聚合到 800–1200 字符，带 breadcrumb 标题路径）；
- 父块内再切**子块**（200–300 字符，是真正建向量/BM25 索引、做匹配的单元）；
- `kb_chunks` 增加 `parent_id / section_path / is_parent / level`；父块文本也存表（供命中后回取），子块建索引。

### 3.2 参考代码（新建 `internal/rag/hierarchical.go`）

```go
package rag

import (
	"regexp"
	"strconv"
	"strings"
)

type Node struct {
	Level  int      // 标题层级 1/2/3
	Title  string
	Path   string   // breadcrumb，如 "学籍管理 > 转专业 > 申请条件"
	Body   string
}

var headingRe = regexp.MustCompile(`(?m)^(#{1,6})\s+(.*)$`)

// ParseMarkdownTree 按标题把文档切成带 breadcrumb 的 section 节点。
func ParseMarkdownTree(text string) []Node {
	matches := headingRe.FindAllStringSubmatchIndex(text, -1)
	var nodes []Node
	stack := []string{} // 当前标题栈，用于 breadcrumb
	if len(matches) == 0 {
		return []Node{{Level: 0, Title: "", Path: "", Body: strings.TrimSpace(text)}}
	}
	for i, m := range matches {
		hashes := text[m[2]:m[3]]
		title := strings.TrimSpace(text[m[4]:m[5]])
		bodyStart := m[1]
		bodyEnd := len(text)
		if i+1 < len(matches) { bodyEnd = matches[i+1][0] }
		body := strings.TrimSpace(text[bodyStart:bodyEnd])
		lvl := len(hashes)
		for len(stack) >= lvl { stack = stack[:len(stack)-1] }
		stack = append(stack, title)
		nodes = append(nodes, Node{Level: lvl, Title: title, Path: strings.Join(stack, " > "), Body: body})
	}
	return nodes
}

type Chunk struct {
	Text        string
	SectionPath string
	IsParent    bool
	ParentKey   string // 子块指向父块（docID+父seq）
}

// HierarchicalChunks 父子两层：父=section（限长内聚合），子=父内按段落聚合到 childLimit。
func HierarchicalChunks(docID string, text string, parentLimit, childLimit, overlap int) []Chunk {
	var out []Chunk
	for pi, node := range ParseMarkdownTree(text) {
		parentKey := docID + ":p" + strconv.Itoa(pi)
		// 父块：section 全文若超 parentLimit 再按段落滑切（复用 ChunkText 思路）
		parents := ChunkText(node.Body, parentLimit, overlap)
		for j, p := range parents {
			pKey := parentKey
			if len(parents) > 1 { pKey = parentKey + "-" + strconv.Itoa(j) }
			out = append(out, Chunk{Text: withPath(node.Path, p), SectionPath: node.Path, IsParent: true, ParentKey: pKey})
			// 子块：父块内再切小，只给子块建检索索引
			for _, c := range ChunkText(p, childLimit, overlap) {
				out = append(out, Chunk{Text: c, SectionPath: node.Path, IsParent: false, ParentKey: pKey})
			}
		}
	}
	return out
}

func withPath(path, body string) string {
	if path == "" { return body }
	return "【" + path + "】\n" + body
}
```

> 注：`strconv.Itoa` 已在 import 中；`ChunkText` 复用现有实现，父块 `parentLimit≈1000`、子块 `childLimit≈260`、overlap≈40（字符/rune）。

### 3.3 表结构与入库改造（`services/rag/store_pg.go`）
```sql
ALTER TABLE kb_chunks ADD COLUMN IF NOT EXISTS parent_id    TEXT;
ALTER TABLE kb_chunks ADD COLUMN IF NOT EXISTS section_path TEXT;
ALTER TABLE kb_chunks ADD COLUMN IF NOT EXISTS is_parent    BOOLEAN NOT NULL DEFAULT false;
CREATE INDEX IF NOT EXISTS idx_kb_chunks_parent ON kb_chunks(parent_id);
-- 检索只在子块上建向量/BM25；父块 is_parent=true 仅按 id 回取，不进召回
```
入库（`ingest.go`）：用 `HierarchicalChunks(doc.Text, 1000, 260, 40)` 替换 `ChunkText(doc.Text,450,80)`；父块写入但 `EmbedBatched` 只对 `IsParent==false` 的子块向量化。

### 3.4 验收
- `make ingest-ms REBUILD=1` 后检查：每个 section 有 1 个父块 + 若干子块，子块带 parent_id；
- 新增单测：给一段含三级标题的 md，断言 breadcrumb 正确、子块 parent 指向正确、父块不建向量；
- 26 题无回归；用 `/api/search?query=转专业绩点要求` 人工对比改造前后命中是否更聚焦条款。

### 3.5 复制给编程助手的提示词
> **提示词 P-切分**
> Go 项目 gewu，internal/rag/ingest.go 现在用 ChunkText 按空行聚合到 450 字符做**单层**切分，kb_chunks 表只有 id/doc_id/seq/text。请实现 Markdown 结构切分 + 父子块：1) 新建 internal/rag/hierarchical.go，用正则按 #/##/### 标题切 section，维护标题栈生成 breadcrumb（"A > B > C"），父块=一个 section（聚合上限 1000 字符，超长复用现有 ChunkText 滑切），子块=父块内按段落聚合到 260 字符、overlap 40；2) 定义 Chunk{Text,SectionPath,IsParent,ParentKey}；3) kb_chunks 增加 parent_id/section_path/is_parent 列与 parent_id 索引（用 IF NOT EXISTS 幂等迁移）；4) 入库流程改为父子两层写入，但只对子块 EmbedBatched、只让子块进 BM25/向量召回，父块仅按 id 回取；5) 保留旧 ChunkText 与配置 CHUNK_MODE=flat|hierarchical 开关；6) 写单测验证三级标题 breadcrumb、父子映射、父块不建向量三件事。注意中文字符按 rune 计数，保持现有确定性（顺序可复现）。

---

## 4. 改造三：Rerank 精排 + 父子扩展（P0）

### 4.1 目标漏斗
```text
BM25 取 20 ∪ 向量取 20 → RRF 粗排 topN=20 →（有 key）Rerank 精排到 topK=4
→ 按子块 parent_id 回取父块、去重 → 用父块文本进上下文（保留子块命中分用于引用/排序）
无 key：跳过 rerank，RRF 后直接父子扩展（可复现基线）
```

### 4.2 参考代码（改 `internal/rag/retrieve.go`）

```go
// Search 升级：粗排 → rerank → 父子扩展。
func (r *Retriever) Search(ctx context.Context, query string, k int) ([]Hit, error) {
	query = r.rewriter.Expand(ctx, query)
	const pool = 20 // 粗排候选池（漏斗放宽）
	bm, _ := r.Store.BM25Search(query, pool)
	vec := r.vectorCandidates(ctx, query, pool) // 抽出现有向量路，失败降级

	ids := firstNIDs(RRFFuse([][]int{idsOf(bm), idsOf(vec)}, 60), pool)
	rows, _ := r.Store.ChunkRows(ids)

	// 1) Rerank：有 key 用 LLM pointwise 打分（也可换 cross-encoder 接口）
	if r.client != nil && r.client.HasKey() {
		ids = r.rerank(ctx, query, ids, rows, k)
	} else {
		ids = firstNIDs(ids, k)
	}
	// 2) 父子扩展：命中子块 → 回取父块去重
	return r.expandToParents(ctx, ids, rows, k)
}

// rerank 用 LLM 对候选逐条打相关性分（0~10），按分重排；解析失败原样返回（不阻断）。
func (r *Retriever) rerank(ctx context.Context, q string, ids []int, rows map[int]ChunkRow, k int) []int {
	type scored struct{ id int; s float64 }
	var out []scored
	for _, id := range ids {
		row := rows[id]
		score := r.llmScore(ctx, q, row.Text) // prompt：只输出 0~10 整数，批量更佳
		out = append(out, scored{id, score})
	}
	sort.SliceStable(out, func(i, j int) bool {
		if out[i].s != out[j].s { return out[i].s > out[j].s }
		return out[i].id < out[j].id // 平局确定性次序
	})
	top := make([]int, 0, k)
	for i, x := range out { if i >= k { break }; top = append(top, x.id) }
	return top
}

// expandToParents 子块命中 → 取父块；同父多子块去重，保留该父下最高子分排序。
func (r *Retriever) expandToParents(ctx context.Context, childIDs []int, _ map[int]ChunkRow, k int) ([]Hit, error) {
	childs, err := r.Store.ChunkRows(childIDs)
	if err != nil { return nil, err }
	bestParent := map[string]int{} // parentKey -> 最高命中序
	var order []string
	for rank, cid := range childIDs {
		c := childs[cid]
		pk := c.ParentID
		if pk == "" { pk = "__self__:" + strconv.Itoa(cid) } // 兼容 flat 模式
		if _, ok := bestParent[pk]; !ok { order = append(order, pk) }
		if _, ok := bestParent[pk]; !ok || rank < bestParent[pk] { bestParent[pk] = rank }
	}
	// 按最高子分排序父块、去重、取 k，文本用父块（命中引用仍可标注子块 section_path）
	return r.Store.ParentHitsOrdered(order, bestParent, k) // Store 新增方法
}
```

> LLM rerank 成本控制：把 pool 条拼**一个**批量打分 prompt（输出 JSON 数组），只 1 次调用；也可预留 `Reranker` 接口，接 bge-reranker / Cohere rerank，无 key 自动跳过。

### 4.3 验收
- 有/无 key 两条路径都跑 26 题，无回归；有 key 时记录 rerank 前后 top3 命中变化（抽 10 题人工标注相关性，给出 nDCG@3 或命中率对比）；
- 断言同父多子块只产生一个上下文块（去重生效）；flat 模式（无 parent_id）行为与改造前一致。

### 4.4 复制给编程助手的提示词
> **提示词 P-检索**
> Go 项目 gewu，internal/rag/retrieve.go 现在 BM25/向量各取 k*2 后 RRFFuse(k=60) 直接返回 k 个 chunk。请升级为"粗排→rerank→父子扩展"漏斗：1) 候选池放宽到每路 20、RRF 后取 topN=20；2) 新增 Reranker 接口与 LLM 实现（有 key 时把候选拼成一个批量 prompt 让模型对每条打 0~10 相关性分、输出 JSON 数组，按分稳定重排到 topK=4，解析失败/无 key 自动跳过且不阻断）；3) 结合父子块改造，命中子块后按 parent_id 回取父块、同父去重、按该父下最高子分排序，进上下文用父块文本、引用保留 section_path；4) flat 模式（parent_id 为空）保持与现在逐字一致；5) 给 rerank 和父子去重写单测（含平局确定性、同父多子去重、无 key 回退）。BM25 中文二元与 RRF k=60 保持不变。

---

## 5. 改造四：Memory 接入生成 + 事实固化（P0）

### 5.1 现状与目标
现状 `kb_memory` 只存不读进 prompt（ADR-0005）。目标：
- **分层**：短期=会话近 N 轮（conversation 已有）+ 更早摘要；长期=episodic（原始）+ semantic（抽取的事实/偏好，结构化可覆盖）；
- **写入**：会话结束异步 **consolidation**（LLM 从本轮对话抽用户事实 → `kb_memory_fact`，UPSERT 覆盖旧值，绝不在用户等待路径同步做）；
- **读取/装配**：直答/研究组装 prompt 时，按 session/user recall Top-K 相关记忆，作为**独立一层**插在 system 之后、RAG 知识之前；会话内冻结一次，避免每轮重复 embed。

### 5.2 表结构（services/rag）
```sql
CREATE TABLE IF NOT EXISTS kb_memory_fact (
  user_id TEXT NOT NULL, kind TEXT NOT NULL,      -- profile|preference|constraint
  key TEXT NOT NULL, value TEXT NOT NULL,         -- 如 key=grade_major value=计算机
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (user_id, kind, key)
);
```

### 5.3 参考代码：异步固化 + 分层装配
```go
// Consolidate 会话结束/每 N 轮后异步调用：抽取事实并 UPSERT（不阻塞回答）。
func (s *Server) Consolidate(ctx context.Context, userID string, transcript string) error {
	prompt := "从对话抽取关于该用户的稳定事实/偏好/约束，输出 JSON 数组 [{kind,key,value}]，无则 []。"
	raw, err := s.gen.Chat(ctx, []llm.Message{{Role: "system", Content: prompt}, {Role: "user", Content: transcript}},
		llm.Options{JSONMode: true, Temperature: 0, Small: true})
	if err != nil { return err }
	for _, f := range parseFacts(raw) { // UPSERT：新值覆盖旧值
		if _, err := s.store.db.ExecContext(ctx,
			`INSERT INTO kb_memory_fact(user_id,kind,key,value) VALUES($1,$2,$3,$4)
			 ON CONFLICT(user_id,kind,key) DO UPDATE SET value=$4,updated_at=now()`,
			userID, f.Kind, f.Key, f.Value); err != nil { s.log.Warn("fact upsert", zap.Error(err)) }
	}
	return nil
}

// BuildContext 分层装配（顺序固定，稳定内容前置以利 prompt cache）。
func (d *Deps) buildMessages(ctx context.Context, userID, sessionID, question string, hits []rag.Hit) []llm.Message {
	msgs := []llm.Message{{Role: "system", Content: AnswerSystem}}          // 1 system
	if mem := d.recallMemory(ctx, userID, sessionID, question, 5); mem != "" {
		msgs = append(msgs, llm.Message{Role: "system", Content: "已知用户信息：\n" + mem}) // 2 长期记忆
	}
	if ctx2, cites := numberedContext(hits); ctx2 != "" {                   // 3 RAG 知识
		msgs = append(msgs, llm.Message{Role: "system", Content: "参考资料：\n" + ctx2})
	}
	// 4 近 N 轮原文 + 更早摘要（conversation 服务取，超长先压工具结果再摘要历史）
	msgs = append(msgs, d.recentHistory(sessionID)...)
	msgs = append(msgs, llm.Message{Role: "user", Content: question})       // 5 当前问题
	return msgs
}
```

### 5.4 验收
- 单测：连续两轮告诉系统"我是计科专业"，`kb_memory_fact` UPSERT 后只有一条且 value 最新；第三轮回答能引用该事实；
- consolidation 失败/无 key 时主链路不受影响（记忆是增强不是依赖）；26 题无记忆数据时输出与现状一致（证明不污染基线）。

### 5.5 复制给编程助手的提示词
> **提示词 P-记忆**
> Go 项目 gewu，services/rag/memory.go 已有 kb_memory + 向量的 put/recall，但按 ADR-0005 没接入答案生成。请把记忆真正接入：1) 新增 kb_memory_fact(user_id,kind,key,value,updated_at) 表，主键 (user_id,kind,key)，UPSERT 覆盖；2) 实现异步 Consolidate：用小模型从会话记录抽取稳定事实/偏好 [{kind,key,value}] 并 UPSERT，失败只告警不影响主链路，且不在用户等待路径同步执行；3) 在直答/研究组装消息时按固定分层顺序装配：system → 长期记忆(fact + 相关 episodic TopK) → RAG 知识 → 近 N 轮历史+更早摘要 → 当前问题，长期记忆会话内只 recall 一次；4) 无记忆/无 key 时消息结构与现状逐字一致，保证 26 题 PARITY；5) 写单测覆盖事实覆盖更新、记忆注入、无记忆回退三种情况。

---

## 6. 改造五（P1，亮点）：通用 ReAct Loop，与 workflow 并存

### 6.1 定位（面试关键认知）
现在 `pipeline.go` 是 **workflow**（Anthropic 语境：代码固定路径，所以确定性、26/26 可复现）。补一个**单主体 ReAct agent**：LLM 自主决定调哪个工具、调几轮，工具就是现有检索/办理/计算能力的封装。**两者不是替代关系**——路由可把"目标明确但路径不定"的问题交给 ReAct，把"要求稳定可复现"的留给 workflow。

### 6.2 参考骨架（新建 `internal/agent/react.go`）
```go
type AgentTool interface {
	Name() string
	Schema() string // JSON schema 注入 system
	Run(ctx context.Context, args map[string]any) (string, error)
}

func (d *Deps) RunReAct(ctx context.Context, emit emitFn, question string, maxTurns int) error {
	msgs := []llm.Message{{Role: "system", Content: d.reactSystem()}, {Role: "user", Content: question}}
	seen := map[string]int{} // 工具结果指纹 → 次数，防死循环
	for turn := 0; turn < maxTurns; turn++ {
		raw, err := d.LLM.Chat(ctx, msgs, llm.Options{JSONMode: true, Temperature: 0}) // 输出 thought/action/action_input 或 final
		if err != nil { return err }
		step := parseAction(raw)
		if step.Final != "" { _ = emit(answerEvt(step.Final)); return nil } // 唯一终止判据：模型不再调工具
		fp := fingerprint(step.Action, step.Input)
		if seen[fp] >= 2 { // 同一调用重复 → 提示模型换法，第三次强制收敛
			msgs = append(msgs, llm.Message{Role: "user", Content: "该工具调用已重复，请换思路或直接给出当前最优结论。"})
			continue
		}
		seen[fp]++
		tool, ok := d.toolByName(step.Action) // 权限/工具集来自路由决策包 Toolset
		if !ok {
			msgs = append(msgs, llm.Message{Role: "user", Content: "工具不存在：" + step.Action})
			continue
		}
		if d.budget.Exceeded() { return ErrBudget } // token/步数预算熔断
		out, err := tool.Run(ctx, step.Input)
		if err != nil { out = "工具执行失败：" + err.Error() } // 错误回填为 observation，不中断会话
		_ = emit(stepEvt(step.Action, out))
		msgs = append(msgs, llm.Message{Role: "assistant", Content: raw},
			llm.Message{Role: "user", Content: "observation: " + out})
	}
	return ErrMaxTurns // 到顶兜底总结
}
```
要点（面试可讲）：唯一终止判据是"模型不再产出 tool_call"；指纹去重复防死循环；maxTurns + 预算熔断防失控；工具错误回填为 observation 而非断链；工具集受路由决策包约束（最小权限）。

### 6.3 复制给编程助手的提示词
> **提示词 P-ReAct**
> Go 项目 gewu 现在 internal/agent/pipeline.go 是 route→固定处理器的 workflow。请新增一个**单主体 ReAct 引擎**而不改动现有链路：1) 新建 react.go，定义 AgentTool 接口（Name/Schema/Run），把现有混合检索、场馆预约、请假、日期解析封装成工具；2) RunReAct 循环：模型每轮输出 {thought,action,action_input} 或 {final}，调工具后把结果作为 observation 回填，**final 是唯一终止条件**；3) 加防护：相同 (action,input) 指纹出现 3 次提示换路、maxTurns 上限、复用 internal/budget 做 token/步数熔断、工具错误回填不中断；4) 可用工具集受路由决策 RouteDecision.Toolset 约束；5) 通过路由新增一类"目标明确但路径不定"走 ReAct，其余仍走原 workflow，保证 26 题默认链路不变；6) 写单测覆盖：两轮工具后 final、重复指纹收敛、超 maxTurns 兜底、工具报错回填。

---

## 7. 改造六（P2，认知即可）：何时从暴力余弦换到 pgvector/HNSW

现状 `vec_pg.go` 用 BYTEA + 进程内暴力余弦是**合理的**：15 文档、chunk 数百级，全量在内存、毫秒返回、零重依赖、和单体数学同构。**不要为了"看起来主流"而提前优化**。但要能讲清切换触发线与做法：

- **触发线**：chunk 到万级以上 / 内存放不下全量向量 / 需要在 SQL 层同时做 metadata 过滤与 ANN / 多副本无法共享进程缓存；
- **做法**：`CREATE EXTENSION vector`；向量列改 `vector(1024)`；建 `USING hnsw (emb vector_cosine_ops)`（pgvector≥0.7、PG≥15；小库可用 ivfflat 且需先 ANALYZE 定 lists）；BM25 若要下沉 SQL 用 `tsvector + GIN`（中文需 zhparser，否则保留现有零依赖二元）；两路在 SQL 内用 RRF（`1/(60+rank)`）`FULL OUTER JOIN` 融合。
- **工程上**：`VecStore` 已抽象（pg-cosine / milvus 两实现），新增 `pgvector` 实现即可三选一，用配置切换——这本身就是"懂权衡、不过度设计"的加分项。

### 复制给编程助手的提示词（可选做）
> **提示词 P-pgvector**
> Go 项目 gewu 的 services/rag 已有 VecStore 抽象（vec_pg.go 进程内暴力余弦、vec_milvus.go）。请新增一个 pgvector 实现：启用 vector 扩展、向量列用 vector(dim)、建 hnsw cosine 索引，检索下推 SQL（带 metadata where），实现与现有 VecStore 相同接口；通过配置 VEC_BACKEND=cosine|pgvector|milvus 切换，默认仍 cosine；写一个基准测试对比三者在 1k/1w 向量下的 QPS 与召回，输出到 eval/reports。不要删除现有 cosine 实现。

---

## 8. 推荐改造顺序与总验收

1. **P-切分 → P-检索**（父子块是 rerank/父子扩展的前提，先做数据层）；
2. **P-路由**（级联 + 决策包，为 ReAct 的工具集约束铺路）；
3. **P-记忆**（依赖 conversation 历史，独立、风险低）；
4. **P-ReAct**（最后做，站在路由决策包和工具封装之上）；
5. P-pgvector 只在数据量真上来时做。

**每步统一门禁**：`go test ./...` 全绿 → `make eval` 26/26 无回归 → 有/无 key 两条链路都验证 → 在 `eval/reports/` 留一份前后对比（路由省调用比例、检索 nDCG/命中率、记忆注入案例）。全部用配置开关灰度，能一键回退到当前冻结基线。

## 9. 面试统一口径（诚实边界）
- 现在线/可跑：五分类路由、BM25+向量+RRF、Deep Research、办理状态机、26 题评测、双形态部署；
- P6 是**按业界范式做的演进**：级联路由、结构父子块、rerank、记忆固化与分层装配、ReAct 引擎——讲到哪项，必须是仓库里**真有代码、真有测试/对比报告**的；做到第几项讲第几项，没合入的就说"设计方案/进行中"，不提前宣称上线。
- 一句话定位：**"我用确定性 workflow 保证可复现基线，再用级联路由 + 父子块混合检索 + 分层记忆 + ReAct 引擎逐步逼近业界主流自主 Agent，两套链路并存、按问题类型路由、用离线评测守住不回归。"**
