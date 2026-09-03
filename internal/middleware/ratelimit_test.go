package middleware

import (
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/gin-gonic/gin"
)

func TestRateLimiterAllowsUpToBurst(t *testing.T) {
	rl := NewRateLimiter(3)
	for i := 0; i < 3; i++ {
		if ok, _ := rl.Allow("1.2.3.4"); !ok {
			t.Fatalf("第 %d 个请求应放行", i+1)
		}
	}
	if ok, retry := rl.Allow("1.2.3.4"); ok {
		t.Fatal("超出突发容量应限流")
	} else if retry < 1 {
		t.Errorf("retry = %d, 应 ≥1", retry)
	}
}

func TestRateLimiterIndependentPerIP(t *testing.T) {
	rl := NewRateLimiter(1)
	if ok, _ := rl.Allow("1.1.1.1"); !ok {
		t.Fatal("首个请求应放行")
	}
	if ok, _ := rl.Allow("1.1.1.1"); ok {
		t.Fatal("同 IP 第二个应限流")
	}
	if ok, _ := rl.Allow("2.2.2.2"); !ok {
		t.Fatal("不同 IP 不受影响")
	}
}

func TestRateLimiterRefillsOverTime(t *testing.T) {
	now := time.Now()
	rl := NewRateLimiter(1) // 容量 1，回满需 1 分钟
	rl.now = func() time.Time { return now }
	if ok, _ := rl.Allow("ip"); !ok {
		t.Fatal("放行")
	}
	if ok, _ := rl.Allow("ip"); ok {
		t.Fatal("应限流")
	}
	rl.now = func() time.Time { return now.Add(61 * time.Second) } // 回充超过 1 个令牌
	if ok, _ := rl.Allow("ip"); !ok {
		t.Fatal("回充后应放行")
	}
}

func TestRateLimiterMinimumOne(t *testing.T) {
	rl := NewRateLimiter(0)
	if rl.rate != 1 {
		t.Errorf("rate = %v, 下限应为 1", rl.rate)
	}
}

func TestHandlerReturns429WithHeaders(t *testing.T) {
	gin.SetMode(gin.TestMode)
	rl := NewRateLimiter(1)
	r := gin.New()
	r.Use(rl.Handler())
	r.GET("/x", func(c *gin.Context) { c.Status(http.StatusOK) })

	req := httptest.NewRequest(http.MethodGet, "/x", nil)
	req.RemoteAddr = "9.9.9.9:1234"
	w := httptest.NewRecorder()
	r.ServeHTTP(w, req)
	if w.Code != http.StatusOK || w.Header().Get("X-RateLimit-Limit-Minute") != "1" {
		t.Fatalf("first = %d %v", w.Code, w.Header())
	}

	w2 := httptest.NewRecorder()
	r.ServeHTTP(w2, req)
	if w2.Code != http.StatusTooManyRequests {
		t.Fatalf("second = %d, want 429", w2.Code)
	}
	if w2.Header().Get("Retry-After") == "" {
		t.Error("应带 Retry-After")
	}
	if !strings.Contains(w2.Body.String(), "请求过于频繁") {
		t.Errorf("body = %q", w2.Body.String())
	}
}

func TestClientIPPrefersForwardedFor(t *testing.T) {
	gin.SetMode(gin.TestMode)
	r := gin.New()
	var got string
	r.GET("/ip", func(c *gin.Context) {
		got = clientIP(c)
		c.Status(200)
	})
	req := httptest.NewRequest(http.MethodGet, "/ip", nil)
	req.RemoteAddr = "10.0.0.1:5555"
	req.Header.Set("X-Forwarded-For", " 8.8.8.8 , 10.0.0.2")
	w := httptest.NewRecorder()
	r.ServeHTTP(w, req)
	if got != "8.8.8.8" {
		t.Errorf("clientIP = %q, want 首段去空白 8.8.8.8", got)
	}
}
