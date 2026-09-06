// Package api 实现 HTTP 接口层：路由注册、请求校验、SSE 事件写出。
//
// 职责边界（P9 结构收口）：本包只做 HTTP 语义（校验/状态码/事件流序列化），
// 编排经 agent.Deps、检索经 rag.Store 触达；不 import llm——接口层不得
// 绕过编排直接调模型。事件形状的契约仍是 agent/events.go（PARITY §3），
// 前端与评测脚本解析的也是同一份。
package api

import (
	"os"

	"github.com/gin-gonic/gin"

	"gewu/internal/agent"
	"gewu/internal/budget"
	"gewu/internal/config"
	"gewu/internal/rag"
)

// Server 聚合 HTTP 层依赖（由 cmd/server 装配注入）。
type Server struct {
	deps     *agent.Deps
	store    *rag.Store
	budget   *budget.TokenBudget
	settings *config.Settings
}

// New 构造 HTTP 服务。
func New(deps *agent.Deps, store *rag.Store, b *budget.TokenBudget, settings *config.Settings) *Server {
	return &Server{deps: deps, store: store, budget: b, settings: settings}
}

// Run 启动 HTTP 服务（GIN_MODE 未设置时用 release 模式）。
func (s *Server) Run(addr string) error {
	if os.Getenv("GIN_MODE") == "" {
		gin.SetMode(gin.ReleaseMode)
	}
	return s.NewRouter().Run(addr)
}
