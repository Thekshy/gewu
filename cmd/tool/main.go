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
	srv, err := tool.NewServer(cfg, log)
	if err != nil {
		log.Fatal("构造工具服务失败", zap.Error(err))
	}
	defer srv.Close()
	err = svcbase.RunGRPC(ctx, svcbase.GRPCConfig{
		Name:      "tool",
		Addr:      cfg.Addr,
		AdminAddr: cfg.AdminAddr,
		Register: func(g *grpc.Server) {
			toolv1.RegisterToolServiceServer(g, srv)
		},
	}, log)
	if err != nil {
		log.Fatal("服务退出", zap.Error(err))
	}
}
