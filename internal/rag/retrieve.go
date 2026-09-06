package rag

import (
	"context"
	"errors"
	"log"
	"sort"
	"strconv"
	"strings"

	"gewu/internal/llm"
)

// LLMer 检索管线对 LLM 访问层的最小依赖（决策 A：冻结库唯一接口化改动）。
// 单体注入 *llm.Client 原样工作；微服务 rag 注入实现该接口的 generate RPC
// 客户端——检索编排仍是这一份代码，行为对齐不靠拷贝。
type LLMer interface {
	HasKey() bool
	Chat(ctx context.Context, messages []llm.Message, o llm.Options) (string, error)
	Embed(ctx context.Context, texts []string) ([][]float64, error)
}

// ErrMissingVectors 索引无向量（-no-embed 入库 / 旧索引未重建）时检索的明确失败。
// P6 阶段0 去静默降级：不再悄悄退成纯 BM25，避免"以为有向量其实没有"。
var ErrMissingVectors = errors.New("向量索引缺失，请配好 EMBED_* 后用 -rebuild 重建索引")

// 检索漏斗宽度（P6 阶段3）：BM25/向量各取 poolN 条 → RRF 粗排 →
// （有 key 且开启 rerank）LLM 精排到 topK → 按 parent_id 回取父块去重。
const poolN = 20

// Retriever 混合检索：BM25 + 向量 → RRF 融合 →（可选）LLM 精排 → 父子扩展。
type Retriever struct {
	Store    *Store
	K        int
	client   LLMer
	rewriter *Rewriter
	reranker Reranker // nil = 关闭精排（RERANK_MODE=off 或无 key 时自动跳过）
}

// NewRetriever 构造检索器（默认不带精排，可用 WithReranker 开启）。
func NewRetriever(store *Store, k int, client LLMer) *Retriever {
	return &Retriever{Store: store, K: k, client: client, rewriter: NewRewriter(client)}
}

// WithReranker 设置精排器（链式）。
func (r *Retriever) WithReranker(rr Reranker) *Retriever {
	r.reranker = rr
	return r
}

// Search 执行混合检索，返回前 k 条命中（hierarchical 模式下为父块文本）。
func (r *Retriever) Search(ctx context.Context, query string, k int) ([]Hit, error) {
	if k <= 0 {
		k = r.K
	}
	query = r.rewriter.Expand(ctx, query) // 口语 → 政策术语（无 key 时原样返回）

	hasEmb, err := r.Store.HasEmbeddings()
	if err != nil {
		return nil, err
	}
	if !hasEmb {
		return nil, ErrMissingVectors
	}

	pool := poolN
	if k > pool {
		pool = k
	}
	bmScored, err := r.Store.BM25Search(query, pool)
	if err != nil {
		return nil, err
	}
	bmIDs := make([]int, len(bmScored))
	for i, sc := range bmScored {
		bmIDs[i] = sc.ID
	}

	vecIDs := r.vectorCandidates(ctx, query, pool)

	fused := firstNIDs(bmIDs, k)
	if len(vecIDs) > 0 {
		fused = firstNIDs(RRFFuse([][]int{bmIDs, vecIDs}, 60), pool)
	} else if len(bmIDs) > pool {
		fused = firstNIDs(bmIDs, pool)
	}

	fused = r.rerankOrKeep(ctx, query, fused, k)
	return r.expandToParents(fused, k)
}

// vectorCandidates 向量召回路：查询向量失败（网络抖动等瞬时错误）时记日志
// 并退化为纯 BM25 召回——这是单次查询的运行时容错，不是索引级静默降级。
func (r *Retriever) vectorCandidates(ctx context.Context, query string, k int) []int {
	if r.client == nil || !r.client.HasKey() {
		return nil
	}
	vecs, err := r.client.Embed(ctx, []string{query})
	if err != nil || len(vecs) == 0 {
		log.Printf("[rag] 查询向量化失败，本查询退化为纯 BM25：%v", err)
		return nil
	}
	scored, err := r.Store.VectorSearch(vecs[0], k)
	if err != nil {
		log.Printf("[rag] 向量检索失败，本查询退化为纯 BM25：%v", err)
		return nil
	}
	ids := make([]int, len(scored))
	for i, sc := range scored {
		ids[i] = sc.ID
	}
	return ids
}

// rerankOrKeep 有 key 且配置了精排器时，把候选拼进一个批量 prompt 打 0~10 分，
// 按分稳定重排（平局保持 RRF 次序）；失败/关闭时原样返回（不阻断）。
func (r *Retriever) rerankOrKeep(ctx context.Context, query string, ids []int, k int) []int {
	if r.reranker == nil || r.client == nil || !r.client.HasKey() || len(ids) <= k {
		return firstNIDs(ids, k)
	}
	rows, err := r.Store.ChunkRows(ids)
	if err != nil {
		return firstNIDs(ids, k)
	}
	texts := make([]string, len(ids))
	for i, cid := range ids {
		texts[i] = rows[cid].Text
	}
	scores, err := r.reranker.Rerank(ctx, query, texts)
	if err != nil {
		log.Printf("[rag] rerank 失败，退回 RRF 粗排顺序：%v", err)
		return firstNIDs(ids, k)
	}
	order := make([]int, len(ids))
	for i := range order {
		order[i] = i
	}
	sort.SliceStable(order, func(a, b int) bool {
		if scores[order[a]] != scores[order[b]] {
			return scores[order[a]] > scores[order[b]]
		}
		return order[a] < order[b] // 平局确定性次序：保持 RRF 相对顺序
	})
	out := make([]int, 0, k)
	for _, i := range order {
		if len(out) >= k {
			break
		}
		out = append(out, ids[i])
	}
	return out
}

// expandToParents 父子扩展：命中子块按 parent_id 回取父块，同父多子块去重
// （取该父下最高命中的子块位次排序），进上下文用父块文本。
// flat 模式（parent_id 为空）每个命中自成一块，行为与改造前逐字一致。
func (r *Retriever) expandToParents(ids []int, k int) ([]Hit, error) {
	childRows, err := r.Store.ChunkRows(ids)
	if err != nil {
		return nil, err
	}
	type slot struct {
		parentKey string // 父块 parent_id；flat 兜底 "__self__:<childID>"
		childID   int
	}
	slots := make([]slot, 0, len(ids))
	bestRank := map[string]int{}
	var order []string
	for rank, cid := range ids {
		row, ok := childRows[cid]
		if !ok {
			continue
		}
		pk := row.ParentID
		if pk == "" {
			pk = "__self__:" + strconv.Itoa(cid)
		}
		if _, seen := bestRank[pk]; !seen {
			bestRank[pk] = rank
			order = append(order, pk)
			slots = append(slots, slot{parentKey: pk, childID: cid})
		}
	}
	if len(slots) > k {
		slots = slots[:k]
		order = order[:k]
	}

	// 批量取父块行与文档元信息。
	var parentIDs []string
	docIDs := make([]string, 0, len(slots))
	for _, sl := range slots {
		if !strings.HasPrefix(sl.parentKey, "__self__:") {
			parentIDs = append(parentIDs, sl.parentKey)
		}
		docIDs = append(docIDs, childRows[sl.childID].DocID)
	}
	parentRows, err := r.Store.ParentRows(parentIDs)
	if err != nil {
		return nil, err
	}
	metas, err := r.Store.DocMetaMap(docIDs)
	if err != nil {
		return nil, err
	}

	hits := make([]Hit, 0, len(slots))
	for _, sl := range slots {
		child := childRows[sl.childID]
		meta := metas[child.DocID]
		if prow, ok := parentRows[sl.parentKey]; ok && prow.IsParent {
			meta = metas[prow.DocID]
			hits = append(hits, Hit{
				ChunkID:     mustAtoi(sl.parentKey, sl.childID),
				DocID:       prow.DocID,
				Seq:         prow.Seq,
				Text:        prow.Text,
				Title:       orDefault(meta.Title, prow.DocID),
				Source:      meta.Source,
				SectionPath: prow.SectionPath,
			})
			continue
		}
		// flat 模式或父块缺失：命中子块即命中本身（与旧行为一致）。
		hits = append(hits, Hit{
			ChunkID:     sl.childID,
			DocID:       child.DocID,
			Seq:         child.Seq,
			Text:        child.Text,
			Title:       orDefault(meta.Title, child.DocID),
			Source:      meta.Source,
			SectionPath: child.SectionPath,
		})
	}
	return hits, nil
}

func mustAtoi(s string, def int) int {
	if n, err := strconv.Atoi(s); err == nil {
		return n
	}
	return def
}

func orDefault(v, def string) string {
	if v == "" {
		return def
	}
	return v
}

// firstNIDs 切片安全截取（Python list[:k] 对短列表的等价行为）。
func firstNIDs(list []int, k int) []int {
	if len(list) <= k {
		return list
	}
	return list[:k]
}
