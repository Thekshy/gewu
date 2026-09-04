package generate

import (
	"strconv"
	"sync"
	"time"

	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"
)

// budgetMeter 每日 token 预算计量：UTC 日期滚动、原子累计（P1 进程内存实现；
// PG/Redis 持久化随 P2「状态与配置」阶段接入——单实例 generate 下语义等价，
// 重启清零属 P1 已知限制，PARITY-MS 备案）。
//
// 计量口径（PARITY §12.2 逐字）：非流式按 usage.total_tokens；流式按产出
// 字符数（rune）/2 下限 1，中途断开也入账；embedding 按 usage，缺省按
// 输入文本 rune 总和/2。
type budgetMeter struct {
	mu    sync.Mutex
	date  string // 当前累计所属 UTC 日期（YYYY-MM-DD）
	used  int64
	limit int64
}

func newBudgetMeter(limit int64) *budgetMeter {
	m := &budgetMeter{limit: limit}
	m.rolloverLocked()
	return m
}

func todayUTC() string { return time.Now().UTC().Format("2006-01-02") }

func (m *budgetMeter) rolloverLocked() {
	if today := todayUTC(); today != m.date {
		m.date = today
		m.used = 0
	}
}

// exceeded 预算耗尽错误：message 与冻结单体逐字一致
// （HTTP 429 与 SSE error 事件都直接用这段文案）。
func (m *budgetMeter) exceeded() error {
	return status.Error(codes.ResourceExhausted,
		"今日 token 预算已用尽（上限 "+strconv.FormatInt(m.limit, 10)+"），请明天再试")
}

// ensure 用量达到上限时返回 RESOURCE_EXHAUSTED。
func (m *budgetMeter) ensure() error {
	m.mu.Lock()
	defer m.mu.Unlock()
	m.rolloverLocked()
	if m.used >= m.limit {
		return m.exceeded()
	}
	return nil
}

// add 累计用量（跨天自动滚动清零）。
func (m *budgetMeter) add(n int64) {
	if n <= 0 {
		return
	}
	m.mu.Lock()
	defer m.mu.Unlock()
	m.rolloverLocked()
	m.used += n
}

// snapshot 今日用量与上限（/api/health 聚合展示）。
func (m *budgetMeter) snapshot() (used, limit int64) {
	m.mu.Lock()
	defer m.mu.Unlock()
	m.rolloverLocked()
	return m.used, m.limit
}
