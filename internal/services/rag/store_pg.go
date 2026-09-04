package rag

import (
	"context"
	"database/sql"
	"fmt"
	"sync"

	_ "github.com/jackc/pgx/v5/stdlib"

	"gewu/internal/rag"
)

// pgStore chunk 元数据存储（PG）：语义对齐冻结 internal/rag 的 SQLite Store
// （UpsertDoc 幂等：同 doc_id 先删旧 chunk 与向量再插；ListDocs 按 doc_id 升序）。
// BM25 倒排不落库——进程内 BM25Index（同一份公式代码）懒构建、写后失效。
type pgStore struct {
	db *sql.DB

	mu sync.RWMutex
	bm *rag.BM25Index // 懒构建缓存（写入后置 nil 重建）
}

func openPGStore(dsn string) (*pgStore, error) {
	db, err := sql.Open("pgx", dsn)
	if err != nil {
		return nil, err
	}
	if err := db.Ping(); err != nil {
		db.Close()
		return nil, fmt.Errorf("连接 PG 失败: %w", err)
	}
	// 建表（静态字面量 DDL，无任何外部输入）
	if _, err := db.Exec(`CREATE TABLE IF NOT EXISTS kb_docs (
		id      TEXT PRIMARY KEY,
		title   TEXT NOT NULL,
		source  TEXT NOT NULL,
		updated TEXT NOT NULL DEFAULT ''
	)`); err != nil {
		db.Close()
		return nil, fmt.Errorf("建 kb_docs 表失败: %w", err)
	}
	if _, err := db.Exec(`CREATE TABLE IF NOT EXISTS kb_chunks (
		id     BIGSERIAL PRIMARY KEY,
		doc_id TEXT NOT NULL,
		seq    INTEGER NOT NULL,
		text   TEXT NOT NULL
	)`); err != nil {
		db.Close()
		return nil, fmt.Errorf("建 kb_chunks 表失败: %w", err)
	}
	if _, err := db.Exec(`CREATE INDEX IF NOT EXISTS idx_kb_chunks_doc ON kb_chunks (doc_id)`); err != nil {
		db.Close()
		return nil, fmt.Errorf("建 kb_chunks 索引失败: %w", err)
	}
	return &pgStore{db: db}, nil
}

// Close 关闭连接。
func (p *pgStore) Close() error { return p.db.Close() }

// UpsertDoc 幂等入库（vectors 由 VectorStore 单独写；此处只管元数据与 chunk）。
// 返回值第一位为旧 chunk id（向量侧据此清理），第二位为新 chunk id。
func (p *pgStore) UpsertDoc(ctx context.Context, docID, title, source, updated string, chunkTexts []string) (oldIDs, newIDs []int64, err error) {
	tx, err := p.db.BeginTx(ctx, nil)
	if err != nil {
		return nil, nil, err
	}
	defer func() { _ = tx.Rollback() }()

	rows, err := tx.QueryContext(ctx, `SELECT id FROM kb_chunks WHERE doc_id = $1`, docID)
	if err != nil {
		return nil, nil, err
	}
	for rows.Next() {
		var id int64
		if err := rows.Scan(&id); err != nil {
			rows.Close()
			return nil, nil, err
		}
		oldIDs = append(oldIDs, id)
	}
	rows.Close()

	if _, err := tx.ExecContext(ctx, `DELETE FROM kb_chunks WHERE doc_id = $1`, docID); err != nil {
		return nil, nil, err
	}
	if _, err := tx.ExecContext(ctx, `INSERT INTO kb_docs (id, title, source, updated)
		VALUES ($1, $2, $3, $4) ON CONFLICT (id) DO UPDATE SET title = $2, source = $3, updated = $4`,
		docID, title, source, updated); err != nil {
		return nil, nil, err
	}
	for seq, text := range chunkTexts {
		var id int64
		if err := tx.QueryRowContext(ctx,
			`INSERT INTO kb_chunks (doc_id, seq, text) VALUES ($1, $2, $3) RETURNING id`,
			docID, seq, text).Scan(&id); err != nil {
			return nil, nil, err
		}
		newIDs = append(newIDs, id)
	}
	if err := tx.Commit(); err != nil {
		return nil, nil, err
	}
	p.invalidate()
	return oldIDs, newIDs, nil
}

// invalidate BM25 缓存失效（写后重建）。
func (p *pgStore) invalidate() {
	p.mu.Lock()
	p.bm = nil
	p.mu.Unlock()
}

// bm25 懒构建（同一份公式代码）。
func (p *pgStore) bm25() (*rag.BM25Index, error) {
	p.mu.RLock()
	if p.bm != nil {
		idx := p.bm
		p.mu.RUnlock()
		return idx, nil
	}
	p.mu.RUnlock()

	p.mu.Lock()
	defer p.mu.Unlock()
	if p.bm != nil { // 双检：并发首查幂等
		return p.bm, nil
	}
	idx := rag.NewBM25Index()
	rows, err := p.db.Query(`SELECT id, text FROM kb_chunks`)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	for rows.Next() {
		var cid int64
		var text string
		if err := rows.Scan(&cid, &text); err != nil {
			return nil, err
		}
		idx.AddDoc(int(cid), text)
	}
	if err := rows.Err(); err != nil {
		return nil, err
	}
	idx.Finalize()
	p.bm = idx
	return idx, nil
}

// BM25Search BM25 检索（k1/b/idf 公式与冻结单体同一份代码）。
func (p *pgStore) BM25Search(query string, k int) ([]rag.Scored, error) {
	idx, err := p.bm25()
	if err != nil {
		return nil, err
	}
	return idx.Search(query, k), nil
}

// ChunkRows 批量取 chunk 行。
func (p *pgStore) ChunkRows(ctx context.Context, ids []int) (map[int]rag.ChunkRow, error) {
	out := make(map[int]rag.ChunkRow, len(ids))
	for _, cid := range ids {
		var r rag.ChunkRow
		var id int64
		err := p.db.QueryRowContext(ctx, `SELECT id, doc_id, seq, text FROM kb_chunks WHERE id = $1`, cid).
			Scan(&id, &r.DocID, &r.Seq, &r.Text)
		if err == nil {
			out[int(id)] = r
		} else if err != sql.ErrNoRows {
			return nil, err
		}
	}
	return out, nil
}

// DocMetaMap 批量取文档元信息。
func (p *pgStore) DocMetaMap(ctx context.Context, docIDs []string) (map[string]rag.DocMeta, error) {
	out := make(map[string]rag.DocMeta, len(docIDs))
	for _, docID := range docIDs {
		var m rag.DocMeta
		var id string
		err := p.db.QueryRowContext(ctx, `SELECT id, title, source, updated FROM kb_docs WHERE id = $1`, docID).
			Scan(&id, &m.Title, &m.Source, &m.Updated)
		if err == nil {
			out[id] = m
		} else if err != sql.ErrNoRows {
			return nil, err
		}
	}
	return out, nil
}

// ListDocs 全部文档（按 doc_id 升序）。
func (p *pgStore) ListDocs(ctx context.Context) ([]rag.DocInfo, error) {
	rows, err := p.db.QueryContext(ctx, `SELECT id, title, source, updated FROM kb_docs ORDER BY id`)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var out []rag.DocInfo
	for rows.Next() {
		var d rag.DocInfo
		if err := rows.Scan(&d.DocID, &d.Title, &d.Source, &d.Updated); err != nil {
			return nil, err
		}
		out = append(out, d)
	}
	if err := rows.Err(); err != nil {
		return nil, err
	}
	for i := range out {
		if err := p.db.QueryRowContext(ctx,
			`SELECT COUNT(*) FROM kb_chunks WHERE doc_id = $1`, out[i].DocID).Scan(&out[i].Chunks); err != nil {
			return nil, err
		}
	}
	return out, nil
}

// Stats 索引规模。
func (p *pgStore) Stats(ctx context.Context) (rag.Stats, error) {
	var st rag.Stats
	if err := p.db.QueryRowContext(ctx, `SELECT COUNT(*) FROM kb_docs`).Scan(&st.Docs); err != nil {
		return st, err
	}
	if err := p.db.QueryRowContext(ctx, `SELECT COUNT(*) FROM kb_chunks`).Scan(&st.Chunks); err != nil {
		return st, err
	}
	return st, nil
}

// Reset 清空知识库（rebuild 用）。
func (p *pgStore) Reset(ctx context.Context) error {
	if _, err := p.db.ExecContext(ctx, `DELETE FROM kb_chunks`); err != nil {
		return err
	}
	if _, err := p.db.ExecContext(ctx, `DELETE FROM kb_docs`); err != nil {
		return err
	}
	p.invalidate()
	return nil
}
