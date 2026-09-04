package gateway

import (
	"context"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/gin-gonic/gin"
	"google.golang.org/grpc"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/credentials/insecure"
	"google.golang.org/grpc/status"

	orchestratorv1 "gewu/pkg/gen/gewu/orchestrator/v1"

	"go.uber.org/zap"
)

// fakeOrch 可编程的编排服务桩：按脚本发事件 / 返回错误 / 观察 ctx 取消。
type fakeOrch struct {
	orchestratorv1.UnimplementedChatServiceServer
	script   []*orchestratorv1.ChatResponse // 逐帧发送
	initErr  error                          // 流建立前返回
	cancelCh chan struct{}                  // ctx 取消时关闭（取消传播测试）
}

func (f *fakeOrch) Chat(req *orchestratorv1.ChatRequest, stream orchestratorv1.ChatService_ChatServer) error {
	if f.initErr != nil {
		return f.initErr
	}
	for _, ev := range f.script {
		if err := stream.Send(ev); err != nil {
			return err
		}
	}
	if f.cancelCh != nil {
		// 等待 ctx 取消（模拟长流式生成），取消后如实上报
		<-stream.Context().Done()
		close(f.cancelCh)
		return stream.Context().Err()
	}
	return nil
}

// newTestGateway 起一个 fake orchestrator gRPC 服务并装配网关路由。
func newTestGateway(t *testing.T, fake *fakeOrch) (*gin.Engine, *httptest.Server) {
	t.Helper()
	gin.SetMode(gin.TestMode)
	lis, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	grpcSrv := grpc.NewServer()
	orchestratorv1.RegisterChatServiceServer(grpcSrv, fake)
	go func() { _ = grpcSrv.Serve(lis) }()
	t.Cleanup(grpcSrv.Stop)

	cc, err := grpc.NewClient(lis.Addr().String(),
		grpc.WithTransportCredentials(insecure.NewCredentials()))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = cc.Close() })

	cfg := LoadConfig()
	cfg.RateLimitPerMinute = 100000 // 测试不限流
	s := newServer(cfg, zap.NewNop(), clients{orchestrator: orchestratorv1.NewChatServiceClient(cc)})
	ts := httptest.NewServer(s.newRouter())
	t.Cleanup(ts.Close)
	return s.newRouter(), ts
}

func postChat(t *testing.T, ts *httptest.Server, body string) *http.Response {
	t.Helper()
	resp, err := http.Post(ts.URL+"/api/chat", "application/json", strings.NewReader(body))
	if err != nil {
		t.Fatal(err)
	}
	return resp
}

func TestChatSSEGoldenFrames(t *testing.T) {
	fake := &fakeOrch{script: []*orchestratorv1.ChatResponse{
		routeEvtForTest("factual", "启发式：短事实型问题", false),
		answerEvtForTest("第一段"),
		answerEvtForTest("第二段"),
		citationsEvtForTest(nil),
		doneEvtForTest(42),
	}}
	_, ts := newTestGateway(t, fake)
	resp := postChat(t, ts, `{"question":"图书馆几点开门"}`)
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		t.Fatalf("状态码 %d", resp.StatusCode)
	}
	if ct := resp.Header.Get("Content-Type"); ct != "text/event-stream" {
		t.Fatalf("Content-Type = %q", ct)
	}
	if cc := resp.Header.Get("Cache-Control"); cc != "no-cache" {
		t.Fatalf("Cache-Control = %q", cc)
	}
	if xb := resp.Header.Get("X-Accel-Buffering"); xb != "no" {
		t.Fatalf("X-Accel-Buffering = %q", xb)
	}
	if tid := resp.Header.Get("X-Trace-Id"); tid == "" {
		t.Fatal("响应应携带 X-Trace-Id")
	}

	body, _ := io.ReadAll(resp.Body)
	want := "data: {\"type\":\"route\",\"route\":\"factual\",\"reason\":\"启发式：短事实型问题\",\"by_llm\":false}\n\n" +
		"data: {\"type\":\"answer_delta\",\"text\":\"第一段\"}\n\n" +
		"data: {\"type\":\"answer_delta\",\"text\":\"第二段\"}\n\n" +
		"data: {\"type\":\"citations\",\"items\":[]}\n\n" +
		"data: {\"type\":\"done\",\"latency_ms\":42}\n\n"
	if string(body) != want {
		t.Fatalf("SSE 帧序列不匹配\n got: %q\nwant: %q", body, want)
	}
}

func TestChatValidationTexts(t *testing.T) {
	_, ts := newTestGateway(t, &fakeOrch{})
	cases := []struct {
		body string
		want string
	}{
		{`{"question":`, `{"detail":"请求体不是合法 JSON"}`},
		{`{}`, `{"detail":"问题不能为空"}`},
		{`{"question":"` + strings.Repeat("长", 501) + `"}`, `{"detail":"问题过长"}`},
		{`{"question":"hi","mode":"x"}`, `{"detail":"mode 必须为 auto/direct/research"}`},
		{`{"question":"hi","role":"teacher"}`, `{"detail":"role 必须为 student/counselor"}`},
		{`{"question":"hi","session_id":"` + strings.Repeat("s", 65) + `"}`, `{"detail":"session_id 过长（上限 64 字符）"}`},
	}
	for _, c := range cases {
		resp := postChat(t, ts, c.body)
		b, _ := io.ReadAll(resp.Body)
		resp.Body.Close()
		if resp.StatusCode != http.StatusUnprocessableEntity {
			t.Fatalf("%s → %d（应 422）", c.body[:20], resp.StatusCode)
		}
		if string(b) != c.want {
			t.Fatalf("422 文案不匹配\n got: %s\nwant: %s", b, c.want)
		}
	}
}

func TestChatBudgetExhausted429(t *testing.T) {
	fake := &fakeOrch{initErr: status.Error(codes.ResourceExhausted,
		"今日 token 预算已用尽（上限 2000000），请明天再试")}
	_, ts := newTestGateway(t, fake)
	resp := postChat(t, ts, `{"question":"hi"}`)
	defer resp.Body.Close()
	b, _ := io.ReadAll(resp.Body)
	if resp.StatusCode != http.StatusTooManyRequests {
		t.Fatalf("状态码 %d（应 429）", resp.StatusCode)
	}
	if string(b) != `{"detail":"今日 token 预算已用尽（上限 2000000），请明天再试"}` {
		t.Fatalf("429 文案不匹配：%s", b)
	}
}

func TestChatOrchestratorUnavailable503(t *testing.T) {
	fake := &fakeOrch{initErr: status.Error(codes.Unavailable, "connection refused")}
	_, ts := newTestGateway(t, fake)
	resp := postChat(t, ts, `{"question":"hi"}`)
	defer resp.Body.Close()
	b, _ := io.ReadAll(resp.Body)
	if resp.StatusCode != http.StatusServiceUnavailable {
		t.Fatalf("状态码 %d（应 503）", resp.StatusCode)
	}
	if string(b) != `{"detail":"orchestrator 不可达"}` {
		t.Fatalf("503 文案不匹配：%s", b)
	}
}

// TestChatClientDisconnectCancelsStream 客户端断开 → gateway ctx 取消 →
// 传播到 orchestrator 流（gRPC 全链路取消的网关侧验证）。
func TestChatClientDisconnectCancelsStream(t *testing.T) {
	cancelCh := make(chan struct{})
	fake := &fakeOrch{
		script:   []*orchestratorv1.ChatResponse{routeEvtForTest("factual", "r", false)},
		cancelCh: cancelCh,
	}
	_, ts := newTestGateway(t, fake)

	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	req, _ := http.NewRequestWithContext(ctx, http.MethodPost, ts.URL+"/api/chat", strings.NewReader(`{"question":"hi"}`))
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	// 读到首帧后主动断开
	buf := make([]byte, 64)
	_, _ = resp.Body.Read(buf)
	resp.Body.Close()
	cancel() // 触发客户端断开

	select {
	case <-cancelCh:
		// 取消已传播到 orchestrator
	case <-time.After(3 * time.Second):
		t.Fatal("客户端断开后 3s 内未传播到 orchestrator 流")
	}
}
