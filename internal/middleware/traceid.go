package middleware

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"fmt"
	"time"

	"github.com/gin-gonic/gin"
)

// X-Trace-Id 观测（P8-4 观测小吸收，svcbase/traceid 的单体零依赖内联版）：
// 每个请求生成（或透传客户端带来的）trace-id，写回响应头并注入 request context，
// gin 日志（LoggerWithFormatter）与 [agent] 日志行（agent.TracePrefix）带同一 ID。

// TraceHeader trace-id 的 HTTP 头（大小写不敏感）。
const TraceHeader = "X-Trace-Id"

type traceIDKey struct{}

// NewTraceID 生成 uuid v4（crypto/rand，零依赖）：xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx。
func NewTraceID() string {
	var b [16]byte
	if _, err := rand.Read(b[:]); err != nil {
		// 随机源不可用（极端）：纳秒时间戳兜底，保证每请求 ID 仍互异
		return fmt.Sprintf("t-%x", time.Now().UnixNano())
	}
	b[6] = (b[6] & 0x0f) | 0x40 // version 4
	b[8] = (b[8] & 0x3f) | 0x80 // variant 10
	h := hex.EncodeToString(b[:])
	return h[0:8] + "-" + h[8:12] + "-" + h[12:16] + "-" + h[16:20] + "-" + h[20:32]
}

// TraceID() gin 中间件：透传或生成 trace-id，写响应头并注入 request context。
func TraceID() gin.HandlerFunc {
	return func(c *gin.Context) {
		id := c.GetHeader(TraceHeader)
		if id == "" {
			id = NewTraceID()
		}
		c.Header(TraceHeader, id)
		c.Set("trace_id", id)
		ctx := context.WithValue(c.Request.Context(), traceIDKey{}, id)
		c.Request = c.Request.WithContext(ctx)
		c.Next()
	}
}

// FromContext 从 request context 取 trace-id（无则空串）。
func FromContext(ctx context.Context) string {
	if v, ok := ctx.Value(traceIDKey{}).(string); ok {
		return v
	}
	return ""
}
