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
	store, closeStore, err := cfg.OpenStore()
	if err != nil {
		log.Fatal("会话存储初始化失败", zap.Error(err))
	}
	defer closeStore()
	if _, memory := store.(*conversation.MemoryStore); memory {
		log.Warn("会话存储运行在内存模式（POSTGRES_DSN 未配置），重启后会话丢失")
	}
	err = svcbase.RunGRPC(ctx, svcbase.GRPCConfig{
		Name:      "conversation",
		Addr:      cfg.Addr,
		AdminAddr: cfg.AdminAddr,
		Register: func(s *grpc.Server) {
			conversationv1.RegisterConversationServiceServer(s, conversation.NewServer(store, log))
		},
	}, log)
	if err != nil {
		log.Fatal("服务退出", zap.Error(err))
	}
}
