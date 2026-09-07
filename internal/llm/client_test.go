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
	"sync"
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
	finish, err := c.ChatStream(context.Background(), []Message{{Role: "user", Content: "hi"}}, Options{},
		func(s string) error { sb.WriteString(s); return nil })
	if err != nil {
		t.Fatalf("ChatStream: %v", err)
	}
	if sb.String() != "你好，世界" {
		t.Errorf("stream = %q", sb.String())
	}
	if finish != "" {
		t.Errorf("无 finish_reason 的流应返回空串, got %q", finish)
	}
	// 5 个字符 / 2 = 2（下限 1）
	if b.Used() != 2 {
		t.Errorf("流式入账 = %d, want 2", b.Used())
	}
}

func TestChatStreamFinishReasonCaptured(t *testing.T) {
	// 末 chunk 携带 finish_reason=length（截断）：返回值透传给调用方标记
	c, _ := testClient(t, func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "text/event-stream")
		_, _ = w.Write([]byte("data: {\"choices\":[{\"delta\":{\"content\":\"部分\"}}]}\n\n" +
			"data: {\"choices\":[{\"delta\":{},\"finish_reason\":\"length\"}]}\n\n" +
			"data: [DONE]\n\n"))
	})
	finish, err := c.ChatStream(context.Background(), []Message{{Role: "user", Content: "hi"}}, Options{},
		func(string) error { return nil })
	if err != nil {
		t.Fatalf("ChatStream: %v", err)
	}
	if finish != "length" {
		t.Errorf("finish = %q, want length", finish)
	}
}

func TestChatStreamPropagatesCallbackError(t *testing.T) {
	c, _ := testClient(t, func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "text/event-stream")
		_, _ = w.Write([]byte("data: {\"choices\":[{\"delta\":{\"content\":\"a\"}}]}\n\ndata: [DONE]\n\n"))
	})
	_, err := c.ChatStream(context.Background(), nil, Options{}, func(string) error { return context.Canceled })
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

// ---------- P6 阶段0：双 provider embed 通道 ----------

func multimodalClient(t *testing.T, s *config.Settings, handler http.HandlerFunc) (*Client, *budget.TokenBudget) {
	t.Helper()
	srv := httptest.NewServer(handler)
	t.Cleanup(srv.Close)
	if s == nil {
		s = config.Default()
	}
	s.LLMKey = "chat-key"
	s.LLMBaseURL = "http://chat-endpoint.invalid/" // chat 端点不应被请求到
	s.EmbedBaseURL = srv.URL + "/"
	s.EmbedAPIKey = "ark-key"
	s.EmbedMode = "ark_multimodal"
	b := budget.New(filepath.Join(t.TempDir(), "usage.json"), 1_000_000)
	return New(s, b), b
}

func TestEmbedMultimodalPerItemAndOrdered(t *testing.T) {
	var mu sync.Mutex
	var bodies []map[string]any
	var paths []string
	auths := map[string]bool{}
	c, b := multimodalClient(t, nil, func(w http.ResponseWriter, r *http.Request) {
		mu.Lock()
		defer mu.Unlock()
		paths = append(paths, r.URL.Path)
		auths[r.Header.Get("Authorization")] = true
		var body map[string]any
		_ = json.NewDecoder(r.Body).Decode(&body)
		bodies = append(bodies, body)
		input := body["input"].([]any)
		text := input[0].(map[string]any)["text"].(string)
		_, _ = w.Write([]byte(`{"data":{"embedding":[` + map[string]string{
			"一": "0.1,0", "二": "0.2,0", "三": "0.3,0", "四": "0.4,0", "五": "0.5,0",
		}[text] + `]},"usage":{"total_tokens":5}}`))
	})
	// 多于并发数（4）的输入，验证保序与并发安全
	vecs, err := c.Embed(context.Background(), []string{"一", "二", "三", "四", "五"})
	if err != nil {
		t.Fatal(err)
	}
	want := [][]float64{{0.1, 0}, {0.2, 0}, {0.3, 0}, {0.4, 0}, {0.5, 0}}
	for i, w := range want {
		if len(vecs[i]) != 2 || vecs[i][0] != w[0] {
			t.Errorf("vecs[%d] = %v, want %v", i, vecs[i], w)
		}
	}
	if len(bodies) != 5 {
		t.Fatalf("应逐条发 5 次请求, got %d", len(bodies))
	}
	for i, body := range bodies {
		input := body["input"].([]any)
		if len(input) != 1 || input[0].(map[string]any)["type"] != "text" {
			t.Errorf("body[%d].input 形状错: %v", i, body["input"])
		}
		if body["model"] != "embedding-3" {
			t.Errorf("body[%d].model = %v", i, body["model"])
		}
	}
	if !auths["Bearer ark-key"] || len(auths) != 1 {
		t.Errorf("embed 应使用 EMBED_API_KEY: %v", auths)
	}
	for _, p := range paths {
		if p != "/embeddings/multimodal" {
			t.Errorf("路径 = %s", p)
		}
	}
	if b.Used() != 25 { // 5 条 × usage 5
		t.Errorf("usage 入账 = %d", b.Used())
	}
}

func TestEmbedMultimodalSingleFailureFailsAll(t *testing.T) {
	var mu sync.Mutex
	n := 0
	c, _ := multimodalClient(t, nil, func(w http.ResponseWriter, r *http.Request) {
		mu.Lock()
		n++
		fail := n == 3
		mu.Unlock()
		if fail {
			w.WriteHeader(http.StatusUnauthorized)
			_, _ = w.Write([]byte(`{"error":"bad key"}`))
			return
		}
		_, _ = w.Write([]byte(`{"data":{"embedding":[0.1]}}`))
	})
	vecs, err := c.Embed(context.Background(), []string{"a", "b", "c", "d"})
	if err == nil || vecs != nil {
		t.Fatalf("单条失败应整体报错: %v, %v", vecs, err)
	}
}

func TestEmbedChannelFallbackToLLM(t *testing.T) {
	// EMBED_* 缺省 → 回退 LLM_BASE_URL/LLM_API_KEY，走标准 /embeddings
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/embeddings" || r.Header.Get("Authorization") != "Bearer llm-key" {
			w.WriteHeader(500)
			return
		}
		_, _ = w.Write([]byte(`{"data":[{"index":0,"embedding":[0.1]}]}`))
	}))
	t.Cleanup(srv.Close)
	s := config.Default()
	s.LLMKey = "llm-key"
	s.LLMBaseURL = srv.URL + "/"
	// EmbedBaseURL/EmbedAPIKey/EmbedMode 均为空
	s.EmbedModel = "embedding-3"
	b := budget.New(filepath.Join(t.TempDir(), "u.json"), 1000)
	c := New(s, b)
	vecs, err := c.Embed(context.Background(), []string{"你好"})
	if err != nil || vecs[0][0] != 0.1 {
		t.Fatalf("vecs = %v, %v", vecs, err)
	}
	if !c.HasEmbedKey() {
		t.Error("回退后 HasEmbedKey 应为 true")
	}
}

func TestEmbedNoEmbedKeyErrors(t *testing.T) {
	// 完全无 key：embed 通道不可用且调用报 ErrNoKey
	s := config.Default()
	s.LLMKey = ""
	s.EmbedAPIKey = ""
	c := New(s, budget.New(filepath.Join(t.TempDir(), "u.json"), 100))
	if c.HasEmbedKey() {
		t.Error("无任何 key 时 HasEmbedKey 应为 false")
	}
	if _, err := c.Embed(context.Background(), []string{"你好"}); !errors.Is(err, ErrNoKey) {
		t.Fatalf("err = %v, want ErrNoKey", err)
	}
}

// ---------- P7-2：embedding 并发健壮性 ----------

func TestEmbedMultimodalFirstErrorCancelsInflight(t *testing.T) {
	var mu sync.Mutex
	inflight, cancelled := 0, 0
	c, _ := multimodalClient(t, nil, func(w http.ResponseWriter, r *http.Request) {
		var body map[string]any
		_ = json.NewDecoder(r.Body).Decode(&body)
		text := body["input"].([]any)[0].(map[string]any)["text"].(string)
		mu.Lock()
		inflight++
		mu.Unlock()
		if text == "b" {
			// 等至少 2 个其余请求真正进入在飞后再失败——
			// worker pool 可能在取消前就跳过未发出的请求，无屏障时取消不可观测。
			deadline := time.Now().Add(2 * time.Second)
			for {
				mu.Lock()
				n := inflight
				mu.Unlock()
				if n >= 3 || time.Now().After(deadline) {
					break
				}
				time.Sleep(5 * time.Millisecond)
			}
			w.WriteHeader(http.StatusInternalServerError)
			_, _ = w.Write([]byte(`{"error":"boom"}`))
			return
		}
		// 其余请求挂住，直到 ctx 被取消（首错即停）或超时兜底
		select {
		case <-r.Context().Done():
			mu.Lock()
			cancelled++
			mu.Unlock()
		case <-time.After(3 * time.Second):
		}
	})
	_, err := c.Embed(context.Background(), []string{"a", "b", "c", "d", "e", "f"})
	if err == nil || !strings.Contains(err.Error(), "向量化失败") {
		t.Fatalf("单条 500 应整体报错: %v", err)
	}
	// 服务端 Done 分支的计数与客户端往返结束之间存在观测竞态，短暂轮询再断言。
	deadline := time.Now().Add(500 * time.Millisecond)
	n := 0
	for {
		mu.Lock()
		n = cancelled
		mu.Unlock()
		if n > 0 || time.Now().After(deadline) {
			break
		}
		time.Sleep(5 * time.Millisecond)
	}
	if n == 0 {
		t.Error("其余在途请求应因 ctx 取消快速退出")
	}
}

func TestEmbedMultimodalInflightCapByWorkerPool(t *testing.T) {
	var mu sync.Mutex
	cur, peak := 0, 0
	c, _ := multimodalClient(t, nil, func(w http.ResponseWriter, r *http.Request) {
		mu.Lock()
		cur++
		if cur > peak {
			peak = cur
		}
		mu.Unlock()
		time.Sleep(40 * time.Millisecond) // 制造可观测的在飞窗口
		mu.Lock()
		cur--
		mu.Unlock()
		_, _ = w.Write([]byte(`{"data":{"embedding":[0.1]}}`))
	})
	// 12 条输入远多于并发度 4：在飞峰值必须 ≤ 4（而非一次性 12 个 goroutine）
	if _, err := c.Embed(context.Background(), []string{"一", "二", "三", "四", "五", "六", "七", "八", "九", "十", "十一", "十二"}); err != nil {
		t.Fatal(err)
	}
	mu.Lock()
	defer mu.Unlock()
	if peak > embedConcurrency {
		t.Errorf("在飞请求数峰值 = %d, 应 ≤ %d", peak, embedConcurrency)
	}
	if peak < 2 {
		t.Errorf("在飞峰值 = %d, 并发未生效", peak)
	}
}

func TestEmbedTextMissingIndexErrors(t *testing.T) {
	// text 通道：2 条输入但响应漏回 index=1 → 明确报错，不静默返回 nil 向量
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_, _ = w.Write([]byte(`{"data":[{"index":0,"embedding":[0.1,0.2]}]}`))
	}))
	t.Cleanup(srv.Close)
	s := config.Default()
	s.LLMKey = "k"
	s.LLMBaseURL = srv.URL + "/"
	c := New(s, budget.New(filepath.Join(t.TempDir(), "u.json"), 1000))
	_, err := c.Embed(context.Background(), []string{"a", "b"})
	if err == nil || !strings.Contains(err.Error(), "第 1 条") {
		t.Fatalf("漏回 index 应报错: %v", err)
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

// ---------- agent-first：原生 tool-calling ----------

func testTools() []ToolDef {
	return []ToolDef{{
		Type: "function",
		Function: ToolFunction{
			Name:        "book_venue",
			Description: "预约场馆",
			Parameters: map[string]any{
				"type":       "object",
				"properties": map[string]any{"venue": map[string]any{"type": "string"}},
				"required":   []string{"venue"},
			},
		},
	}}
}

func TestChatWithToolsSendsToolsAndParsesCalls(t *testing.T) {
	var gotBody map[string]any
	c, b := testClient(t, func(w http.ResponseWriter, r *http.Request) {
		_ = json.NewDecoder(r.Body).Decode(&gotBody)
		_, _ = w.Write([]byte(`{"choices":[{"message":{"content":"","tool_calls":[` +
			`{"id":"c1","type":"function","function":{"name":"book_venue","arguments":"{\"venue\":\"羽毛球馆\"}"}}` +
			`]}}],"usage":{"total_tokens":50}}`))
	})
	comp, err := c.ChatWithTools(context.Background(),
		[]Message{{Role: "user", Content: "帮我预约"}}, Options{}, testTools())
	if err != nil {
		t.Fatal(err)
	}
	if len(comp.ToolCalls) != 1 || comp.ToolCalls[0].Name != "book_venue" {
		t.Fatalf("tool_calls = %+v", comp.ToolCalls)
	}
	if comp.ToolCalls[0].Arguments != `{"venue":"羽毛球馆"}` {
		t.Errorf("arguments = %q", comp.ToolCalls[0].Arguments)
	}
	if b.Used() != 50 {
		t.Errorf("usage 入账 = %d", b.Used())
	}
	tools, _ := gotBody["tools"].([]any)
	if len(tools) != 1 {
		t.Fatalf("请求应携带 1 个工具: %v", gotBody["tools"])
	}
	fn := tools[0].(map[string]any)["function"].(map[string]any)
	if fn["name"] != "book_venue" || fn["description"] != "预约场馆" {
		t.Errorf("工具定义 = %v", fn)
	}
	if _, has := gotBody["response_format"]; has {
		t.Error("ChatWithTools 不应设置 response_format（与 tools 可能互斥）")
	}
}

func TestChatWithToolsFinalWithoutCalls(t *testing.T) {
	c, _ := testClient(t, func(w http.ResponseWriter, r *http.Request) {
		_, _ = w.Write([]byte(`{"choices":[{"message":{"content":"最终回答"}}]}`))
	})
	comp, err := c.ChatWithTools(context.Background(),
		[]Message{{Role: "user", Content: "hi"}}, Options{}, testTools())
	if err != nil {
		t.Fatal(err)
	}
	if len(comp.ToolCalls) != 0 || comp.Content != "最终回答" {
		t.Fatalf("comp = %+v", comp)
	}
}

func TestMessageToolProtocolRoundTrip(t *testing.T) {
	// assistant 的 tool_calls 与 tool 结果的 tool_call_id 必须按协议回传端点
	var bodies []map[string]any
	c, _ := testClient(t, func(w http.ResponseWriter, r *http.Request) {
		var body map[string]any
		_ = json.NewDecoder(r.Body).Decode(&body)
		bodies = append(bodies, body)
		_, _ = w.Write([]byte(`{"choices":[{"message":{"content":"done"}}]}`))
	})
	var assistantMsg Message
	assistantMsg.Role = "assistant"
	assistantMsg.ToolCalls = []ToolCallMsg{{ID: "c1", Type: "function"}}
	assistantMsg.ToolCalls[0].Function.Name = "book_venue"
	assistantMsg.ToolCalls[0].Function.Arguments = `{}`
	msgs := []Message{
		{Role: "user", Content: "q"},
		assistantMsg,
		{Role: "tool", ToolCallID: "c1", Content: "observation"},
	}
	_, err := c.ChatWithTools(context.Background(), msgs, Options{}, testTools())
	if err != nil {
		t.Fatal(err)
	}
	sent := bodies[0]["messages"].([]any)
	asst := sent[1].(map[string]any)
	calls := asst["tool_calls"].([]any)
	if len(calls) != 1 || calls[0].(map[string]any)["id"] != "c1" {
		t.Errorf("assistant.tool_calls = %v", calls)
	}
	toolMsg := sent[2].(map[string]any)
	if toolMsg["role"] != "tool" || toolMsg["tool_call_id"] != "c1" || toolMsg["content"] != "observation" {
		t.Errorf("tool 消息 = %v", toolMsg)
	}
}

// ---------- P10-1：响应侧 finish_reason + usage 三元组 ----------

func TestChatWithToolsFinishReasonPassthrough(t *testing.T) {
	for _, fr := range []string{"stop", "tool_calls", "length"} {
		c, _ := testClient(t, func(w http.ResponseWriter, r *http.Request) {
			_, _ = w.Write([]byte(`{"choices":[{"message":{"content":"","tool_calls":[` +
				`{"id":"c1","type":"function","function":{"name":"book_venue","arguments":"{}"}}` +
				`]},"finish_reason":"` + fr + `"}],"usage":{"total_tokens":10}}`))
		})
		comp, err := c.ChatWithTools(context.Background(),
			[]Message{{Role: "user", Content: "帮我预约"}}, Options{}, testTools())
		if err != nil {
			t.Fatalf("finish_reason=%s: %v", fr, err)
		}
		if comp.FinishReason != fr {
			t.Errorf("FinishReason = %q, want %q", comp.FinishReason, fr)
		}
	}
}

func TestChatWithToolsFinishReasonAbsentIsEmpty(t *testing.T) {
	// 兼容不回 finish_reason 字段的端点：解析后为空串，不影响既有行为
	c, _ := testClient(t, func(w http.ResponseWriter, r *http.Request) {
		_, _ = w.Write([]byte(`{"choices":[{"message":{"content":"最终回答"}}]}`))
	})
	comp, err := c.ChatWithTools(context.Background(),
		[]Message{{Role: "user", Content: "hi"}}, Options{}, testTools())
	if err != nil {
		t.Fatal(err)
	}
	if comp.FinishReason != "" {
		t.Errorf("缺字段时 FinishReason = %q, want 空串", comp.FinishReason)
	}
}

func TestUsageTripleParsedBudgetTotalOnly(t *testing.T) {
	var resp chatResponse
	if err := json.Unmarshal([]byte(
		`{"usage":{"prompt_tokens":30,"completion_tokens":18,"total_tokens":48}}`), &resp); err != nil {
		t.Fatal(err)
	}
	if resp.Usage == nil || resp.Usage.PromptTokens != 30 ||
		resp.Usage.CompletionTokens != 18 || resp.Usage.TotalTokens != 48 {
		t.Fatalf("usage 三元组 = %+v, want 30/18/48", resp.Usage)
	}
	// 记账口径不变：budget 仍只入账 total_tokens
	c, b := testClient(t, func(w http.ResponseWriter, r *http.Request) {
		_, _ = w.Write([]byte(`{"choices":[{"message":{"content":"ok"}}],` +
			`"usage":{"prompt_tokens":30,"completion_tokens":18,"total_tokens":48}}`))
	})
	if _, err := c.Chat(context.Background(), []Message{{Role: "user", Content: "q"}}, Options{}); err != nil {
		t.Fatal(err)
	}
	if b.Used() != 48 {
		t.Errorf("budget 入账 = %d, want 48（只吃 total_tokens）", b.Used())
	}
}
