package rag

import (
	"context"

	ragv1 "gewu/pkg/gen/gewu/rag/v1"

	"go.uber.org/zap"
)

// Server 检索服务实现。P0 骨架：Stats/ListDocs 返回空库（真实现 P3 接入，
// 届时 chunk 元数据进 PG、向量进 Milvus FLAT）；Search 继承 Unimplemented。
type Server struct {
	ragv1.UnimplementedRagServiceServer
	log *zap.Logger
}

// NewServer 构造检索服务。
func NewServer(log *zap.Logger) *Server { return &Server{log: log} }

// Stats P0 空库占位（/api/health 聚合依赖该 RPC 可用）。
func (s *Server) Stats(ctx context.Context, req *ragv1.StatsRequest) (*ragv1.StatsResponse, error) {
	return &ragv1.StatsResponse{Docs: 0, Chunks: 0, Embedded: false}, nil
}

// ListDocs P0 空列表占位。
func (s *Server) ListDocs(ctx context.Context, req *ragv1.ListDocsRequest) (*ragv1.ListDocsResponse, error) {
	return &ragv1.ListDocsResponse{Docs: []*ragv1.DocInfo{}}, nil
}
