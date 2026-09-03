package main

import (
	"bufio"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"path/filepath"
	"strings"
	"testing"

	"gewu/internal/agent"
	"gewu/internal/budget"
	"gewu/internal/business"
	"gewu/internal/config"
	"gewu/internal/llm"
	"gewu/internal/rag"
)

// newTestServer 构造零 key 模式的完整 HTTP 服务（临时数据目录）。
func newTestServer(t *testing.T, rateLimit int) (*server, *httptest.Server) {
	t.Helper()
	dir := t.TempDir()
	settings := config.Default()
	settings.DataDir = dir
	settings.IndexPath = filepath.Join(dir, "index.db")
	settings.RateLimitPerMinute = rateLimit

	store, err := rag.Open(settings.IndexPath)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = store.Close() })
	// 种两条可检索语料
	if err := store.UpsertDoc("0001-transfer", "转专业管理办法", "教务处", "2026-01-01",
		[]string{"申请转专业的条件：绩点排名前 20%，无不及格课程。"}, nil); err != nil {
		t.Fatal(err)
	}
	biz, err := business.Open(filepath.Join(dir, "business.db"))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = biz.Close() })

	tb := budget.New(filepath.Join(dir, "usage.json"), settings.DailyTokenBudget)
	lc := llm.New(settings, tb) // 无 key → 零 key 模式
	deps := agent.NewDeps(settings, lc, rag.NewRetriever(store, settings.RetrievalK, lc), biz)
	srv := &server{deps: deps, store: store, budget: tb, llm: lc, settings: settings}
	ts := httptest.NewServer(srv.newRouter())
	t.Cleanup(ts.Close)
	return srv, ts
}

// sseEvents POST /api/chat 并解析全部 SSE 事件为 JSON map。
func sseEvents(t *testing.T, base string, body string) (int, []map[string]any) {
	t.Helper()
	resp, err := http.Post(base+"/api/chat", "application/json", strings.NewReader(body))
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	var events []map[string]any
	scanner := bufio.NewScanner(resp.Body)
	scanner.Buffer(make([]byte, 1024*1024), 1024*1024)
	for scanner.Scan() {
		line := scanner.Text()
		if strings.HasPrefix(line, "data: ") {
			var ev map[string]any
			if err := json.Unmarshal([]byte(line[6:]), &ev); err != nil {
				t.Fatalf("事件不是合法 JSON: %q", line)
			}
			events = append(events, ev)
		}
	}
	return resp.StatusCode, events
}

func TestHealthEndpoint(t *testing.T) {
	_, ts := newTestServer(t, 20)
	resp, err := http.Get(ts.URL + "/api/health")
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	var h map[string]any
	_ = json.NewDecoder(resp.Body).Decode(&h)
	if h["status"] != "ok" || h["llm"] != false || h["version"] != "0.1.0" {
		t.Errorf("health = %v", h)
	}
	if _, ok := h["budget"].(map[string]any); !ok {
		t.Errorf("budget 形态: %v", h["budget"])
	}
}

func TestDocsEndpoint(t *testing.T) {
	_, ts := newTestServer(t, 20)
	resp, err := http.Get(ts.URL + "/api/docs")
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	var docs []map[string]any
	_ = json.NewDecoder(resp.Body).Decode(&docs)
	if len(docs) != 1 || docs[0]["doc_id"] != "0001-transfer" || docs[0]["chunks"] != float64(1) {
		t.Errorf("docs = %v", docs)
	}
}

func TestSearchEndpoint(t *testing.T) {
	_, ts := newTestServer(t, 20)
	resp, err := http.Post(ts.URL+"/api/search", "application/json",
		strings.NewReader(`{"query": "转专业条件", "k": 3}`))
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	var hits []map[string]any
	_ = json.NewDecoder(resp.Body).Decode(&hits)
	if len(hits) == 0 || hits[0]["doc_id"] != "0001-transfer" {
		t.Errorf("hits = %v", hits)
	}
}

func TestSearchValidation(t *testing.T) {
	_, ts := newTestServer(t, 100)
	for _, body := range []string{
		`{"query": "", "k": 3}`,
		`{"query": "x", "k": 0}`,
		`{"query": "x", "k": 99}`,
	} {
		resp, _ := http.Post(ts.URL+"/api/search", "application/json", strings.NewReader(body))
		if resp.StatusCode != http.StatusUnprocessableEntity {
			t.Errorf("body %s → %d, want 422", body, resp.StatusCode)
		}
		resp.Body.Close()
	}
}

func TestChatSSEZeroKeyFactual(t *testing.T) {
	_, ts := newTestServer(t, 100)
	code, events := sseEvents(t, ts.URL, `{"question": "转专业需要什么条件", "mode": "auto"}`)
	if code != http.StatusOK {
		t.Fatalf("code = %d", code)
	}
	if len(events) < 3 {
		t.Fatalf("events = %v", events)
	}
	if events[0]["type"] != "route" || events[0]["route"] != "factual" {
		t.Errorf("首事件应为 route/factual: %v", events[0])
	}
	if events[0]["by_llm"] != false {
		t.Errorf("零 key 应 by_llm=false: %v", events[0])
	}
	var answer string
	var hasCitations bool
	for _, ev := range events {
		if ev["type"] == "answer_delta" {
			answer += ev["text"].(string)
		}
		if ev["type"] == "citations" {
			hasCitations = true
			items, ok := ev["items"].([]any)
			if !ok || len(items) == 0 {
				t.Errorf("citations.items 应为非空数组: %v", ev)
			}
		}
	}
	if !strings.Contains(answer, "检索演示模式") {
		t.Errorf("零 key 应走演示模式: %q", answer)
	}
	if !hasCitations {
		t.Error("缺 citations 事件")
	}
	last := events[len(events)-1]
	lastType, _ := last["type"].(string)
	if lastType != "done" {
		t.Errorf("末事件应为 done: %v", last)
	}
	if _, ok := last["latency_ms"].(float64); !ok {
		t.Errorf("done 应含 latency_ms: %v", last)
	}
}

func TestChatSSETransactionFlow(t *testing.T) {
	_, ts := newTestServer(t, 100)
	// 第一轮：预约（信息一次给全 → 直接确认）
	code, events := sseEvents(t, ts.URL,
		`{"question": "帮我预约明天晚上的羽毛球馆", "session_id": "s1"}`)
	if code != http.StatusOK {
		t.Fatal(code)
	}
	var pending map[string]any
	for _, ev := range events {
		if ev["type"] == "pending_action" {
			pending = ev
		}
	}
	if pending == nil || pending["tool"] != "book_venue" {
		t.Fatalf("pending = %v", pending)
	}
	args, _ := pending["args"].(map[string]any)
	if args["场馆"] != "羽毛球馆" || args["时段"] != "19:00-21:00" {
		t.Errorf("args = %v（场馆应显示名称而非 ID）", args)
	}

	// 第二轮：确认 → 执行 → 回执
	_, events = sseEvents(t, ts.URL, `{"question": "确认", "session_id": "s1"}`)
	var result map[string]any
	for _, ev := range events {
		if ev["type"] == "action_result" {
			result = ev
		}
	}
	if result == nil || result["success"] != true {
		t.Fatalf("result = %v", result)
	}
	if !strings.HasPrefix(result["receipt"].(string), "VE-") {
		t.Errorf("receipt = %v", result["receipt"])
	}

	// overview 应能看到预约
	resp, _ := http.Get(ts.URL + "/api/business/overview")
	var ov map[string]any
	_ = json.NewDecoder(resp.Body).Decode(&ov)
	resp.Body.Close()
	bookings := ov["bookings"].([]any)
	if len(bookings) != 1 {
		t.Fatalf("bookings = %v", bookings)
	}
	b := bookings[0].(map[string]any)
	if b["venue"] != "羽毛球馆" || b["slot"] != "19:00-21:00" {
		t.Errorf("booking = %v", b)
	}

	// reset 后清空
	resp2, _ := http.Post(ts.URL+"/api/business/reset", "application/json", nil)
	var rr map[string]any
	_ = json.NewDecoder(resp2.Body).Decode(&rr)
	resp2.Body.Close()
	if rr["status"] != "ok" {
		t.Errorf("reset = %v", rr)
	}
	resp3, _ := http.Get(ts.URL + "/api/business/overview")
	var ov2 map[string]any
	_ = json.NewDecoder(resp3.Body).Decode(&ov2)
	resp3.Body.Close()
	if n := len(ov2["bookings"].([]any)); n != 0 {
		t.Errorf("reset 后 bookings = %d", n)
	}
}

func TestChatValidationAndRefusalShape(t *testing.T) {
	_, ts := newTestServer(t, 1000)
	resp, _ := http.Post(ts.URL+"/api/chat", "application/json",
		strings.NewReader(`{"question": "`+strings.Repeat("长", 501)+`"}`))
	if resp.StatusCode != http.StatusUnprocessableEntity {
		t.Errorf("超长问题 → %d, want 422", resp.StatusCode)
	}
	resp.Body.Close()

	resp2, _ := http.Post(ts.URL+"/api/chat", "application/json",
		strings.NewReader(`{"question": "ok", "mode": "bogus"}`))
	if resp2.StatusCode != http.StatusUnprocessableEntity {
		t.Errorf("非法 mode → %d, want 422", resp2.StatusCode)
	}
	resp2.Body.Close()
}

func TestRateLimitOnChat(t *testing.T) {
	_, ts := newTestServer(t, 2)
	body := `{"question": "转专业条件"}`
	last := 0
	for i := 0; i < 3; i++ {
		resp, err := http.Post(ts.URL+"/api/chat", "application/json", strings.NewReader(body))
		if err != nil {
			t.Fatal(err)
		}
		io.Copy(io.Discard, resp.Body)
		resp.Body.Close()
		last = resp.StatusCode
	}
	if last != http.StatusTooManyRequests {
		t.Errorf("第 3 次 = %d, want 429", last)
	}
}

func TestMarshalNoEscapeKeepsUTF8(t *testing.T) {
	out, err := marshalNoEscape(map[string]string{"text": "中文《测试》"})
	if err != nil {
		t.Fatal(err)
	}
	got := string(out)
	if !strings.Contains(got, "中文《测试》") {
		t.Errorf("out = %s", got)
	}
}
