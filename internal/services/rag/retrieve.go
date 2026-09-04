package rag

import (
	"context"

	"gewu/internal/rag"
)

// search 混合检索编排（拷贝改造自冻结 internal/rag/retrieve.go 的 Search：
// 参数与顺序逐字——改写→BM25 k*2→向量 k*2（可用时）→RRF(k=60)→组装 Hit；
// BM25/RRF/分词公式为同一份代码（internal/rag），存储换 PG + VectorStore）。
func (s *Server) search(ctx context.Context, query string, k int) ([]rag.Hit, error) {
	if k <= 0 {
		k = s.cfg.RetrievalK
	}
	s.gen.refreshKey(ctx) // key 状态同步（env 固定后不变，惰性刷新兜底）
	query = s.rewriter.Expand(ctx, query)

	bmScored, err := s.store.BM25Search(query, k*2)
	if err != nil {
		return nil, err
	}
	bmIDs := make([]int, len(bmScored))
	for i, sc := range bmScored {
		bmIDs[i] = sc.ID
	}

	var vecIDs []int
	hasEmb, err := s.vectors.Has(ctx)
	if err != nil {
		return nil, err
	}
	if hasEmb && s.gen.HasKey() {
		vecs, err := s.gen.Embed(ctx, []string{query})
		if err == nil && len(vecs) > 0 {
			scored, verr := s.vectors.Search(ctx, vecs[0], k*2)
			if verr == nil {
				vecIDs = make([]int, len(scored))
				for i, sc := range scored {
					vecIDs[i] = sc.ID
				}
			}
		} else if err != nil {
			s.log.Warn("向量检索失败，本查询降级为纯 BM25", zapErr(err))
		}
	}

	fused := firstNIDs(bmIDs, k)
	if len(vecIDs) > 0 {
		fused = firstNIDs(rag.RRFFuse([][]int{bmIDs, vecIDs}, 60), k)
	}

	rows, err := s.store.ChunkRows(ctx, fused)
	if err != nil {
		return nil, err
	}
	docIDs := make([]string, 0, len(rows))
	for _, row := range rows {
		docIDs = append(docIDs, row.DocID)
	}
	metas, err := s.store.DocMetaMap(ctx, docIDs)
	if err != nil {
		return nil, err
	}

	hits := make([]rag.Hit, 0, len(fused))
	for _, cid := range fused {
		row, ok := rows[cid]
		if !ok {
			continue
		}
		meta := metas[row.DocID]
		title := row.DocID
		if meta.Title != "" {
			title = meta.Title
		}
		hits = append(hits, rag.Hit{
			ChunkID: cid,
			DocID:   row.DocID,
			Seq:     row.Seq,
			Text:    row.Text,
			Title:   title,
			Source:  meta.Source,
		})
	}
	return hits, nil
}

// firstNIDs 切片安全截取（与冻结实现一致）。
func firstNIDs(list []int, k int) []int {
	if len(list) <= k {
		return list
	}
	return list[:k]
}
