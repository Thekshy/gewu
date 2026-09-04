// Package gateway 对外 HTTP 网关（gin，:8000）：SSE 透传、按 IP 令牌桶限流、
// CORS、身份派生（role → demo-{role}，不信任客户端传 user）、/api/* 分发与
// /admin/* 转发。P0 骨架：/api/health 已聚合下游（决策 D：失败 503），
// 其余端点在 P1~P4 接入。
package gateway

import (
	"strconv"

	"gewu/internal/svcbase"
)

// Config 网关进程配置。
type Config struct {
	Addr      string // 对外 HTTP（:8000，契约端口）
	AdminAddr string // 管理端口 /healthz（compose 探针，不经限流）

	OrchestratorAddr string
	ConversationAddr string
	GenerateAddr     string
	ToolAddr         string
	RagAddr          string

	RateLimitPerMinute int

	MaxQuestionChars int // 问题长度上限（PARITY §2.4，缺省 500）
}

// LoadConfig 从环境变量装配。
func LoadConfig() Config {
	rate := 20
	if v, err := strconv.Atoi(svcbase.EnvOr("RATE_LIMIT_PER_MINUTE", "")); err == nil && v > 0 {
		rate = v
	}
	return Config{
		Addr:      svcbase.EnvOr("GATEWAY_ADDR", ":8000"),
		AdminAddr: svcbase.EnvOr("GATEWAY_ADMIN_ADDR", ":9100"),

		OrchestratorAddr: svcbase.NormalizeTarget(svcbase.EnvOr("ORCHESTRATOR_ADDR", ":9001")),
		ConversationAddr: svcbase.NormalizeTarget(svcbase.EnvOr("CONVERSATION_ADDR", ":9002")),
		GenerateAddr:     svcbase.NormalizeTarget(svcbase.EnvOr("GENERATE_ADDR", ":9003")),
		ToolAddr:         svcbase.NormalizeTarget(svcbase.EnvOr("TOOL_ADDR", ":9004")),
		RagAddr:          svcbase.NormalizeTarget(svcbase.EnvOr("RAG_ADDR", ":9005")),

		RateLimitPerMinute: rate,
		MaxQuestionChars:   parseMaxQuestionChars(svcbase.EnvOr("MAX_QUESTION_CHARS", "")),
	}
}
