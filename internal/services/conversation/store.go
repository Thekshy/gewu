// Package conversation 会话服务：办理流程跨轮状态（PARITY §12.3 逐字：
// TTL 30 分钟 get/ensure 惰性清理；ensure 刷新 role/user；clear 移除）
// + 消息落库（新增，仅审计/前端展示，不参与答案生成）。
//
// 存储：PG（服务重启后会话保留——有意差异，PARITY-MS 备案）；
// 内存实现供单测与无 PG 降级使用，语义套件共用同一测试。
package conversation

import (
	"context"
	"sync"
	"time"
)

// ttl 会话过期时间（PARITY §12.3）。
const ttl = 30 * time.Minute

// Session 会话状态（对齐冻结单体 agent.TxSession）。
type Session struct {
	SessionID string
	Role      string
	User      string
	Phase     string // idle | collect | confirm
	Tool      string
	Slots     map[string]string
	LastAsked string
	Updated   time.Time
}

// Message 审计消息。
type Message struct {
	SessionID string
	Role      string // user | assistant
	Content   string
}

// Store 会话存储接口：语义契约由 conversation_semantics_test.go 锁定，
// PG 与内存两个实现必须通过同一套件。
type Store interface {
	// Get 取会话（不存在或已过期返回 nil）；触发惰性清理。
	Get(ctx context.Context, sessionID string) (*Session, error)
	// Ensure 取或建会话并刷新 role/user/时间戳。
	Ensure(ctx context.Context, sessionID, role, user string) (*Session, error)
	// Save 全量保存（编排侧变更后回写）。
	Save(ctx context.Context, s *Session) error
	// Clear 移除会话（办理完成/取消/切话题）。
	Clear(ctx context.Context, sessionID string) error
	// AppendMessage 审计落库（尽力而为，不阻断主链路）。
	AppendMessage(ctx context.Context, m Message) error
}

// ---------- 内存实现 ----------

// MemoryStore 进程内 map + TTL 惰性清理（冻结单体 SessionStore 语义同源）。
type MemoryStore struct {
	mu    sync.Mutex
	data  map[string]*Session
	clock func() time.Time
}

// NewMemoryStore 构造内存会话存储（clock 可注入，测试用）。
func NewMemoryStore() *MemoryStore { return &MemoryStore{data: map[string]*Session{}, clock: time.Now} }

func (m *MemoryStore) evictLocked() {
	now := m.clock()
	for sid, sess := range m.data {
		if now.Sub(sess.Updated) > ttl {
			delete(m.data, sid)
		}
	}
}

// Get 取会话（过期返回 nil）。
func (m *MemoryStore) Get(ctx context.Context, sessionID string) (*Session, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	m.evictLocked()
	sess := m.data[sessionID]
	if sess == nil {
		return nil, nil
	}
	cp := *sess
	cp.Slots = copySlots(sess.Slots)
	return &cp, nil
}

// Ensure 取或建会话，并刷新 role/user/时间戳。
func (m *MemoryStore) Ensure(ctx context.Context, sessionID, role, user string) (*Session, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	m.evictLocked()
	sess, ok := m.data[sessionID]
	if !ok {
		sess = &Session{SessionID: sessionID, Phase: "idle", Slots: map[string]string{}}
		m.data[sessionID] = sess
	}
	sess.Role, sess.User = role, user
	sess.Updated = m.clock()
	cp := *sess
	cp.Slots = copySlots(sess.Slots)
	return &cp, nil
}

// Save 全量保存（写入即活动：刷新时间戳，否则零值时间会被 TTL 立即驱逐）。
func (m *MemoryStore) Save(ctx context.Context, s *Session) error {
	m.mu.Lock()
	defer m.mu.Unlock()
	cp := *s
	cp.Slots = copySlots(s.Slots)
	cp.Updated = m.clock()
	m.data[s.SessionID] = &cp
	return nil
}

// Clear 移除会话。
func (m *MemoryStore) Clear(ctx context.Context, sessionID string) error {
	m.mu.Lock()
	defer m.mu.Unlock()
	delete(m.data, sessionID)
	return nil
}

// AppendMessage 内存实现忽略（审计仅持久化实现关心）。
func (m *MemoryStore) AppendMessage(ctx context.Context, msg Message) error { return nil }

func copySlots(src map[string]string) map[string]string {
	out := make(map[string]string, len(src))
	for k, v := range src {
		out[k] = v
	}
	return out
}
