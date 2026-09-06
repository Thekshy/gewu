package api

import (
	"fmt"
	"net/http"

	"github.com/gin-gonic/gin"

	"gewu/internal/middleware"
)

// NewRouter 装配全部路由与中间件（顺序：日志/恢复 → trace-id → 限流 → CORS）。
func (s *Server) NewRouter() *gin.Engine {
	r := gin.New()
	r.Use(gin.LoggerWithFormatter(func(p gin.LogFormatterParams) string {
		return fmt.Sprintf("[gin %s] %s %s %d %s %s\n",
			p.TimeStamp.Format("2006/01/02 15:04:05"), p.Method, p.Path, p.StatusCode,
			p.Latency, traceIDOrDash(p.Keys))
	}), gin.Recovery())
	r.Use(middleware.TraceID())
	r.Use(middleware.NewRateLimiter(s.settings.RateLimitPerMinute).Handler())
	r.Use(cors())

	r.GET("/api/health", s.health)
	r.GET("/api/docs", s.listDocs)
	r.POST("/api/search", s.search)
	r.POST("/api/chat", s.chat)
	r.POST("/api/business/reset", s.businessReset)
	r.GET("/api/business/overview", s.businessOverview)
	return r
}

// cors 允许任意 Origin/Method/Header（公开 demo）。
func cors() gin.HandlerFunc {
	return func(c *gin.Context) {
		c.Header("Access-Control-Allow-Origin", "*")
		c.Header("Access-Control-Allow-Methods", "*")
		c.Header("Access-Control-Allow-Headers", "*")
		if c.Request.Method == http.MethodOptions {
			c.AbortWithStatus(http.StatusNoContent)
			return
		}
		c.Next()
	}
}

// traceIDOrDash gin 日志取 trace-id（TraceID 中间件写入 Keys）。
func traceIDOrDash(keys map[any]any) string {
	if id, ok := keys["trace_id"].(string); ok && id != "" {
		return "trace=" + id
	}
	return "trace=-"
}
