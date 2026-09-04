package orchestrator

import (
	"context"

	ragv1 "gewu/pkg/gen/gewu/rag/v1"

	"gewu/internal/rag"

	"go.uber.org/zap"
)

// ragSearch 经 rag 服务检索（本地 Hit 形态对齐冻结 rag.Hit）。
// rag 未接线（单测装配）时返回空——按「无命中」语义处理。
func (s *Server) ragSearch(ctx context.Context, query string, k int) []rag.Hit {
	if s.rag == nil {
		return nil
	}
	if k <= 0 {
		k = s.cfg.RetrievalK
	}
	resp, err := s.rag.Search(ctx, &ragv1.SearchRequest{Query: query, K: int32(k)})
	if err != nil {
		// 检索失败按「无命中」处理（单体语义：Search 错误会中止整轮——
		// 微服务侧选择降级为无数据，避免基础设施抖动放大为整轮失败；
		// 差异见 PARITY-MS）
		s.log.Warn("检索失败，本轮按无命中处理", zap.Error(err))
		return nil
	}
	hits := make([]rag.Hit, 0, len(resp.GetHits()))
	for _, h := range resp.GetHits() {
		hits = append(hits, rag.Hit{
			ChunkID: int(h.GetChunkId()), DocID: h.GetDocId(), Seq: int(h.GetSeq()),
			Text: h.GetText(), Title: h.GetTitle(), Source: h.GetSource(),
		})
	}
	return hits
}
