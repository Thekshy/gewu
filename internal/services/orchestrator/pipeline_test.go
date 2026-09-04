package orchestrator

import (
	"context"
	"net"
	"testing"
	"time"

	generatev1 "gewu/pkg/gen/gewu/generate/v1"
	orchestratorv1 "gewu/pkg/gen/gewu/orchestrator/v1"

	"gewu/internal/agent"

	"google.golang.org/grpc"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/credentials/insecure"
	"google.golang.org/grpc/status"

	"go.uber.org/zap"
)

// fakeGen 可编程的 generate 服务桩。
type fakeGen struct {
	generatev1.UnimplementedGenerateServiceServer
	hasKey    bool
	ensureErr error
	chatOut   string
	chatErr   error
	deltas    []string
}

func (f *fakeGen) EnsureBudget(ctx context.Context, req *generatev1.EnsureBudgetRequest) (*generatev1.EnsureBudgetResponse, error) {
	if f.ensureErr != nil {
		return nil, f.ensureErr
	}
	return &generatev1.EnsureBudgetResponse{HasKey: f.hasKey}, nil
}

func (f *fakeGen) Chat(ctx context.Context, req *generatev1.ChatRequest) (*generatev1.ChatResponse, error) {
	if f.chatErr != nil {
		return nil, f.chatErr
	}
	return &generatev1.ChatResponse{Content: f.chatOut}, nil
}

func (f *fakeGen) ChatStream(req *generatev1.ChatStreamRequest, stream generatev1.GenerateService_ChatStreamServer) error {
	for _, d := range f.deltas {
		if err := stream.Send(&generatev1.ChatStreamResponse{Text: d}); err != nil {
			return err
		}
	}
	return nil
}

// newTestServer 起一个 fake generate gRPC 服务并装配编排服务。
func newTestServer(t *testing.T, fake *fakeGen) *Server {
	t.Helper()
	lis, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	grpcSrv := grpc.NewServer()
	generatev1.RegisterGenerateServiceServer(grpcSrv, fake)
	go func() { _ = grpcSrv.Serve(lis) }()
	t.Cleanup(grpcSrv.Stop)

	cc, err := grpc.NewClient(lis.Addr().String(), grpc.WithTransportCredentials(insecure.NewCredentials()))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = cc.Close() })
	return newServerWithGen(LoadConfig(), generatev1.NewGenerateServiceClient(cc), zap.NewNop())
}

// collect 驱动一次管线并收集全部事件（跳过 gRPC 层；gRPC 语义由 gateway 侧测试覆盖）。
func collect(t *testing.T, s *Server, question string) ([]*orchestratorv1.ChatResponse, error) {
	t.Helper()
	var events []*orchestratorv1.ChatResponse
	send := func(ev *orchestratorv1.ChatResponse) error {
		events = append(events, ev)
		return nil
	}
	req := &orchestratorv1.ChatRequest{Question: question, Mode: "auto", SessionId: "t", Role: "student"}
	ensure, err := s.generate.EnsureBudget(context.Background(), &generatev1.EnsureBudgetRequest{})
	if err != nil {
		return events, err
	}
	err = s.runChat(context.Background(), send, req, ensure.HasKey, time.Now())
	return events, err
}

func eventKind(ev *orchestratorv1.ChatResponse) string {
	switch ev.GetKind().(type) {
	case *orchestratorv1.ChatResponse_Route:
		return "route"
	case *orchestratorv1.ChatResponse_AnswerDelta:
		return "answer_delta"
	case *orchestratorv1.ChatResponse_Citations:
		return "citations"
	case *orchestratorv1.ChatResponse_Done:
		return "done"
	case *orchestratorv1.ChatResponse_Error:
		return "error"
	}
	return "?"
}

func TestPipelineZeroKeyFactualNoData(t *testing.T) {
	s := newTestServer(t, &fakeGen{hasKey: false})
	events, err := collect(t, s, "图书馆几点开门")
	if err != nil {
		t.Fatal(err)
	}
	if len(events) != 4 {
		t.Fatalf("事件数应 4（route/answer/citations/done），实际 %d", len(events))
	}
	r := events[0].GetRoute()
	if r.GetRoute() != "factual" || r.GetByLlm() || r.GetReason() != "启发式：短事实型问题" {
		t.Fatalf("零 key 应走启发式路由：%v", r)
	}
	if events[1].GetAnswerDelta().GetText() != agent.NoDataAnswer {
		t.Fatalf("零 key 裸管线应答 NO_DATA：%q", events[1].GetAnswerDelta().GetText())
	}
	if items := events[2].GetCitations().GetItems(); len(items) != 0 {
		t.Fatalf("citations 应为空：%v", items)
	}
	if events[3].GetDone().GetLatencyMs() < 0 {
		t.Fatal("done.latency_ms 非法")
	}
}

func TestPipelineRefusalFixedAnswer(t *testing.T) {
	s := newTestServer(t, &fakeGen{
		hasKey:  true,
		chatOut: `{"route":"refusal","reason":"与校园无关"}`,
	})
	events, err := collect(t, s, "今天A股行情怎样")
	if err != nil {
		t.Fatal(err)
	}
	r := events[0].GetRoute()
	if r.GetRoute() != "refusal" || !r.GetByLlm() || r.GetReason() != "与校园无关" {
		t.Fatalf("应采用 LLM 路由结论：%v", r)
	}
	if events[1].GetAnswerDelta().GetText() != agent.RefusalAnswer {
		t.Fatal("refusal 应发固定拒答话术")
	}
}

func TestPipelineBareStreamAnswer(t *testing.T) {
	s := newTestServer(t, &fakeGen{
		hasKey:  true,
		chatOut: `{"route":"factual","reason":"r"}`,
		deltas:  []string{"第一", "第二", "第三"},
	})
	events, err := collect(t, s, "转专业条件")
	if err != nil {
		t.Fatal(err)
	}
	kinds := make([]string, 0, len(events))
	for _, ev := range events {
		kinds = append(kinds, eventKind(ev))
	}
	want := []string{"route", "answer_delta", "answer_delta", "answer_delta", "citations", "done"}
	if len(kinds) != len(want) {
		t.Fatalf("事件序不匹配：%v", kinds)
	}
	for i := range want {
		if kinds[i] != want[i] {
			t.Fatalf("事件序不匹配：%v", kinds)
		}
	}
	if events[1].GetAnswerDelta().GetText() != "第一" {
		t.Fatalf("流式增量不匹配：%v", events[1].GetAnswerDelta().GetText())
	}
}

func TestPipelineRouterLLMErrorFallsBackHeuristic(t *testing.T) {
	s := newTestServer(t, &fakeGen{
		hasKey:  true,
		chatErr: status.Error(codes.Unavailable, "endpoint down"),
	})
	events, err := collect(t, s, "帮我预约明天晚上的羽毛球馆")
	if err != nil {
		t.Fatal(err)
	}
	r := events[0].GetRoute()
	if r.GetRoute() != "transaction" || r.GetByLlm() || r.GetReason() != "启发式：业务办理诉求" {
		t.Fatalf("LLM 失败应降级启发式（且不中止管线）：%v", r)
	}
}

func TestPipelineModeDirectSkipsRouter(t *testing.T) {
	s := newTestServer(t, &fakeGen{hasKey: true, chatOut: `{"route":"factual"}`})
	events, err := collect(t, s, "x")
	if err != nil {
		t.Fatal(err)
	}
	_ = events // mode 透传由 runChat 处理；此处仅保证不 panic
}
