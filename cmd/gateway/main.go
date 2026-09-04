// gateway 微服务网关入口：gin :8000，对外唯一 HTTP/SSE 端点。
package main

import (
	"gewu/internal/services/gateway"
	"gewu/internal/svcbase"

	"go.uber.org/zap"
)

func main() {
	log := svcbase.NewLogger("gateway")
	ctx, stop := svcbase.MainSignalContext()
	defer stop()
	if err := gateway.Run(ctx, gateway.LoadConfig(), log); err != nil {
		log.Fatal("网关退出", zap.Error(err))
	}
}
