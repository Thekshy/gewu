package orchestrator

import (
	"path/filepath"

	"gewu/internal/svcbase"
)

// Config 服务进程配置。
type Config struct {
	Addr      string // gRPC 监听地址
	AdminAddr string // 管理端口（/healthz）

	GenerateAddr     string // generate 服务地址（LLM 网关 + 预算）
	ConversationAddr string // conversation 服务地址（会话状态）

	PostgresDSN string // agent_config 存储；空 = 内存模式
	RedisAddr   string // 配置缓存（L2）；空 = 跳过

	BusinessDBPath string // 冻结业务库（P4 迁 tool 服务前的过渡）
}

// LoadConfig 从环境变量装配（缺省本机开发端口）。
func LoadConfig() Config {
	dataDir := svcbase.EnvOr("DATA_DIR", "data")
	return Config{
		Addr:      svcbase.EnvOr("ORCHESTRATOR_ADDR", ":9001"),
		AdminAddr: svcbase.EnvOr("ORCHESTRATOR_ADMIN_ADDR", ":9101"),

		GenerateAddr:     svcbase.NormalizeTarget(svcbase.EnvOr("GENERATE_ADDR", ":9003")),
		ConversationAddr: svcbase.NormalizeTarget(svcbase.EnvOr("CONVERSATION_ADDR", ":9002")),

		PostgresDSN: svcbase.EnvOr("POSTGRES_DSN", ""),
		RedisAddr:   svcbase.EnvOr("REDIS_ADDR", ""),

		BusinessDBPath: filepath.Join(dataDir, "business.db"),
	}
}
