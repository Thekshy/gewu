package rag

import (
	"context"
	"log"

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

// Retriever 混合检索：BM25 + 向量（可用时）→ RRF 融合 → 命中附文档元信息。
type Retriever struct {
	Store    *Store
	K        int
	client   LLMer
	rewriter *Rewriter
}

// NewRetriever 构造检索器。client 为 nil（零 key 模式）时只走 BM25。
func NewRetriever(store *Store, k int, client LLMer) *Retriever {
	return &Retriever{Store: store, K: k, client: client, rewriter: NewRewriter(client)}
}

// Search 执行混合检索，返回前 k 条命中。
func (r *Retriever) Search(ctx context.Context, query string, k int) ([]Hit, error) {
	if k <= 0 {
		k = r.K
	}
	query = r.rewriter.Expand(ctx, query) // 口语 → 政策术语（无 key 时原样返回）

	bmScored, err := r.Store.BM25Search(query, k*2)
	if err != nil {
		return nil, err
	}
	bmIDs := make([]int, len(bmScored))
	for i, sc := range bmScored {
		bmIDs[i] = sc.ID
	}

	var vecIDs []int
	hasEmb, err := r.Store.HasEmbeddings()
	if err != nil {
		return nil, err
	}
	if hasEmb && r.client != nil && r.client.HasKey() {
		vecs, err := r.client.Embed(ctx, []string{query})
		if err == nil && len(vecs) > 0 {
			scored, err := r.Store.VectorSearch(vecs[0], k*2)
			if err == nil {
				vecIDs = make([]int, len(scored))
				for i, sc := range scored {
					vecIDs[i] = sc.ID
				}
			}
		} else if err != nil {
			log.Printf("[rag] 向量检索失败，本查询降级为纯 BM25：%v", err)
		}
	}

	fused := firstNIDs(bmIDs, k)
	if len(vecIDs) > 0 {
		fused = firstNIDs(RRFFuse([][]int{bmIDs, vecIDs}, 60), k)
	}

	rows, err := r.Store.ChunkRows(fused)
	if err != nil {
		return nil, err
	}
	docIDs := make([]string, 0, len(rows))
	for _, row := range rows {
		docIDs = append(docIDs, row.DocID)
	}
	metas, err := r.Store.DocMetaMap(docIDs)
	if err != nil {
		return nil, err
	}

	hits := make([]Hit, 0, len(fused))
	for _, cid := range fused {
		row, ok := rows[cid]
		if !ok {
			continue
		}
		meta := metas[row.DocID]
		hits = append(hits, Hit{
			ChunkID: cid,
			DocID:   row.DocID,
			Seq:     row.Seq,
			Text:    row.Text,
			Title:   orDefault(meta.Title, row.DocID),
			Source:  meta.Source,
		})
	}
	return hits, nil
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
