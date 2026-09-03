package agent

import (
	"sync"
	"time"
)

// ttl 会话过期时间。
const ttl = 30 * time.Minute

// Phase 办理流程阶段。
const (
	PhaseIdle    = "idle"
	PhaseCollect = "collect"
	PhaseConfirm = "confirm"
)

// TxSession 知行执行层的跨轮状态。
type TxSession struct {
	SessionID string
	Role      string
	User      string
	Phase     string // idle | collect | confirm
	Tool      string
	Slots     map[string]string
	LastAsked string
	Updated   time.Time
}

// SessionStore 会话状态存储：进程内 map + TTL 惰性清理。
type SessionStore struct {
	mu    sync.Mutex
	data  map[string]*TxSession
	clock func() time.Time // 可注入，测试用
}

// NewSessionStore 构造会话存储。
func NewSessionStore() *SessionStore {
	return &SessionStore{data: map[string]*TxSession{}, clock: time.Now}
}

// evictLocked 清理过期会话；需持有 mu。
func (s *SessionStore) evictLocked() {
	now := s.clock()
	for sid, sess := range s.data {
		if now.Sub(sess.Updated) > ttl {
			delete(s.data, sid)
		}
	}
}

// Get 取会话（过期返回 nil）。
func (s *SessionStore) Get(sessionID string) *TxSession {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.evictLocked()
	return s.data[sessionID]
}

// Ensure 取或建会话，并刷新 role/user/时间戳。
func (s *SessionStore) Ensure(sessionID, role, user string) *TxSession {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.evictLocked()
	sess, ok := s.data[sessionID]
	if !ok {
		sess = &TxSession{SessionID: sessionID, Phase: PhaseIdle, Slots: map[string]string{}}
		s.data[sessionID] = sess
	}
	sess.Role, sess.User = role, user
	sess.Updated = s.clock()
	return sess
}

// Clear 移除会话（办理完成/取消/切话题）。
func (s *SessionStore) Clear(sessionID string) {
	s.mu.Lock()
	defer s.mu.Unlock()
	delete(s.data, sessionID)
}
