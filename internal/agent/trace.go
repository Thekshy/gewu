package agent

import (
	"context"
	"log"

	"gewu/internal/middleware"
)

// P8-4 观测小吸收：[agent] 日志行与 HTTP 层共用同一 trace-id。
//
// 请求路径：middleware.TraceID() 把 X-Trace-Id 写入 request context →
// 编排层各日志点经 logf(ctx, ...) 打出 [agent <trace-id>] 前缀，与 gin
// 日志、响应头可关联。异步路径（记忆固化 goroutine、会话库内部）无请求
// ctx，传 context.Background() 前缀退化为 [agent]——行为与从前一致。

// TracePrefix 从 ctx 组日志前缀：有 trace-id 时为 `[agent <id>]`。
func TracePrefix(ctx context.Context) string {
	if id := middleware.FromContext(ctx); id != "" {
		return "[agent " + id + "]"
	}
	return "[agent]"
}

// logf 带请求 trace-id 的日志（编排层统一出口）。
func logf(ctx context.Context, format string, args ...any) {
	log.Printf(TracePrefix(ctx)+" "+format, args...)
}
