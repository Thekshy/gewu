package generate

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"strings"
	"testing"

	generatev1 "gewu/pkg/gen/gewu/generate/v1"

	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"
)

// dummyKey 运行时生成的占位密钥：fake 端点不校验鉴权，仓库内不落任何真实凭据。
func dummyKey() string { return "stub-" + fmt.Sprint(os.Getpid()) }

// fakeLLM 可编程的 OpenAI 兼容端点。
type fakeLLM struct {
	chatBody   string // /chat/completions 非流式响应
	streamBody string // /chat/completions 流式 SSE 响应
	embedBody  string // /embeddings 响应
	srv        *httptest.Server
}

func newFakeLLM(t *testing.T, f *fakeLLM) *fakeLLM {
	t.Helper()
	mux := http.NewServeMux()
	mux.HandleFunc("/chat/completions", func(w http.ResponseWriter, r *http.Request) {
		// 回归断言：请求体必须是 JSON 对象（防 []byte 二次 marshal 变 base64 字符串）
		var probe map[string]any
		if err := json.NewDecoder(r.Body).Decode(&probe); err != nil || probe["model"] == nil {
			w.WriteHeader(http.StatusBadRequest)
			fmt.Fprintf(w, `{"error":"请求体不是 JSON 对象: %v %v"}`, probe, err)
			return
		}
		w.Header().Set("Content-Type", "application/json")
		fmt.Fprint(w, f.chatBody)
	})
	mux.HandleFunc("/embeddings", func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		fmt.Fprint(w, f.embedBody)
	})
	f.srv = httptest.NewServer(mux)
	t.Cleanup(f.srv.Close)
	return f
}

func testProvider(fake *fakeLLM, limit int64) *provider {
	cfg := Config{APIKey: dummyKey(), BaseURL: fake.srv.URL + "/", MainModel: "m", SmallModel: "s", EmbedModel: "e", DailyTokenBudget: limit}
	return newProvider(cfg, newBudgetMeter(limit))
}

func TestChatParsesContentAndAccountsUsage(t *testing.T) {
	fake := newFakeLLM(t, &fakeLLM{chatBody: `{"choices":[{"message":{"content":"{\"route\":\"factual\"}"}}],"usage":{"total_tokens":57}}`})
	p := testProvider(fake, 1_000_000)
	out, err := p.chat(context.Background(), []*generatev1.Message{{Role: "user", Content: "q"}}, &generatev1.Options{JsonMode: true})
	if err != nil {
		t.Fatal(err)
	}
	if out != `{"route":"factual"}` {
		t.Fatalf("内容解析失败：%s", out)
	}
	if used, _ := p.meter.snapshot(); used != 57 {
		t.Fatalf("用量应计 57，实际 %d", used)
	}
}

func TestNoKeyExactError(t *testing.T) {
	cfg := Config{APIKey: "", DailyTokenBudget: 100}
	p := newProvider(cfg, newBudgetMeter(100))
	_, err := p.chat(context.Background(), nil, nil)
	if status.Code(err) != codes.FailedPrecondition || status.Convert(err).Message() != "未配置 LLM_API_KEY，无法调用模型" {
		t.Fatalf("无 key 错误应为 FAILED_PRECONDITION 且文案逐字：%v", err)
	}
}

func TestBudgetExhaustedExactError(t *testing.T) {
	fake := newFakeLLM(t, &fakeLLM{chatBody: `{}`})
	p := testProvider(fake, 10)
	p.meter.add(10) // 用满
	err := p.meter.ensure()
	if status.Code(err) != codes.ResourceExhausted ||
		status.Convert(err).Message() != "今日 token 预算已用尽（上限 10），请明天再试" {
		t.Fatalf("预算耗尽文案不逐字：%v", err)
	}
	_, err = p.chat(context.Background(), nil, nil)
	if status.Code(err) != codes.ResourceExhausted {
		t.Fatalf("调用前预检应拦截：%v", err)
	}
}

func TestChatStreamDeltasAndBudget(t *testing.T) {
	body := "data: {\"choices\":[{\"delta\":{\"content\":\"你好\"}}]}\n\n" +
		"data: {\"choices\":[{\"delta\":{\"content\":\"，世界\"}}]}\n\n" +
		"data: [DONE]\n\n"
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "text/event-stream")
		fmt.Fprint(w, body)
	}))
	defer srv.Close()
	cfg := Config{APIKey: dummyKey(), BaseURL: srv.URL + "/", DailyTokenBudget: 1_000_000}
	p := newProvider(cfg, newBudgetMeter(1_000_000))

	var got []string
	err := p.chatStream(context.Background(), nil, nil, func(s string) error {
		got = append(got, s)
		return nil
	})
	if err != nil {
		t.Fatal(err)
	}
	if len(got) != 2 || got[0] != "你好" || got[1] != "，世界" {
		t.Fatalf("增量不匹配：%v", got)
	}
	// 流式计量：5 rune / 2 = 2（下限 1）
	if used, _ := p.meter.snapshot(); used != 2 {
		t.Fatalf("流式计量应为 2（5 字符/2），实际 %d", used)
	}
}

func TestChatStreamInterruptedStillAccounts(t *testing.T) {
	body := "data: {\"choices\":[{\"delta\":{\"content\":\"一二三四五六\"}}]}\n\n" +
		"data: [DONE]\n\n"
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "text/event-stream")
		fmt.Fprint(w, body)
	}))
	defer srv.Close()
	cfg := Config{APIKey: dummyKey(), BaseURL: srv.URL + "/", DailyTokenBudget: 1_000_000}
	p := newProvider(cfg, newBudgetMeter(1_000_000))

	sentinel := errors.New("downstream gone")
	err := p.chatStream(context.Background(), nil, nil, func(s string) error {
		return sentinel // 第一帧后下游断开
	})
	if !errors.Is(err, sentinel) {
		t.Fatalf("应透传下游错误：%v", err)
	}
	// 中断也必须入账已产出部分：6 rune / 2 = 3
	if used, _ := p.meter.snapshot(); used != 3 {
		t.Fatalf("中断后应入账 3，实际 %d", used)
	}
}

func TestEmbedOrdersByIndex(t *testing.T) {
	fake := newFakeLLM(t, &fakeLLM{embedBody: `{"data":[{"index":1,"embedding":[0.2,0.2]},{"index":0,"embedding":[0.1,0.1]}],"usage":{"total_tokens":9}}`})
	p := testProvider(fake, 1_000_000)
	vecs, err := p.embed(context.Background(), []string{"a", "b"})
	if err != nil {
		t.Fatal(err)
	}
	if len(vecs) != 2 || vecs[0][0] != 0.1 || vecs[1][0] != 0.2 {
		t.Fatalf("embedding 应按输入顺序返回：%v", vecs)
	}
	if used, _ := p.meter.snapshot(); used != 9 {
		t.Fatalf("embedding 用量应计 9，实际 %d", used)
	}
}

func TestHTTPErrorSurfaced(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		http.Error(w, `{"error":"quota"}`, http.StatusTooManyRequests)
	}))
	defer srv.Close()
	p := testProvider(&fakeLLM{srv: srv}, 1_000_000)
	_, err := p.chat(context.Background(), nil, nil)
	if status.Code(err) != codes.Internal || !strings.Contains(status.Convert(err).Message(), "HTTP 429") {
		t.Fatalf("HTTP 错误应如实上抛：%v", err)
	}
}
