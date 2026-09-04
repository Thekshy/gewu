package rag

import (
	"context"
	"database/sql"
	"encoding/binary"
	"fmt"
	"math"
	"sort"
	"sync"

	_ "github.com/jackc/pgx/v5/stdlib"

	"gewu/internal/rag"
)

// pgVectorStore PG 降级实现：向量以 float32 小端 BLOB 存储，检索在服务进程内
// 做暴力余弦——与冻结单体的 SQLite vectors 表行为同构（ADR：资源受限时的
// pgvector 角色实现，避免 milvus 依赖；数学上与 FLAT IP 等价）。
type pgVectorStore struct {
	db *sql.DB

	mu      sync.RWMutex
	cache   map[int][]float32 // L2 归一化缓存（写后失效重建）
	invalid bool
}

func openPGVectorStore(dsn string) (*pgVectorStore, error) {
	db, err := sql.Open("pgx", dsn)
	if err != nil {
		return nil, err
	}
	if err := db.Ping(); err != nil {
		db.Close()
		return nil, fmt.Errorf("连接 PG 失败: %w", err)
	}
	if _, err := db.Exec(`CREATE TABLE IF NOT EXISTS kb_vectors (
		chunk_id BIGINT PRIMARY KEY,
		dim      INTEGER NOT NULL,
		data     BYTEA NOT NULL
	)`); err != nil {
		db.Close()
		return nil, fmt.Errorf("建 kb_vectors 表失败: %w", err)
	}
	return &pgVectorStore{db: db}, nil
}

// Name 实现名。
func (p *pgVectorStore) Name() string { return "pg-cosine" }

// Upsert 写入（覆盖同 id）。
func (p *pgVectorStore) Upsert(ctx context.Context, ids []int64, vectors [][]float64) error {
	for i, id := range ids {
		if i >= len(vectors) {
			break
		}
		blob, dim := packVec(vectors[i])
		if _, err := p.db.ExecContext(ctx, `INSERT INTO kb_vectors (chunk_id, dim, data)
			VALUES ($1, $2, $3) ON CONFLICT (chunk_id) DO UPDATE SET dim = $2, data = $3`,
			id, dim, blob); err != nil {
			return err
		}
	}
	p.mu.Lock()
	p.cache = nil
	p.mu.Unlock()
	return nil
}

// vectors 懒加载归一化缓存。
func (p *pgVectorStore) vectors() (map[int][]float32, error) {
	p.mu.RLock()
	if p.cache != nil {
		c := p.cache
		p.mu.RUnlock()
		return c, nil
	}
	p.mu.RUnlock()

	p.mu.Lock()
	defer p.mu.Unlock()
	if p.cache != nil {
		return p.cache, nil
	}
	cache := map[int][]float32{}
	rows, err := p.db.Query(`SELECT chunk_id, dim, data FROM kb_vectors`)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	for rows.Next() {
		var cid int64
		var dim int
		var blob []byte
		if err := rows.Scan(&cid, &dim, &blob); err != nil {
			return nil, err
		}
		v, ok := unpackVec(blob, dim)
		if !ok {
			continue
		}
		norm := l2norm(v)
		if norm > 0 {
			for i := range v {
				v[i] /= norm
			}
		}
		cache[int(cid)] = v
	}
	if err := rows.Err(); err != nil {
		return nil, err
	}
	p.cache = cache
	return cache, nil
}

// Search 暴力余弦（与冻结单体 VectorSearch 同构：查询向量归一化后点积，
// 维度不一致跳过，平局按 chunk id 升序）。
func (p *pgVectorStore) Search(ctx context.Context, query []float64, k int) ([]rag.Scored, error) {
	vecs, err := p.vectors()
	if err != nil {
		return nil, err
	}
	if len(vecs) == 0 || len(query) == 0 {
		return nil, nil
	}
	q := make([]float32, len(query))
	var norm float64
	for i, x := range query {
		q[i] = float32(x)
		norm += x * x
	}
	if norm > 0 {
		inv := float32(1 / math.Sqrt(norm))
		for i := range q {
			q[i] *= inv
		}
	}
	out := make([]rag.Scored, 0, len(vecs))
	for cid, v := range vecs {
		if len(v) != len(q) {
			continue // 维度不一致（换了嵌入模型）跳过
		}
		var dot float64
		for i := range v {
			dot += float64(v[i]) * float64(q[i])
		}
		out = append(out, rag.Scored{ID: cid, Score: dot})
	}
	sort.Slice(out, func(i, j int) bool {
		if out[i].Score != out[j].Score {
			return out[i].Score > out[j].Score
		}
		return out[i].ID < out[j].ID
	})
	if len(out) > k {
		out = out[:k]
	}
	return out, nil
}

// Has 是否存在向量。
func (p *pgVectorStore) Has(ctx context.Context) (bool, error) {
	var n int
	if err := p.db.QueryRowContext(ctx, `SELECT COUNT(*) FROM kb_vectors`).Scan(&n); err != nil {
		return false, err
	}
	return n > 0, nil
}

// DeleteChunks 删除指定向量。
func (p *pgVectorStore) DeleteChunks(ctx context.Context, ids []int64) error {
	for _, id := range ids {
		if _, err := p.db.ExecContext(ctx, `DELETE FROM kb_vectors WHERE chunk_id = $1`, id); err != nil {
			return err
		}
	}
	p.mu.Lock()
	p.cache = nil
	p.mu.Unlock()
	return nil
}

// Drop 清空。
func (p *pgVectorStore) Drop(ctx context.Context) error {
	if _, err := p.db.ExecContext(ctx, `DELETE FROM kb_vectors`); err != nil {
		return err
	}
	p.mu.Lock()
	p.cache = nil
	p.mu.Unlock()
	return nil
}

// packVec float64 列表 → (float32 小端字节, 维度)——与冻结单体 packVector
// 的字节格式一致（numpy tobytes 兼容）。
func packVec(v []float64) ([]byte, int) {
	buf := make([]byte, 4*len(v))
	for i, x := range v {
		binary.LittleEndian.PutUint32(buf[i*4:], math.Float32bits(float32(x)))
	}
	return buf, len(v)
}

func unpackVec(blob []byte, dim int) ([]float32, bool) {
	if len(blob) != dim*4 {
		return nil, false
	}
	out := make([]float32, dim)
	for i := range out {
		out[i] = math.Float32frombits(binary.LittleEndian.Uint32(blob[i*4:]))
	}
	return out, true
}

func l2norm(v []float32) float32 {
	var sum float64
	for _, x := range v {
		sum += float64(x) * float64(x)
	}
	return float32(math.Sqrt(sum))
}
