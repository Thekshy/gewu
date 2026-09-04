// Package conversation 会话服务：办理流程跨轮状态（PARITY §12.3）+
// 消息落库（审计）。P0 为骨架——状态存储在 P2 接入（PG + TTL 语义逐字迁移）。
package conversation

import "gewu/internal/svcbase"

// Config 服务进程配置。
type Config struct {
	Addr      string
	AdminAddr string
}

// LoadConfig 从环境变量装配。
func LoadConfig() Config {
	return Config{
		Addr:      svcbase.EnvOr("CONVERSATION_ADDR", ":9002"),
		AdminAddr: svcbase.EnvOr("CONVERSATION_ADMIN_ADDR", ":9102"),
	}
}
