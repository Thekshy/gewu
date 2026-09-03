// Package middleware 提供 HTTP 中间件：按 IP 令牌桶限流（公开 demo 的第一道防线）。
package middleware

import (
	"fmt"
	"net/http"
	"strconv"
	"sync"
	"time"

	"github.com/gin-gonic/gin"
)

// bucket 单 IP 令牌桶状态。
type bucket struct {
	tokens float64
	last   time.Time
}

// RateLimiter 按 IP 的令牌桶限流器：容量=速率=limit/分钟，按时间线性回充。
// 桶表进程内存储、互斥锁保护（读多写少但计算简单，锁足够）。
type RateLimiter struct {
	rate    float64 // 每分钟令牌数（即桶容量）
	buckets map[string]*bucket
	mu      sync.Mutex
	now     func() time.Time // 可注入，测试用
}

// NewRateLimiter 构造限流器，limitPerMinute 下限 1。
func NewRateLimiter(limitPerMinute int) *RateLimiter {
	if limitPerMinute < 1 {
		limitPerMinute = 1
	}
	return &RateLimiter{
		rate:    float64(limitPerMinute),
		buckets: map[string]*bucket{},
		now:     time.Now,
	}
}

// clientIP 取客户端 IP：X-Forwarded-For 首段（去空白），否则 TCP 对端地址。
func clientIP(c *gin.Context) string {
	fwd := c.GetHeader("X-Forwarded-For")
	if fwd != "" {
		for _, part := range splitComma(fwd) {
			if ip := trim(part); ip != "" {
				return ip
			}
		}
	}
	return c.ClientIP()
}

func splitComma(s string) []string {
	var out []string
	start := 0
	for i := 0; i <= len(s); i++ {
		if i == len(s) || s[i] == ',' {
			out = append(out, s[start:i])
			start = i + 1
		}
	}
	return out
}

func trim(s string) string {
	for len(s) > 0 && (s[0] == ' ' || s[0] == '\t') {
		s = s[1:]
	}
	for len(s) > 0 && (s[len(s)-1] == ' ' || s[len(s)-1] == '\t') {
		s = s[:len(s)-1]
	}
	return s
}

// Allow 尝试消费 1 个令牌；被限流时返回需等待秒数。
func (r *RateLimiter) Allow(ip string) (ok bool, retryAfter int) {
	r.mu.Lock()
	defer r.mu.Unlock()
	now := r.now()
	b, exists := r.buckets[ip]
	if !exists {
		b = &bucket{tokens: r.rate, last: now}
		r.buckets[ip] = b
	}
	b.tokens = minF(r.rate, b.tokens+now.Sub(b.last).Seconds()*r.rate/60.0)
	b.last = now
	if b.tokens < 1.0 {
		retry := int((1.0-b.tokens)*60.0/r.rate) + 1
		return false, retry
	}
	b.tokens -= 1.0
	return true, 0
}

func minF(a, b float64) float64 {
	if a < b {
		return a
	}
	return b
}

// Handler gin 中间件：被限流返回 429（中文提示 + Retry-After），放行附速率头。
func (r *RateLimiter) Handler() gin.HandlerFunc {
	return func(c *gin.Context) {
		ip := clientIP(c)
		if ok, retry := r.Allow(ip); !ok {
			c.Header("Retry-After", strconv.Itoa(retry))
			c.AbortWithStatusJSON(http.StatusTooManyRequests, gin.H{
				"detail": fmt.Sprintf("请求过于频繁，请 %d 秒后重试", retry),
			})
			return
		}
		c.Header("X-RateLimit-Limit-Minute", strconv.Itoa(int(r.rate)))
		c.Next()
	}
}
