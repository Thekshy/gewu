// generate 生成服务入口：gRPC :9003（LLM 网关 + 预算计量）。
package main

import (
	"google.golang.org/grpc"

	"gewu/internal/services/generate"
	"gewu/internal/svcbase"
	generatev1 "gewu/pkg/gen/gewu/generate/v1"

	"go.uber.org/zap"
)

func main() {
	log := svcbase.NewLogger("generate")
	ctx, stop := svcbase.MainSignalContext()
	defer stop()
	cfg := generate.LoadConfig()
	err := svcbase.RunGRPC(ctx, svcbase.GRPCConfig{
		Name:      "generate",
		Addr:      cfg.Addr,
		AdminAddr: cfg.AdminAddr,
		Register: func(s *grpc.Server) {
			generatev1.RegisterGenerateServiceServer(s, generate.NewServer(cfg, log))
		},
	}, log)
	if err != nil {
		log.Fatal("服务退出", zap.Error(err))
	}
}
