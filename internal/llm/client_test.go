package llm

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"gewu/internal/budget"
	"gewu/internal/config"
)

func testClient(t *testing.T, handler http.HandlerFunc) (*Client, *budget.TokenBudget) {
	t.Helper()
	srv := httptest.NewServer(handler)
	t.Cleanup(srv.Close)
	s := config.Default()
	s.LLMKey = "test-key"
	s.LLMBaseURL = srv.URL + "/"
	b := budget.New(filepath.Join(t.TempDir(), "usage.json"), 1_000_000)
	return New(s, b), b
}

func TestChatParsesContentAndAccountsUsage(t *testing.T) {
	var gotBody map[string]any
	c, b := testClient(t, func(w http.ResponseWriter, r *http.Request) {
		_ = json.NewDecoder(r.Body).Decode(&gotBody)
		_, _ = w.Write([]byte(`{"choices":[{"message":{"content":"你好"}}],"usage":{"total_tokens":88}}`))
	})
	out, err := c.Chat(context.Background(), []Message{{Role: "user", Content: "hi"}},
		Options{JSONMode: true, Temperature: 0, MaxTokens: 100, Small: true})
	if err != nil || out != "你好" {
		t.Fatalf("Chat = %q, %v", out, err)
	}
	if b.Used() != 88 {
		t.Errorf("usage 入账 = %d, want 88", b.Used())
	}
	if gotBody["model"] != "glm-5.3-flash" { // small=true 应走小模型
		t.Errorf("model = %v, want glm-5.3-flash", gotBody["model"])
	}
	rf, _ := gotBody["response_format"].(map[string]any)
	if rf == nil || rf["type"] != "json_object" {
		t.Errorf("json_mode 未生效: %v", gotBody["response_format"])
	}
	if auth := gotBody["_"]; auth != nil {
		t.Error("unreachable")
	}
}

func TestChatMainModelWhenNotSmall(t *testing.T) {
	var model string
	c, _ := testClient(t, func(w http.ResponseWriter, r *http.Request) {
		var body map[string]any
		_ = json.NewDecoder(r.Body).Decode(&body)
		model = body["model"].(string)
		_, _ = w.Write([]byte(`{"choices":[{"message":{"content":""}}]}`))
	})
	_, _ = c.Chat(context.Background(), nil, Options{})
	if model != "glm-5.3" {
		t.Errorf("model = %s, want glm-5.3", model)
	}
}

func TestNoKeyReturnsErrNoKey(t *testing.T) {
	s := config.Default()
	s.LLMKey = ""
	b := budget.New(filepath.Join(t.TempDir(), "u.json"), 100)
	c := New(s, b)
	if _, err := c.Chat(context.Background(), nil, Options{}); !errors.Is(err, ErrNoKey) {
		t.Fatalf("err = %v, want ErrNoKey", err)
	}
}

func TestChatStreamDeltasAndBudget(t *testing.T) {
	c, b := testClient(t, func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "text/event-stream")
		_, _ = w.Write([]byte("data: {\"choices\":[{\"delta\":{\"content\":\"你好\"}}]}\n\n" +
			": keep-alive\n\n" +
			"data: {\"choices\":[{\"delta\":{\"content\":\"，世界\"}}]}\n\n" +
			"data: [DONE]\n\n"))
	})
	var sb strings.Builder
	err := c.ChatStream(context.Background(), []Message{{Role: "user", Content: "hi"}}, Options{},
		func(s string) error { sb.WriteString(s); return nil })
	if err != nil {
		t.Fatalf("ChatStream: %v", err)
	}
	if sb.String() != "你好，世界" {
		t.Errorf("stream = %q", sb.String())
	}
	// 5 个字符 / 2 = 2（下限 1）
	if b.Used() != 2 {
		t.Errorf("流式入账 = %d, want 2", b.Used())
	}
}

func TestChatStreamPropagatesCallbackError(t *testing.T) {
	c, _ := testClient(t, func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "text/event-stream")
		_, _ = w.Write([]byte("data: {\"choices\":[{\"delta\":{\"content\":\"a\"}}]}\n\ndata: [DONE]\n\n"))
	})
	err := c.ChatStream(context.Background(), nil, Options{}, func(string) error { return context.Canceled })
	if !errors.Is(err, context.Canceled) {
		t.Fatalf("回调错误应向上传播, got %v", err)
	}
}

func TestHTTPErrorSurfaced(t *testing.T) {
	c, _ := testClient(t, func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusUnauthorized)
		_, _ = w.Write([]byte(`{"error":{"message":"bad key"}}`))
	})
	_, err := c.Chat(context.Background(), nil, Options{})
	if err == nil || !strings.Contains(err.Error(), "401") {
		t.Fatalf("err = %v, want HTTP 401", err)
	}
}

func TestEmbedOrdersByIndex(t *testing.T) {
	c, b := testClient(t, func(w http.ResponseWriter, r *http.Request) {
		_, _ = w.Write([]byte(`{"data":[{"index":1,"embedding":[0.2,0]},{"index":0,"embedding":[0.1,0]}],` +
			`"usage":{"total_tokens":9}}`))
	})
	vecs, err := c.Embed(context.Background(), []string{"a", "b"})
	if err != nil {
		t.Fatal(err)
	}
	if vecs[0][0] != 0.1 || vecs[1][0] != 0.2 {
		t.Errorf("embedding 顺序错: %v", vecs)
	}
	if b.Used() != 9 {
		t.Errorf("embedding 入账 = %d", b.Used())
	}
}

func TestBudgetExceededBlocksCall(t *testing.T) {
	s := config.Default()
	s.LLMKey = "k"
	usagePath := filepath.Join(t.TempDir(), "usage.json")
	today := time.Now().UTC().Format("2006-01-02")
	_ = os.WriteFile(usagePath, []byte(`{"date":"`+today+`","tokens":100}`), 0o644)
	b := budget.New(usagePath, 100)
	c := New(s, b)
	_, err := c.Chat(context.Background(), nil, Options{})
	if !errors.Is(err, budget.ErrBudgetExceeded) {
		t.Fatalf("err = %v, want budget", err)
	}
}
