// orchestrator 编排服务入口：gRPC :9001。
package main

import (
	"google.golang.org/grpc"

	"gewu/internal/services/orchestrator"
	"gewu/internal/svcbase"
	orchestratorv1 "gewu/pkg/gen/gewu/orchestrator/v1"

	"go.uber.org/zap"
)

func main() {
	log := svcbase.NewLogger("orchestrator")
	ctx, stop := svcbase.MainSignalContext()
	defer stop()
	cfg := orchestrator.LoadConfig()
	err := svcbase.RunGRPC(ctx, svcbase.GRPCConfig{
		Name:      "orchestrator",
		Addr:      cfg.Addr,
		AdminAddr: cfg.AdminAddr,
		Register: func(s *grpc.Server) {
			orchestratorv1.RegisterChatServiceServer(s, orchestrator.NewServer(log))
		},
	}, log)
	if err != nil {
		log.Fatal("服务退出", zap.Error(err))
	}
}
