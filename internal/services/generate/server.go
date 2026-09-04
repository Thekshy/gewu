package generate

import (
	"context"

	generatev1 "gewu/pkg/gen/gewu/generate/v1"

	"go.uber.org/zap"
)

// Server 生成服务实现。P0 骨架：BudgetStatus 返回真实配置
// （gateway /api/health 聚合依赖）；Chat/ChatStream/Embed 继承 Unimplemented，
// P1 接入 provider（含预算预检与计量）。
type Server struct {
	generatev1.UnimplementedGenerateServiceServer
	log    *zap.Logger
	cfg    Config
	hasKey bool
}

// NewServer 构造生成服务。
func NewServer(cfg Config, log *zap.Logger) *Server {
	return &Server{cfg: cfg, hasKey: cfg.APIKey != "", log: log}
}

// BudgetStatus 今日预算状态（P0：used 恒 0——计量在 P1 随 provider 接入落地）。
func (s *Server) BudgetStatus(ctx context.Context, req *generatev1.BudgetStatusRequest) (*generatev1.BudgetStatusResponse, error) {
	return &generatev1.BudgetStatusResponse{
		Used:   0,
		Limit:  s.cfg.DailyTokenBudget,
		HasKey: s.hasKey,
	}, nil
}
