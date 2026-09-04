package svcbase

import (
	"context"
	"crypto/rand"
	"encoding/hex"

	"google.golang.org/grpc"
	"google.golang.org/grpc/metadata"
	"google.golang.org/grpc/status"

	"go.uber.org/zap"
)

// trace-id 贯穿约定：gateway 为每个 HTTP 请求生成（或透传）X-Trace-Id，
// 经 gRPC metadata（键 x-trace-id）传入各服务，服务端拦截器提取进 context，
// 出站调用再由客户端拦截器续传——全链路同一 ID。

const traceHeader = "x-trace-id"

type traceKey struct{}

// NewTraceID 生成 16 位十六进制随机 ID。
func NewTraceID() string {
	var b [8]byte
	if _, err := rand.Read(b[:]); err != nil {
		return "0000000000000000"
	}
	return hex.EncodeToString(b[:])
}

// WithTraceID 把 trace id 放入 context。
func WithTraceID(ctx context.Context, id string) context.Context {
	return context.WithValue(ctx, traceKey{}, id)
}

// TraceID 取当前 trace id（无则空串）。
func TraceID(ctx context.Context) string {
	if v, ok := ctx.Value(traceKey{}).(string); ok {
		return v
	}
	return ""
}

// traceFromIncomingMD 从入站 metadata 提取 trace id；缺失时生成新 ID。
func traceFromIncomingMD(ctx context.Context) (string, bool) {
	if md, ok := metadata.FromIncomingContext(ctx); ok {
		if vals := md.Get(traceHeader); len(vals) > 0 && vals[0] != "" {
			return vals[0], true
		}
	}
	return NewTraceID(), false
}

// UnaryServerInterceptor 入站一元调用：提取/生成 trace id + 访问日志。
func UnaryServerInterceptor(log *zap.Logger) grpc.UnaryServerInterceptor {
	return func(ctx context.Context, req any, info *grpc.UnaryServerInfo, handler grpc.UnaryHandler) (any, error) {
		id, _ := traceFromIncomingMD(ctx)
		ctx = WithTraceID(ctx, id)
		resp, err := handler(ctx, req)
		log.Info("grpc",
			zap.String("trace_id", id),
			zap.String("method", info.FullMethod),
			zap.String("code", status.Code(err).String()),
			zap.Error(err),
		)
		return resp, err
	}
}

// StreamServerInterceptor 入站流式调用：同上（trace id 经包装流传递给业务 ctx）。
func StreamServerInterceptor(log *zap.Logger) grpc.StreamServerInterceptor {
	return func(srv any, ss grpc.ServerStream, info *grpc.StreamServerInfo, handler grpc.StreamHandler) error {
		id, _ := traceFromIncomingMD(ss.Context())
		err := handler(srv, &tracedServerStream{ServerStream: ss, ctx: WithTraceID(ss.Context(), id)})
		log.Info("grpc",
			zap.String("trace_id", id),
			zap.String("method", info.FullMethod),
			zap.Bool("server_stream", info.IsServerStream),
			zap.String("code", status.Code(err).String()),
			zap.Error(err),
		)
		return err
	}
}

// tracedServerStream 覆盖 Context() 以携带 trace id。
type tracedServerStream struct {
	grpc.ServerStream
	ctx context.Context
}

func (s *tracedServerStream) Context() context.Context { return s.ctx }

// UnaryClientInterceptor 出站一元调用：把 context 里的 trace id 写入 metadata。
func UnaryClientInterceptor() grpc.UnaryClientInterceptor {
	return func(ctx context.Context, method string, req, reply any, cc *grpc.ClientConn, invoker grpc.UnaryInvoker, opts ...grpc.CallOption) error {
		return invoker(traceOutgoing(ctx), method, req, reply, cc, opts...)
	}
}

// StreamClientInterceptor 出站流式调用：同上（header 随流首包发出）。
func StreamClientInterceptor() grpc.StreamClientInterceptor {
	return func(ctx context.Context, desc *grpc.StreamDesc, cc *grpc.ClientConn, method string, streamer grpc.Streamer, opts ...grpc.CallOption) (grpc.ClientStream, error) {
		return streamer(traceOutgoing(ctx), desc, cc, method, opts...)
	}
}

// traceOutgoing 补写出站 metadata；已有则不覆盖。
func traceOutgoing(ctx context.Context) context.Context {
	id := TraceID(ctx)
	if id == "" {
		return ctx
	}
	if md, ok := metadata.FromOutgoingContext(ctx); ok {
		if vals := md.Get(traceHeader); len(vals) > 0 && vals[0] != "" {
			return ctx
		}
	}
	return metadata.AppendToOutgoingContext(ctx, traceHeader, id)
}
