package rag

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"fmt"
	"sort"
	"time"

	"go.uber.org/zap"
)

// 长期记忆（P5）：独立表（与知识 chunk 分离——collection 隔离语义），
// 共用 embedding 管线（经 generate）。**不接入答案生成**（ADR-0005）。
//
// Recall 语义：
// - 库中有向量且有 key → 语义检索（会话内向量余弦，(score, time) 稳定序）；
// - 无向量/无 key → 降级为该会话最近写入的 k 条（BM25 对短记忆无意义，
//   以 recency 近似——API 契约保证可用，语义强弱按索引状态如实降级）。

const memoryMaxLen = 2000

// openMemoryStore 建 memory 表。
func (p *pgStore) openMemory() error {
	if _, err := p.db.Exec(`CREATE TABLE IF NOT EXISTS kb_memory (
		id         TEXT PRIMARY KEY,
		session_id TEXT NOT NULL,
		text       TEXT NOT NULL,
		created_at TIMESTAMPTZ NOT NULL DEFAULT now()
	)`); err != nil {
		return fmt.Errorf("建 kb_memory 表失败: %w", err)
	}
	if _, err := p.db.Exec(`CREATE TABLE IF NOT EXISTS kb_memory_vectors (
		memory_id TEXT PRIMARY KEY,
		dim       INTEGER NOT NULL,
		data      BYTEA NOT NULL
	)`); err != nil {
		return fmt.Errorf("建 kb_memory_vectors 表失败: %w", err)
	}
	return nil
}

func newMemoryID() string {
	var b [8]byte
	if _, err := rand.Read(b[:]); err != nil {
		return fmt.Sprintf("m-%d", time.Now().UnixNano())
	}
	return "m-" + hex.EncodeToString(b[:])
}

type memoryRow struct {
	ID        string
	SessionID string
	Text      string
	CreatedAt time.Time
}

// memoryPut 写入记忆 + 向量（embed 失败时仅存文本——降级语义，如实可查）。
func (s *Server) memoryPut(ctx context.Context, sessionID, text string) (string, error) {
	id := newMemoryID()
	if _, err := s.store.db.ExecContext(ctx,
		`INSERT INTO kb_memory (id, session_id, text) VALUES ($1, $2, $3)`, id, sessionID, text); err != nil {
		return "", err
	}
	// embedding 尽力而为：失败仅记录（Recall 将走 recency 降级）
	s.gen.refreshKey(ctx)
	if s.gen.HasKey() {
		if vecs, err := s.gen.Embed(ctx, []string{text}); err == nil && len(vecs) == 1 {
			blob, dim := packVec(vecs[0])
			if _, err := s.store.db.ExecContext(ctx,
				`INSERT INTO kb_memory_vectors (memory_id, dim, data) VALUES ($1, $2, $3)
				 ON CONFLICT (memory_id) DO UPDATE SET dim = $2, data = $3`, id, dim, blob); err != nil {
				s.log.Warn("记忆向量写入失败", zap.Error(err))
			}
		} else if err != nil {
			s.log.Warn("记忆向量化失败（仅存文本）", zap.Error(err))
		}
	}
	return id, nil
}

// memoryRecall 检索记忆（向量可用走余弦，否则 recency 降级）。
func (s *Server) memoryRecall(ctx context.Context, sessionID, query string, k int) ([]*memoryItemOut, error) {
	rows, err := s.store.db.QueryContext(ctx,
		`SELECT id, session_id, text, created_at FROM kb_memory WHERE session_id = $1`, sessionID)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var mems []memoryRow
	for rows.Next() {
		var m memoryRow
		if err := rows.Scan(&m.ID, &m.SessionID, &m.Text, &m.CreatedAt); err != nil {
			return nil, err
		}
		mems = append(mems, m)
	}
	if err := rows.Err(); err != nil {
		return nil, err
	}
	if len(mems) == 0 {
		return nil, nil
	}

	// 向量路：会话内所有记忆有向量且查询 embed 成功 → 余弦
	type scored struct {
		m        memoryRow
		has      bool
		scoreVec []float32
	}
	all := make([]scored, 0, len(mems))
	vecCount := 0
	for _, m := range mems {
		sc := scored{m: m}
		var dim int
		var blob []byte
		err := s.store.db.QueryRowContext(ctx,
			`SELECT dim, data FROM kb_memory_vectors WHERE memory_id = $1`, m.ID).Scan(&dim, &blob)
		if err == nil {
			if v, ok := unpackVec(blob, dim); ok {
				sc.has = true
				sc.scoreVec = v
				vecCount++
			}
		}
		all = append(all, sc)
	}

	s.gen.refreshKey(ctx)
	if vecCount == len(all) && vecCount > 0 && s.gen.HasKey() {
		if qv, err := s.gen.Embed(ctx, []string{query}); err == nil && len(qv) == 1 {
			out := make([]*memoryItemOut, 0, len(all))
			for _, sc := range all {
				dot := 0.0
				for i, x := range sc.scoreVec {
					dot += float64(x) * qv[0][i] // 双侧已 L2 归一化 → 内积=余弦
				}
				out = append(out, &memoryItemOut{ID: sc.m.ID, Text: sc.m.Text, Score: dot, At: sc.m.CreatedAt})
			}
			// 稳定序：score 降序，平局按时间新→旧
			sort.SliceStable(out, func(i, j int) bool {
				if out[i].Score != out[j].Score {
					return out[i].Score > out[j].Score
				}
				return out[i].At.After(out[j].At)
			})
			if len(out) > k {
				out = out[:k]
			}
			return out, nil
		}
	}

	// recency 降级：最近写入的 k 条
	sort.SliceStable(mems, func(i, j int) bool { return mems[i].CreatedAt.After(mems[j].CreatedAt) })
	if len(mems) > k {
		mems = mems[:k]
	}
	out := make([]*memoryItemOut, 0, len(mems))
	for _, m := range mems {
		out = append(out, &memoryItemOut{ID: m.ID, Text: m.Text, Score: 0, At: m.CreatedAt})
	}
	return out, nil
}

type memoryItemOut struct {
	ID    string
	Text  string
	Score float64
	At    time.Time
}
