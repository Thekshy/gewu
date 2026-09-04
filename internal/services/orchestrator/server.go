package orchestrator

import (
	"context"
	"sync/atomic"
	"time"

	conversationv1 "gewu/pkg/gen/gewu/conversation/v1"
	generatev1 "gewu/pkg/gen/gewu/generate/v1"
	orchestratorv1 "gewu/pkg/gen/gewu/orchestrator/v1"
	ragv1 "gewu/pkg/gen/gewu/rag/v1"

	"gewu/internal/business"
	"gewu/internal/svcbase"

	"go.uber.org/zap"
	"google.golang.org/grpc"
)

// Server 编排服务：编排管线（PARITY §4）+ 五分类路由 + 工具调度 + agent_config。
// P2 起：会话经 conversation、LLM 经 generate、配置经 configStore；
// 业务系统暂以冻结 internal/business 同进程过渡（P4 迁 tool 服务）。
type Server struct {
	orchestratorv1.UnimplementedChatServiceServer
	orchestratorv1.UnimplementedConfigServiceServer

	log      *zap.Logger
	cfg      Config
	generate generatev1.GenerateServiceClient
	rag      ragv1.RagServiceClient
	sessions sessionStore
	business *business.Business
	tools    map[string]Tool
	cfgStore *configStore

	hasKey atomic.Bool // 启动时从 generate 查询（env 固定后不变化）
}

// NewServer 构造编排服务（内部建立到 generate/conversation 的懒连接、
// 打开业务库与配置存储）。
func NewServer(cfg Config, log *zap.Logger) (*Server, error) {
	genCC, err := svcbase.Dial(cfg.GenerateAddr)
	if err != nil {
		return nil, err
	}
	convCC, err := svcbase.Dial(cfg.ConversationAddr)
	if err != nil {
		return nil, err
	}
	ragCC, err := svcbase.Dial(cfg.RagAddr)
	if err != nil {
		return nil, err
	}
	biz, err := business.Open(cfg.BusinessDBPath)
	if err != nil {
		return nil, err
	}
	cs, err := openConfigStore(cfg.PostgresDSN, cfg.RedisAddr, log)
	if err != nil {
		return nil, err
	}
	if cfg.PostgresDSN == "" {
		log.Warn("agent_config 运行在内存模式（POSTGRES_DSN 未配置），配置修改重启失效")
	}
	s := newServerWithDeps(cfg, generatev1.NewGenerateServiceClient(genCC),
		newGrpcSessions(conversationv1.NewConversationServiceClient(convCC)), biz, cs, log)
	s.rag = ragv1.NewRagServiceClient(ragCC)
	return s, nil
}

// newServerWithDeps 注入依赖（测试用）。
func newServerWithDeps(cfg Config, gen generatev1.GenerateServiceClient, sess sessionStore,
	biz *business.Business, cs *configStore, log *zap.Logger) *Server {
	s := &Server{
		log: log, cfg: cfg,
		generate: gen,
		sessions: sess,
		business: biz,
		cfgStore: cs,
	}
	s.tools = toolsFor(s)
	return s
}

// InitHasKey 启动时查询 generate 的 key 状态（重试 3 次，失败视为无 key——
// generate 恢复后重启编排即可；单次 Chat 失败也会自然降级启发式）。
func (s *Server) InitHasKey(ctx context.Context) {
	for i := 0; i < 3; i++ {
		resp, err := s.generate.BudgetStatus(ctx, &generatev1.BudgetStatusRequest{})
		if err == nil {
			s.hasKey.Store(resp.GetHasKey())
			return
		}
		s.log.Warn("查询 generate key 状态失败，稍后重试", zap.Error(err))
		select {
		case <-ctx.Done():
			return
		case <-time.After(time.Second):
		}
	}
	s.hasKey.Store(false)
}

// Register gRPC 注册辅助（cmd 入口用）。
func (s *Server) Register(g *grpc.Server) {
	orchestratorv1.RegisterChatServiceServer(g, s)
	orchestratorv1.RegisterConfigServiceServer(g, s)
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
	s.hasKey.Store(hasKey) // 与启动查询保持同步（env 热切换也能跟上）

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
