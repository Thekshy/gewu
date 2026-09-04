package conversation

import (
	"context"
	"database/sql"
	"encoding/json"
	"fmt"
	"time"

	_ "github.com/jackc/pgx/v5/stdlib"
)

// PGStore PG 会话存储：服务重启后会话保留（有意差异，见 PARITY-MS）。
// TTL 判定基于 updated_at 时间戳（get/ensure 时惰性清理，语义与内存实现一致）。
type PGStore struct {
	db *sql.DB
}

// OpenPG 打开（或创建）会话库并建表。
func OpenPG(dsn string) (*PGStore, error) {
	db, err := sql.Open("pgx", dsn)
	if err != nil {
		return nil, err
	}
	if err := db.Ping(); err != nil {
		db.Close()
		return nil, fmt.Errorf("连接 PG 失败: %w", err)
	}
	if _, err := db.Exec(`CREATE TABLE IF NOT EXISTS tx_sessions (
		session_id TEXT PRIMARY KEY,
		role       TEXT NOT NULL,
		user_name  TEXT NOT NULL,
		phase      TEXT NOT NULL DEFAULT 'idle',
		tool       TEXT NOT NULL DEFAULT '',
		slots      JSONB  NOT NULL DEFAULT '{}',
		last_asked TEXT NOT NULL DEFAULT '',
		updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
	)`); err != nil {
		db.Close()
		return nil, fmt.Errorf("建 tx_sessions 表失败: %w", err)
	}
	if _, err := db.Exec(`CREATE TABLE IF NOT EXISTS tx_messages (
		id         BIGSERIAL PRIMARY KEY,
		session_id TEXT NOT NULL,
		role       TEXT NOT NULL,
		content    TEXT NOT NULL,
		created_at TIMESTAMPTZ NOT NULL DEFAULT now()
	)`); err != nil {
		db.Close()
		return nil, fmt.Errorf("建 tx_messages 表失败: %w", err)
	}
	return &PGStore{db: db}, nil
}

// Close 关闭底层连接。
func (p *PGStore) Close() error { return p.db.Close() }

const getSessionSQL = `SELECT session_id, role, user_name, phase, tool, slots, last_asked, updated_at
FROM tx_sessions WHERE session_id = $1 AND updated_at > now() - interval '30 minutes'`

func (p *PGStore) get(ctx context.Context, sessionID string) (*Session, error) {
	var s Session
	var slots []byte
	var updated time.Time
	err := p.db.QueryRowContext(ctx, getSessionSQL, sessionID).
		Scan(&s.SessionID, &s.Role, &s.User, &s.Phase, &s.Tool, &slots, &s.LastAsked, &updated)
	if err == sql.ErrNoRows {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	s.Slots = map[string]string{}
	if len(slots) > 0 {
		_ = json.Unmarshal(slots, &s.Slots)
	}
	s.Updated = updated
	return &s, nil
}

// Get 取会话（SQL 条件即 TTL 惰性清理：过期行视同不存在）。
func (p *PGStore) Get(ctx context.Context, sessionID string) (*Session, error) {
	return p.get(ctx, sessionID)
}

// Ensure 取或建会话，并刷新 role/user/时间戳。
// 过期会话先惰性清理再重建（phase 归零 idle）——与冻结单体
// evict→get-or-create 语义逐字对齐，UPSERT 保留旧 phase 是错的。
func (p *PGStore) Ensure(ctx context.Context, sessionID, role, user string) (*Session, error) {
	if _, err := p.db.ExecContext(ctx,
		`DELETE FROM tx_sessions WHERE session_id = $1 AND updated_at <= now() - interval '30 minutes'`, sessionID); err != nil {
		return nil, err
	}
	if _, err := p.db.ExecContext(ctx, `INSERT INTO tx_sessions (session_id, role, user_name) VALUES ($1, $2, $3)
		ON CONFLICT (session_id) DO UPDATE SET role = $2, user_name = $3, updated_at = now()`,
		sessionID, role, user); err != nil {
		return nil, err
	}
	return p.get(ctx, sessionID)
}

// Save 全量保存。
func (p *PGStore) Save(ctx context.Context, s *Session) error {
	slots, err := json.Marshal(s.Slots)
	if err != nil {
		return err
	}
	_, err = p.db.ExecContext(ctx, `INSERT INTO tx_sessions
		(session_id, role, user_name, phase, tool, slots, last_asked, updated_at)
		VALUES ($1, $2, $3, $4, $5, $6, $7, now())
		ON CONFLICT (session_id) DO UPDATE SET
			role = $2, user_name = $3, phase = $4, tool = $5, slots = $6, last_asked = $7, updated_at = now()`,
		s.SessionID, s.Role, s.User, s.Phase, s.Tool, slots, s.LastAsked)
	return err
}

// Clear 移除会话。
func (p *PGStore) Clear(ctx context.Context, sessionID string) error {
	_, err := p.db.ExecContext(ctx, `DELETE FROM tx_sessions WHERE session_id = $1`, sessionID)
	return err
}

// AppendMessage 审计消息落库。
func (p *PGStore) AppendMessage(ctx context.Context, m Message) error {
	_, err := p.db.ExecContext(ctx,
		`INSERT INTO tx_messages (session_id, role, content) VALUES ($1, $2, $3)`,
		m.SessionID, m.Role, m.Content)
	return err
}
