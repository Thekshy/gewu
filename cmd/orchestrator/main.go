// orchestrator 编排服务入口：gRPC :9001。
package main

import (
	"context"

	"gewu/internal/services/orchestrator"
	"gewu/internal/svcbase"

	"go.uber.org/zap"
)

func main() {
	log := svcbase.NewLogger("orchestrator")
	ctx, stop := svcbase.MainSignalContext()
	defer stop()
	cfg := orchestrator.LoadConfig()
	srv, err := orchestrator.NewServer(cfg, log)
	if err != nil {
		log.Fatal("构造编排服务失败", zap.Error(err))
	}
	// key 状态后台探测（不阻塞监听；探明前默认零 key 走确定性链路，
	// 每次 Chat 的 EnsureBudget 也会带回 has_key 自动校正）
	go srv.InitHasKey(context.Background())
	err = svcbase.RunGRPC(ctx, svcbase.GRPCConfig{
		Name:      "orchestrator",
		Addr:      cfg.Addr,
		AdminAddr: cfg.AdminAddr,
		Register:  srv.Register,
	}, log)
	if err != nil {
		log.Fatal("服务退出", zap.Error(err))
	}
}
