package agent

import (
	"database/sql"
	"encoding/json"
	"fmt"
	"log"
	"sync"
	"time"

	_ "modernc.org/sqlite"
)

// P8-1 会话持久化（吸收自微服务 conversation 形态的能力缺口）：
// 办理流程（槽位收集/确认）进行中重启服务，进程内 map 会丢状态，SQLite 后端
// 让流程可以跨重启续办。
//
// 结构：进程内 map 是运行时真相源（指针语义与内存版逐条一致——调用方直接修改
// 返回的 *TxSession，无写回负担），SQLite 只做持久化镜像：
//   - Open 时全量加载 → map（重启恢复；TTL 惰性清理继续基于 Updated 倒计时，
//     所以「重启后 30 分钟内可续办」的语义不变）；
//   - Sync() 全量写透（幂等 UPSERT）——RunChat 每轮出口调用，任何路径对 sess
//     的修改在下一轮到来前都已落库；
//   - Clear 同步 DELETE（取消/完成立即从库中移除）。
//
// 单连接（SetMaxOpenConns(1)）与 memory.go 同模式。

// sqliteSessionStore SQLite 持久化的会话存储。
type sqliteSessionStore struct {
	mu    sync.Mutex
	db    *sql.DB
	data  map[string]*TxSession
	clock func() time.Time
}

// OpenSessionStore 打开（或创建）SQLite 会话库并全量加载。
// 建议独立文件 data/sessions.db（与索引/业务/记忆库职责分离）。
func OpenSessionStore(path string) (SessionStore, error) {
	return openSessionStore(path, time.Now)
}

func openSessionStore(path string, clock func() time.Time) (SessionStore, error) {
	db, err := sql.Open("sqlite", path)
	if err != nil {
		return nil, err
	}
	db.SetMaxOpenConns(1)
	// DDL 为静态字面量，幂等；逐条执行便于定位失败。
	if _, err := db.Exec(`CREATE TABLE IF NOT EXISTS tx_sessions (
		session_id TEXT PRIMARY KEY,
		role       TEXT NOT NULL DEFAULT '',
		user       TEXT NOT NULL DEFAULT '',
		phase      TEXT NOT NULL DEFAULT 'idle',
		tool       TEXT NOT NULL DEFAULT '',
		slots      TEXT NOT NULL DEFAULT '{}',
		last_asked TEXT NOT NULL DEFAULT '',
		updated_at TEXT NOT NULL
	)`); err != nil {
		db.Close()
		return nil, fmt.Errorf("建 tx_sessions 表失败: %w", err)
	}
	s := &sqliteSessionStore{db: db, data: map[string]*TxSession{}, clock: clock}
	if err := s.loadAll(); err != nil {
		db.Close()
		return nil, err
	}
	return s, nil
}

// loadAll 启动时全量加载：会话量级为个位数，一次读入后运行期不再查库。
// 单条损坏（slots 非 JSON / 时间不可解析）丢弃该条并告警，不影响其余会话。
func (s *sqliteSessionStore) loadAll() error {
	rows, err := s.db.Query(`SELECT session_id, role, user, phase, tool, slots, last_asked, updated_at FROM tx_sessions`)
	if err != nil {
		return fmt.Errorf("加载会话失败: %w", err)
	}
	defer rows.Close()
	for rows.Next() {
		var sess TxSession
		var slotsJSON, updated string
		if err := rows.Scan(&sess.SessionID, &sess.Role, &sess.User, &sess.Phase,
			&sess.Tool, &slotsJSON, &sess.LastAsked, &updated); err != nil {
			return fmt.Errorf("读取会话行失败: %w", err)
		}
		if err := json.Unmarshal([]byte(slotsJSON), &sess.Slots); err != nil {
			log.Printf("[agent] 会话 %s slots 损坏，丢弃：%v", sess.SessionID, err)
			continue
		}
		if sess.Slots == nil {
			sess.Slots = map[string]string{}
		}
		t, err := time.Parse(time.RFC3339Nano, updated)
		if err != nil {
			log.Printf("[agent] 会话 %s updated_at 损坏，丢弃：%v", sess.SessionID, err)
			continue
		}
		sess.Updated = t
		s.data[sess.SessionID] = &sess
	}
	return rows.Err()
}

// evictLocked 清理过期会话；需持有 mu。
func (s *sqliteSessionStore) evictLocked() {
	now := s.clock()
	for sid, sess := range s.data {
		if now.Sub(sess.Updated) > ttl {
			delete(s.data, sid)
		}
	}
}

// Get 取会话（过期返回 nil）。
func (s *sqliteSessionStore) Get(sessionID string) *TxSession {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.evictLocked()
	return s.data[sessionID]
}

// Ensure 取或建会话，并刷新 role/user/时间戳。
func (s *sqliteSessionStore) Ensure(sessionID, role, user string) *TxSession {
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

// Clear 移除会话并同步删库（办理完成/取消/切话题后不复活）。
func (s *sqliteSessionStore) Clear(sessionID string) {
	s.mu.Lock()
	delete(s.data, sessionID)
	s.mu.Unlock()
	if _, err := s.db.Exec(`DELETE FROM tx_sessions WHERE session_id = ?`, sessionID); err != nil {
		log.Printf("[agent] 会话 %s 删除落库失败（内存已移除）：%v", sessionID, err)
	}
}

// Sync 全量写透：幂等 UPSERT 当前全部会话。
// 语句为静态字面量，全部数据经 ? 占位符参数化传入。
func (s *sqliteSessionStore) Sync() error {
	s.mu.Lock()
	sessList := make([]*TxSession, 0, len(s.data))
	for _, sess := range s.data {
		sessList = append(sessList, sess)
	}
	s.mu.Unlock()

	for _, sess := range sessList {
		if err := s.upsert(sess); err != nil {
			return err
		}
	}
	return nil
}

// upsert 单条写透（slots 序列化为 JSON，updated_at 为 RFC3339Nano 文本）。
func (s *sqliteSessionStore) upsert(sess *TxSession) error {
	slots, err := json.Marshal(sess.Slots)
	if err != nil {
		return fmt.Errorf("会话 %s slots 序列化失败: %w", sess.SessionID, err)
	}
	_, err = s.db.Exec(`INSERT INTO tx_sessions
		(session_id, role, user, phase, tool, slots, last_asked, updated_at)
		VALUES (?, ?, ?, ?, ?, ?, ?, ?)
		ON CONFLICT(session_id) DO UPDATE SET
			role = excluded.role, user = excluded.user, phase = excluded.phase,
			tool = excluded.tool, slots = excluded.slots,
			last_asked = excluded.last_asked, updated_at = excluded.updated_at`,
		sess.SessionID, sess.Role, sess.User, sess.Phase, sess.Tool,
		string(slots), sess.LastAsked, sess.Updated.Format(time.RFC3339Nano))
	if err != nil {
		return fmt.Errorf("会话 %s 落库失败: %w", sess.SessionID, err)
	}
	return nil
}

// Close 关闭底层连接。
func (s *sqliteSessionStore) Close() error {
	if s == nil || s.db == nil {
		return nil
	}
	return s.db.Close()
}
