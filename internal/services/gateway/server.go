package gateway

import (
	"context"
	"errors"
	"net/http"
	"os"
	"time"

	"github.com/gin-gonic/gin"
	"google.golang.org/grpc"

	"gewu/internal/config"
	"gewu/internal/middleware"
	"gewu/internal/svcbase"

	conversationv1 "gewu/pkg/gen/gewu/conversation/v1"
	generatev1 "gewu/pkg/gen/gewu/generate/v1"
	orchestratorv1 "gewu/pkg/gen/gewu/orchestrator/v1"
	ragv1 "gewu/pkg/gen/gewu/rag/v1"
	toolv1 "gewu/pkg/gen/gewu/tool/v1"

	"go.uber.org/zap"
)

// Server 网关：持有五个下游服务的客户端，对外只暴露 HTTP/SSE。
type Server struct {
	log *zap.Logger
	cfg Config

	orchestrator orchestratorv1.ChatServiceClient
	conversation conversationv1.ConversationServiceClient
	generate     generatev1.GenerateServiceClient
	tool         toolv1.ToolServiceClient
	rag          ragv1.RagServiceClient
}

// Run 装配下游连接并启动 HTTP + 管理端口，ctx 结束时优雅退出。
func Run(ctx context.Context, cfg Config, log *zap.Logger) error {
	s := &Server{log: log, cfg: cfg}

	conns := make([]*grpc.ClientConn, 0, 5)
	dial := func(target, name string) *grpc.ClientConn {
		cc, err := svcbase.Dial(target)
		if err != nil {
			log.Fatal("下游连接构造失败", zap.String("dep", name), zap.Error(err))
		}
		conns = append(conns, cc)
		return cc
	}
	s.orchestrator = orchestratorv1.NewChatServiceClient(dial(cfg.OrchestratorAddr, "orchestrator"))
	s.conversation = conversationv1.NewConversationServiceClient(dial(cfg.ConversationAddr, "conversation"))
	s.generate = generatev1.NewGenerateServiceClient(dial(cfg.GenerateAddr, "generate"))
	s.tool = toolv1.NewToolServiceClient(dial(cfg.ToolAddr, "tool"))
	s.rag = ragv1.NewRagServiceClient(dial(cfg.RagAddr, "rag"))
	defer func() {
		for _, cc := range conns {
			_ = cc.Close()
		}
	}()

	if os.Getenv("GIN_MODE") == "" {
		gin.SetMode(gin.ReleaseMode)
	}
	r := s.newRouter()
	httpSrv := &http.Server{Addr: cfg.Addr, Handler: r, ReadHeaderTimeout: 10 * time.Second}
	adminSrv := &http.Server{Addr: cfg.AdminAddr, Handler: svcbase.AdminMux("gateway"), ReadHeaderTimeout: 5 * time.Second}

	httpErr := make(chan error, 1)
	go func() {
		if err := httpSrv.ListenAndServe(); err != nil && !errors.Is(err, http.ErrServerClosed) {
			httpErr <- err
		}
	}()
	adminErr := make(chan error, 1)
	go func() {
		if err := adminSrv.ListenAndServe(); err != nil && !errors.Is(err, http.ErrServerClosed) {
			adminErr <- err
		}
	}()
	log.Info("网关启动",
		zap.String("addr", cfg.Addr),
		zap.String("admin_addr", cfg.AdminAddr))

	select {
	case <-ctx.Done():
	case err := <-httpErr:
		return err
	case err := <-adminErr:
		return err
	}
	shutdownCtx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	_ = adminSrv.Shutdown(shutdownCtx)
	return httpSrv.Shutdown(shutdownCtx)
}

// newRouter 装配路由与中间件。顺序：访问日志/trace 在前（不影响契约响应），
// 之后限流最先、再 CORS（与冻结单体 PARITY §12.1 的相对顺序一致）。
func (s *Server) newRouter() *gin.Engine {
	r := gin.New()
	r.Use(gin.Recovery())
	r.Use(s.accessLog(), s.trace())
	r.Use(middleware.NewRateLimiter(s.cfg.RateLimitPerMinute).Handler())
	r.Use(cors())

	r.GET("/api/health", s.health)

	// P0 骨架占位：契约端点在 P1~P4 逐阶段接入
	notWired := func(stage string) gin.HandlerFunc {
		return func(c *gin.Context) {
			c.JSON(http.StatusServiceUnavailable, gin.H{"detail": "P0 脚手架：" + stage})
		}
	}
	r.GET("/api/docs", notWired("/api/docs 在 P3 接入（rag）"))
	r.POST("/api/search", notWired("/api/search 在 P3 接入（rag）"))
	r.POST("/api/chat", notWired("/api/chat 在 P1 接入（orchestrator SSE）"))
	r.POST("/api/business/reset", notWired("/api/business/* 在 P4 接入（tool）"))
	r.GET("/api/business/overview", notWired("/api/business/* 在 P4 接入（tool）"))
	return r
}

// trace 为每个请求生成/透传 X-Trace-Id 并写入 context 与响应头
// （置于限流之前，仅新增头部，不改变限流/CORS 的契约行为）。
func (s *Server) trace() gin.HandlerFunc {
	return func(c *gin.Context) {
		id := c.GetHeader("X-Trace-Id")
		if id == "" {
			id = svcbase.NewTraceID()
		}
		c.Request = c.Request.WithContext(svcbase.WithTraceID(c.Request.Context(), id))
		c.Header("X-Trace-Id", id)
		c.Next()
	}
}

// accessLog zap 结构化访问日志（trace_id 贯穿）。
func (s *Server) accessLog() gin.HandlerFunc {
	return func(c *gin.Context) {
		t0 := time.Now()
		c.Next()
		s.log.Info("http",
			zap.String("trace_id", svcbase.TraceID(c.Request.Context())),
			zap.String("method", c.Request.Method),
			zap.String("path", c.Request.URL.Path),
			zap.Int("status", c.Writer.Status()),
			zap.Duration("latency", time.Since(t0)),
			zap.String("client_ip", c.ClientIP()),
		)
	}
}

// cors 允许任意 Origin/Method/Header。
// 这是有意的契约行为而非疏漏：公开 demo（PARITY §1「CORS:允许任意 Origin /
// Method / Header」），冻结单体同款；收紧属行为变更，需另立决策。
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

// health 聚合 rag（docs/chunks/embeddings）与 generate（llm/budget）。
// 决策 D：下游不可达 → 503 {"detail":"<服务名> 不可达"}（诚实失败，评测立即可见）。
func (s *Server) health(c *gin.Context) {
	ctx, cancel := context.WithTimeout(c.Request.Context(), 3*time.Second)
	defer cancel()

	stats, err := s.rag.Stats(ctx, &ragv1.StatsRequest{})
	if err != nil {
		s.log.Warn("health：rag 不可达", zap.String("trace_id", svcbase.TraceID(ctx)), zap.Error(err))
		c.JSON(http.StatusServiceUnavailable, gin.H{"detail": "rag 不可达"})
		return
	}
	budget, err := s.generate.BudgetStatus(ctx, &generatev1.BudgetStatusRequest{})
	if err != nil {
		s.log.Warn("health：generate 不可达", zap.String("trace_id", svcbase.TraceID(ctx)), zap.Error(err))
		c.JSON(http.StatusServiceUnavailable, gin.H{"detail": "generate 不可达"})
		return
	}
	c.JSON(http.StatusOK, gin.H{
		"status":     "ok",
		"version":    config.Version,
		"llm":        budget.HasKey,
		"embeddings": budget.HasKey && stats.Embedded,
		"docs":       stats.Docs,
		"chunks":     stats.Chunks,
		"budget":     gin.H{"used": budget.Used, "limit": budget.Limit},
	})
}
