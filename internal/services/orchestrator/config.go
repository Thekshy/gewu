// Package orchestrator 编排服务：会话编排管线（PARITY §4）+ 五分类路由 +
// 工具调度 + agent_config 管理。P0 为骨架——业务在 P1~P4 逐阶段接入。
package orchestrator

import "gewu/internal/svcbase"

// Config 服务进程配置。
type Config struct {
	Addr      string // gRPC 监听地址
	AdminAddr string // 管理端口（/healthz）
}

// LoadConfig 从环境变量装配（缺省本机开发端口）。
func LoadConfig() Config {
	return Config{
		Addr:      svcbase.EnvOr("ORCHESTRATOR_ADDR", ":9001"),
		AdminAddr: svcbase.EnvOr("ORCHESTRATOR_ADMIN_ADDR", ":9101"),
	}
}
