package svcbase

import (
	"context"
	"errors"
	"fmt"
	"net"
	"net/http"
	"os"
	"os/signal"
	"strings"
	"syscall"
	"time"

	"google.golang.org/grpc"
	"google.golang.org/grpc/credentials/insecure"
	"google.golang.org/grpc/health"
	healthpb "google.golang.org/grpc/health/grpc_health_v1"
	"google.golang.org/grpc/reflection"

	"go.uber.org/zap"
)

// EnvOr 读环境变量，空串取缺省。
func EnvOr(key, def string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return def
}

// GRPCConfig gRPC 服务进程装配参数。
type GRPCConfig struct {
	Name      string // 服务名（日志）
	Addr      string // gRPC 监听地址，如 :9001
	AdminAddr string // 管理端口（HTTP /healthz），如 :9101
	Register  func(*grpc.Server)
}

// RunGRPC 启动 gRPC 服务 + 标准健康检查 + reflection + 管理端口 healthz，
// ctx 结束（SIGINT/SIGTERM）时优雅停止。六服务共用的进程骨架。
func RunGRPC(ctx context.Context, cfg GRPCConfig, log *zap.Logger) error {
	lis, err := net.Listen("tcp", cfg.Addr)
	if err != nil {
		return fmt.Errorf("监听 %s 失败: %w", cfg.Addr, err)
	}
	srv := grpc.NewServer(
		grpc.ChainUnaryInterceptor(UnaryServerInterceptor(log)),
		grpc.ChainStreamInterceptor(StreamServerInterceptor(log)),
	)
	hs := health.NewServer()
	healthpb.RegisterHealthServer(srv, hs)
	reflection.Register(srv)
	if cfg.Register != nil {
		cfg.Register(srv)
	}

	admin := &http.Server{
		Addr:              cfg.AdminAddr,
		Handler:           AdminMux(cfg.Name),
		ReadHeaderTimeout: 5 * time.Second,
	}
	adminErr := make(chan error, 1)
	go func() {
		if err := admin.ListenAndServe(); err != nil && !errors.Is(err, http.ErrServerClosed) {
			adminErr <- err
		}
	}()

	serveErr := make(chan error, 1)
	go func() { serveErr <- srv.Serve(lis) }()
	log.Info("gRPC 服务启动",
		zap.String("addr", cfg.Addr),
		zap.String("admin_addr", cfg.AdminAddr))

	select {
	case <-ctx.Done():
	case err := <-serveErr:
		return err
	case err := <-adminErr:
		return err
	}

	// 优雅退出：先停接收新请求，管理端口最后关
	stopped := make(chan struct{})
	go func() { srv.GracefulStop(); close(stopped) }()
	select {
	case <-stopped:
	case <-time.After(10 * time.Second):
		srv.Stop()
	}
	shutdownCtx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	_ = admin.Shutdown(shutdownCtx)
	log.Info("gRPC 服务已停止")
	return nil
}

// AdminMux 管理端口：/healthz 供 compose/k8s 存活探针（不经过业务逻辑与限流）。
func AdminMux(name string) http.Handler {
	mux := http.NewServeMux()
	mux.HandleFunc("/healthz", func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "text/plain; charset=utf-8")
		fmt.Fprintf(w, "ok %s\n", name)
	})
	return mux
}

// Dial 构造到下游服务的 gRPC 连接（懒连接 + trace-id 出站拦截器）。
// 目标形如 "orchestrator:9001" 或 "127.0.0.1:9001"。
func Dial(target string) (*grpc.ClientConn, error) {
	return grpc.NewClient(target,
		grpc.WithTransportCredentials(insecure.NewCredentials()),
		grpc.WithChainUnaryInterceptor(UnaryClientInterceptor()),
		grpc.WithChainStreamInterceptor(StreamClientInterceptor()),
	)
}

// MainSignalContext 标准退出信号 context（cmd 入口共用）。
func MainSignalContext() (context.Context, func()) {
	return signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
}

// NormalizeTarget 环境变量里的地址若以 : 开头（本机绑定形态），作为客户端
// 目标时补 127.0.0.1 前缀。
func NormalizeTarget(addr string) string {
	if strings.HasPrefix(addr, ":") {
		return "127.0.0.1" + addr
	}
	return addr
}
