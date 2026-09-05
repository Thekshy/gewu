package orchestrator

import (
	"context"

	"google.golang.org/grpc"
	"google.golang.org/grpc/credentials/insecure"
	"google.golang.org/grpc/metadata"

	"gewu/internal/svcbase"
)

// dialTool 连接 tool 服务，带 x-user 身份出站拦截器（ADR-0006：user 由
// 服务端从 role 派生后随调用注入，tool 侧只认 metadata）。
func dialTool(target string) (*grpc.ClientConn, error) {
	return grpc.NewClient(target,
		grpc.WithTransportCredentials(insecure.NewCredentials()),
		grpc.WithChainUnaryInterceptor(userInjectInterceptor()),
	)
}

// userInjectInterceptor 从 ctx 取派生 user 写入出站 metadata（x-user）。
func userInjectInterceptor() grpc.UnaryClientInterceptor {
	return func(ctx context.Context, method string, req, reply any,
		cc *grpc.ClientConn, invoker grpc.UnaryInvoker, opts ...grpc.CallOption) error {
		if u := svcbase.UserFromContext(ctx); u != "" {
			ctx = metadata.AppendToOutgoingContext(ctx, "x-user", u)
		}
		return invoker(ctx, method, req, reply, cc, opts...)
	}
}

// withUser 把派生身份放入 ctx（每轮 chat 开始时）。
func withUser(ctx context.Context, user string) context.Context {
	return svcbase.WithUser(ctx, user)
}
