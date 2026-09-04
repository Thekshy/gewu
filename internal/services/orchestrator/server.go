package orchestrator

import (
	orchestratorv1 "gewu/pkg/gen/gewu/orchestrator/v1"

	"gewu/internal/svcbase"

	"go.uber.org/zap"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"
)

// Server 编排服务实现。P0 骨架：Chat 返回 Unimplemented，
// 管线（续轮意图→路由→五分支分发）在 P1 起从冻结单体逐字迁移。
type Server struct {
	orchestratorv1.UnimplementedChatServiceServer
	log *zap.Logger
}

// NewServer 构造编排服务。
func NewServer(log *zap.Logger) *Server { return &Server{log: log} }

// Chat P0 占位：P1 接入最小闭环（gateway SSE → 管线 → generate 流式回传）。
func (s *Server) Chat(req *orchestratorv1.ChatRequest, stream orchestratorv1.ChatService_ChatServer) error {
	s.log.Info("chat 未接入（P0 骨架）",
		zap.String("trace_id", svcbase.TraceID(stream.Context())),
		zap.String("session_id", req.SessionId))
	return status.Error(codes.Unimplemented, "编排管线在 P1 接入（P0 脚手架）")
}
