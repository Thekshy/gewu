package generate

import (
	"context"

	generatev1 "gewu/pkg/gen/gewu/generate/v1"

	"go.uber.org/zap"
)

// Server 生成服务：LLM 网关 + 预算集中计量。
// 降级决策留在调用方（orchestrator/rag）——本服务只如实报错。
type Server struct {
	generatev1.UnimplementedGenerateServiceServer
	log      *zap.Logger
	cfg      Config
	provider *provider
	meter    *budgetMeter
}

// NewServer 构造生成服务。
func NewServer(cfg Config, log *zap.Logger) *Server {
	meter := newBudgetMeter(cfg.DailyTokenBudget)
	return &Server{cfg: cfg, log: log, provider: newProvider(cfg, meter), meter: meter}
}

// BudgetStatus 今日预算状态（gateway /api/health 聚合用）。
func (s *Server) BudgetStatus(ctx context.Context, req *generatev1.BudgetStatusRequest) (*generatev1.BudgetStatusResponse, error) {
	used, limit := s.meter.snapshot()
	return &generatev1.BudgetStatusResponse{
		Used:   used,
		Limit:  limit,
		HasKey: s.provider.hasKey,
	}, nil
}

// EnsureBudget 预算预检（orchestrator 在 chat 入口调用）：
// 耗尽 → RESOURCE_EXHAUSTED（message = 429 文案逐字）。
func (s *Server) EnsureBudget(ctx context.Context, req *generatev1.EnsureBudgetRequest) (*generatev1.EnsureBudgetResponse, error) {
	if err := s.meter.ensure(); err != nil {
		return nil, err
	}
	return &generatev1.EnsureBudgetResponse{HasKey: s.provider.hasKey}, nil
}

// Chat 同步补全（路由/拆解/抽槽/改写等辅助调用）。
func (s *Server) Chat(ctx context.Context, req *generatev1.ChatRequest) (*generatev1.ChatResponse, error) {
	content, err := s.provider.chat(ctx, req.GetMessages(), req.GetOptions())
	if err != nil {
		return nil, err
	}
	return &generatev1.ChatResponse{Content: content}, nil
}

// ChatStream 流式补全（主答案）；ctx 取消（客户端断开）立刻中止上游读取。
func (s *Server) ChatStream(req *generatev1.ChatStreamRequest, stream generatev1.GenerateService_ChatStreamServer) error {
	err := s.provider.chatStream(stream.Context(), req.GetMessages(), req.GetOptions(), func(text string) error {
		return stream.Send(&generatev1.ChatStreamResponse{Text: text})
	})
	// Send 失败（orchestrator→gateway 链路已断）视作下游取消：不再包装为内部错误
	if err != nil && stream.Context().Err() != nil {
		return stream.Context().Err()
	}
	return err
}

// Embed 批量向量化（P3 rag 摄入与查询用）。
func (s *Server) Embed(ctx context.Context, req *generatev1.EmbedRequest) (*generatev1.EmbedResponse, error) {
	vectors, err := s.provider.embed(ctx, req.GetTexts())
	if err != nil {
		return nil, err
	}
	out := make([]*generatev1.Vector, 0, len(vectors))
	for _, v := range vectors {
		out = append(out, &generatev1.Vector{Values: v})
	}
	return &generatev1.EmbedResponse{Vectors: out}, nil
}
