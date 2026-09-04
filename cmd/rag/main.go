// rag 检索服务入口：gRPC :9005（混合检索 + 摄入 + 记忆）。
package main

import (
	"google.golang.org/grpc"

	"gewu/internal/services/rag"
	"gewu/internal/svcbase"
	ragv1 "gewu/pkg/gen/gewu/rag/v1"

	"go.uber.org/zap"
)

func main() {
	log := svcbase.NewLogger("rag")
	ctx, stop := svcbase.MainSignalContext()
	defer stop()
	cfg := rag.LoadConfig()
	err := svcbase.RunGRPC(ctx, svcbase.GRPCConfig{
		Name:      "rag",
		Addr:      cfg.Addr,
		AdminAddr: cfg.AdminAddr,
		Register: func(s *grpc.Server) {
			ragv1.RegisterRagServiceServer(s, rag.NewServer(log))
		},
	}, log)
	if err != nil {
		log.Fatal("服务退出", zap.Error(err))
	}
}
