// tool 工具服务入口：gRPC :9004（权限矩阵 + 业务系统宿主）。
package main

import (
	"google.golang.org/grpc"

	"gewu/internal/services/tool"
	"gewu/internal/svcbase"
	toolv1 "gewu/pkg/gen/gewu/tool/v1"

	"go.uber.org/zap"
)

func main() {
	log := svcbase.NewLogger("tool")
	ctx, stop := svcbase.MainSignalContext()
	defer stop()
	cfg := tool.LoadConfig()
	err := svcbase.RunGRPC(ctx, svcbase.GRPCConfig{
		Name:      "tool",
		Addr:      cfg.Addr,
		AdminAddr: cfg.AdminAddr,
		Register: func(s *grpc.Server) {
			toolv1.RegisterToolServiceServer(s, tool.NewServer(log))
		},
	}, log)
	if err != nil {
		log.Fatal("服务退出", zap.Error(err))
	}
}
