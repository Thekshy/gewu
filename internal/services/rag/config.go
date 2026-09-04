// Package rag 检索服务：混合检索（复用冻结库 internal/rag）+ 摄入流水线（P3）
// + 长期记忆（P5）。P0 骨架——Stats/ListDocs 返回空库结果（health 聚合依赖），
// 真实索引在 P3 接入。
package rag

import "gewu/internal/svcbase"

// Config 服务进程配置。
type Config struct {
	Addr      string
	AdminAddr string
}

// LoadConfig 从环境变量装配。
func LoadConfig() Config {
	return Config{
		Addr:      svcbase.EnvOr("RAG_ADDR", ":9005"),
		AdminAddr: svcbase.EnvOr("RAG_ADMIN_ADDR", ":9105"),
	}
}
