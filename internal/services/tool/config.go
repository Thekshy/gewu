// Package tool 工具服务：工具注册中心 + 权限矩阵（PARITY §10）+ mock 业务系统
// 宿主（PARITY §8，SQLite→PG）。P0 为骨架——业务在 P4 接入。
package tool

import "gewu/internal/svcbase"

// Config 服务进程配置。
type Config struct {
	Addr      string
	AdminAddr string
}

// LoadConfig 从环境变量装配。
func LoadConfig() Config {
	return Config{
		Addr:      svcbase.EnvOr("TOOL_ADDR", ":9004"),
		AdminAddr: svcbase.EnvOr("TOOL_ADMIN_ADDR", ":9104"),
	}
}
