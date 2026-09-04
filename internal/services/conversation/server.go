package conversation

import (
	conversationv1 "gewu/pkg/gen/gewu/conversation/v1"

	"go.uber.org/zap"
)

// Server 会话服务实现。P0 骨架：继承 Unimplemented（全部 RPC 返回
// codes.Unimplemented），P2 落地 PG 存储 + TTL 30 分钟惰性清理
// （PARITY §12.3 逐字）+ 消息落库（审计，不参与答案生成）。
type Server struct {
	conversationv1.UnimplementedConversationServiceServer
	log *zap.Logger
}

// NewServer 构造会话服务。
func NewServer(log *zap.Logger) *Server { return &Server{log: log} }
