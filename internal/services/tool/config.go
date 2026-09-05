// Package tool 工具服务：工具注册中心 + 权限矩阵（PARITY §10）+ mock 业务系统
// 宿主（PARITY §8，SQLite→PG 迁移；校验顺序/文案/单号/时区逐字）。
// 敏感参数 user 一律从 gRPC metadata（x-user）注入，调用方不可传。
package tool

import (
	"path/filepath"

	"gewu/internal/svcbase"
)

// Config 服务进程配置。
type Config struct {
	Addr      string
	AdminAddr string

	PostgresDSN string // 业务库存储
}

// LoadConfig 从环境变量装配。
func LoadConfig() Config {
	dataDir := svcbase.EnvOr("DATA_DIR", "data")
	_ = filepath.Join(dataDir, "business.db") // 仅文档锚点：P4 起业务库在 PG
	return Config{
		Addr:        svcbase.EnvOr("TOOL_ADDR", ":9004"),
		AdminAddr:   svcbase.EnvOr("TOOL_ADMIN_ADDR", ":9104"),
		PostgresDSN: svcbase.EnvOr("POSTGRES_DSN", ""),
	}
}
