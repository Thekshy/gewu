// conversation 会话服务入口：gRPC :9002。
package main

import (
	"google.golang.org/grpc"

	"gewu/internal/services/conversation"
	"gewu/internal/svcbase"
	conversationv1 "gewu/pkg/gen/gewu/conversation/v1"

	"go.uber.org/zap"
)

func main() {
	log := svcbase.NewLogger("conversation")
	ctx, stop := svcbase.MainSignalContext()
	defer stop()
	cfg := conversation.LoadConfig()
	err := svcbase.RunGRPC(ctx, svcbase.GRPCConfig{
		Name:      "conversation",
		Addr:      cfg.Addr,
		AdminAddr: cfg.AdminAddr,
		Register: func(s *grpc.Server) {
			conversationv1.RegisterConversationServiceServer(s, conversation.NewServer(log))
		},
	}, log)
	if err != nil {
		log.Fatal("服务退出", zap.Error(err))
	}
}
