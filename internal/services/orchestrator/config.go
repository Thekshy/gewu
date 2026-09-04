package orchestrator

import "gewu/internal/svcbase"

// Config 服务进程配置。
type Config struct {
	Addr      string // gRPC 监听地址
	AdminAddr string // 管理端口（/healthz）

	GenerateAddr string // generate 服务地址（LLM 网关 + 预算）
}

// LoadConfig 从环境变量装配（缺省本机开发端口）。
func LoadConfig() Config {
	return Config{
		Addr:      svcbase.EnvOr("ORCHESTRATOR_ADDR", ":9001"),
		AdminAddr: svcbase.EnvOr("ORCHESTRATOR_ADMIN_ADDR", ":9101"),

		GenerateAddr: svcbase.NormalizeTarget(svcbase.EnvOr("GENERATE_ADDR", ":9003")),
	}
}
