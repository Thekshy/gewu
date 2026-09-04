package orchestrator

import (
	"context"

	orchestratorv1 "gewu/pkg/gen/gewu/orchestrator/v1"
)

// ConfigService 实现：/admin/*（gateway 转发）的配置管理后端。
// 新增命名空间，不触碰既有 /api/* 契约。

// GetConfig 当前生效配置（含是否默认行）。
func (s *Server) GetConfig(ctx context.Context, req *orchestratorv1.GetConfigRequest) (*orchestratorv1.GetConfigResponse, error) {
	cfg, isDefault, err := s.cfgStore.Get(ctx)
	if err != nil {
		return nil, internalStatus(err)
	}
	return &orchestratorv1.GetConfigResponse{
		Config:    toConfigProto(cfg),
		IsDefault: isDefault,
	}, nil
}

// SetConfig 更新配置（空字段保持不变），返回更新后的生效配置。
func (s *Server) SetConfig(ctx context.Context, req *orchestratorv1.SetConfigRequest) (*orchestratorv1.SetConfigResponse, error) {
	in := req.GetConfig()
	cfg, err := s.cfgStore.Set(ctx, AgentConfig{
		RouterSystem:   in.GetRouterSystem(),
		SlotExtractSys: in.GetSlotExtractSystem(),
		PlannerSystem:  in.GetPlannerSystem(),
		AnswerSystem:   in.GetAnswerSystem(),
	})
	if err != nil {
		return nil, internalStatus(err)
	}
	s.log.Info("agent_config 已更新")
	return &orchestratorv1.SetConfigResponse{Config: toConfigProto(cfg)}, nil
}

func toConfigProto(c AgentConfig) *orchestratorv1.AgentConfig {
	return &orchestratorv1.AgentConfig{
		RouterSystem:      c.RouterSystem,
		SlotExtractSystem: c.SlotExtractSys,
		PlannerSystem:     c.PlannerSystem,
		AnswerSystem:      c.AnswerSystem,
	}
}
