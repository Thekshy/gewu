// Package budget 实现每日 token 预算：公开 demo 的成本防线，超限直接拒绝 LLM 调用。
//
// 预算以 UTC 日期为周期边界重置，持久化在 data/usage.json，跨重启有效。
// Python 版每次查询都重新读文件（见 go-notes：原设计问题 #2），Go 版改为
// 进程内计数 + 写穿持久化，读路径零系统调用。
package budget

import (
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"sync"
	"time"
)

// ErrBudgetExceeded 预算耗尽的哨兵值（errors.Is 判定用）。
var ErrBudgetExceeded = errors.New("budget exceeded")

// ExceededError 预算耗尽的具体错误：Error() 直接返回面向用户的中文消息，
// 同时支持 errors.Is(err, ErrBudgetExceeded)。
type ExceededError struct{ Message string }

func (e *ExceededError) Error() string        { return e.Message }
func (e *ExceededError) Is(target error) bool { return target == ErrBudgetExceeded }

// TokenBudget 以天为单位累计 token 用量。
type TokenBudget struct {
	path  string
	limit int64

	mu   sync.Mutex
	date string // 当前累计所属的 UTC 日期（YYYY-MM-DD）
	used int64
}

type usageFile struct {
	Date   string `json:"date"`
	Tokens int64  `json:"tokens"`
}

// New 构造预算器并从磁盘加载今日用量（文件缺失/损坏视为 0，日期滚动自动清零）。
func New(path string, dailyLimit int64) *TokenBudget {
	b := &TokenBudget{path: path, limit: dailyLimit}
	b.mu.Lock()
	defer b.mu.Unlock()
	b.reloadLocked()
	return b
}

// todayUTC 当前 UTC 日期。
func todayUTC() string { return time.Now().UTC().Format("2006-01-02") }

// reloadLocked 读取磁盘状态；需持有 mu。
func (b *TokenBudget) reloadLocked() {
	b.date = todayUTC()
	b.used = 0
	data, err := os.ReadFile(b.path)
	if err != nil {
		return
	}
	var f usageFile
	if json.Unmarshal(data, &f) == nil && f.Date == b.date {
		b.used = f.Tokens
	}
}

// rolloverLocked 日期跨天后清零；需持有 mu。
func (b *TokenBudget) rolloverLocked() {
	if today := todayUTC(); today != b.date {
		b.date = today
		b.used = 0
	}
}

// Used 今日已用 token 数。
func (b *TokenBudget) Used() int64 {
	b.mu.Lock()
	defer b.mu.Unlock()
	b.rolloverLocked()
	return b.used
}

// Ensure 用量达到上限时返回 *ExceededError（errors.Is 可判定 ErrBudgetExceeded），
// 错误消息与 Python 版逐字一致（HTTP 429 与 SSE error 事件都直接用它）。
func (b *TokenBudget) Ensure() error {
	if b.Used() >= b.limit {
		return &ExceededError{Message: fmt.Sprintf("今日 token 预算已用尽（上限 %d），请明天再试", b.limit)}
	}
	return nil
}

// Add 累计用量并写穿持久化（原子替换文件）。
func (b *TokenBudget) Add(n int64) {
	if n <= 0 {
		return
	}
	b.mu.Lock()
	defer b.mu.Unlock()
	b.rolloverLocked()
	b.used += n
	b.persistLocked()
}

// persistLocked 落盘；需持有 mu。
func (b *TokenBudget) persistLocked() {
	_ = os.MkdirAll(filepath.Dir(b.path), 0o755)
	data, err := json.Marshal(usageFile{Date: b.date, Tokens: b.used})
	if err != nil {
		return
	}
	tmp := b.path + ".tmp"
	if err := os.WriteFile(tmp, data, 0o644); err != nil {
		return
	}
	_ = os.Rename(tmp, b.path)
}

// Limit 每日上限（/api/health 展示用）。
func (b *TokenBudget) Limit() int64 { return b.limit }
