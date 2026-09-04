package orchestrator

import (
	"time"

	generatev1 "gewu/pkg/gen/gewu/generate/v1"
	orchestratorv1 "gewu/pkg/gen/gewu/orchestrator/v1"

	"gewu/internal/svcbase"

	"go.uber.org/zap"
	"google.golang.org/grpc"
)

// Server 编排服务：编排管线（PARITY §4）+ 五分类路由 + 工具调度。
// P1：裸管线（路由 → refusal 固定话术 / 其余直连 generate 流式），
// 检索在 P3 接入、会话与工具在 P2/P4 接入。
type Server struct {
	orchestratorv1.UnimplementedChatServiceServer
	log      *zap.Logger
	cfg      Config
	generate generatev1.GenerateServiceClient
}

// NewServer 构造编排服务（内部建立到 generate 的懒连接）。
func NewServer(cfg Config, log *zap.Logger) (*Server, error) {
	cc, err := svcbase.Dial(cfg.GenerateAddr)
	if err != nil {
		return nil, err
	}
	return newServerWithGen(cfg, generatev1.NewGenerateServiceClient(cc), log), nil
}

// newServerWithGen 注入 generate 客户端（测试用）。
func newServerWithGen(cfg Config, gen generatev1.GenerateServiceClient, log *zap.Logger) *Server {
	return &Server{log: log, cfg: cfg, generate: gen}
}

// Register gRPC 注册辅助（cmd 入口用）。
func (s *Server) Register(g *grpc.Server) {
	orchestratorv1.RegisterChatServiceServer(g, s)
}

// Chat 一轮问答：入口预算预检 → 管线 → 事件流（route → … → done）。
// 耗尽 → RESOURCE_EXHAUSTED 原样上抛（gateway 映射 HTTP 429，文案逐字）。
// 客户端断开 → stream ctx 取消 → 传播到 generate 的 provider 请求（不烧 token）。
func (s *Server) Chat(req *orchestratorv1.ChatRequest, stream orchestratorv1.ChatService_ChatServer) error {
	ctx := stream.Context()
	t0 := time.Now()

	ensure, err := s.generate.EnsureBudget(ctx, &generatev1.EnsureBudgetRequest{})
	if err != nil {
		return err
	}
	hasKey := ensure.HasKey

	if err := s.runChat(ctx, stream.Send, req, hasKey, t0); err != nil {
		// 异常兜底：error 事件 + done（与冻结单体 RunChat 的 except 行为一致；
		// 客户端已断开时 emit 失败在此静默忽略）
		s.log.Warn("chat 异常", zap.String("trace_id", svcbase.TraceID(ctx)), zap.Error(err))
		_ = stream.Send(errorEvent(errText(err)))
		_ = stream.Send(doneEvent(elapsedMS(t0)))
	}
	return nil
}

func elapsedMS(t0 time.Time) int64 { return time.Since(t0).Milliseconds() }
