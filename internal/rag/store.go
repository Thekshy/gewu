package rag

import (
	"database/sql"
	"encoding/binary"
	"math"
	"sort"
	"sync"

	_ "modernc.org/sqlite" // 纯 Go SQLite 驱动，免 CGO
)

// Hit 一条检索命中：chunk 及其所属文档元信息。
type Hit struct {
	ChunkID int
	DocID   string
	Seq     int
	Text    string
	Title   string
	Source  string
}

// Scored 带分数的 chunk id（BM25 / 向量两路召回的中间形态）。
type Scored struct {
	ID    int
	Score float64
}

// DocInfo /api/docs 列表项。
type DocInfo struct {
	DocID   string `json:"doc_id"`
	Title   string `json:"title"`
	Source  string `json:"source"`
	Updated string `json:"updated"`
	Chunks  int    `json:"chunks"`
}

// DocMeta 文档元信息。
type DocMeta struct {
	Title   string
	Source  string
	Updated string
}

// ChunkRow chunk 基本行。
type ChunkRow struct {
	DocID string
	Seq   int
	Text  string
}

// Stats 索引规模统计（/api/health）。
type Stats struct {
	Docs     int
	Chunks   int
	Embedded bool
}

// bm25Index 内存倒排索引（首次查询时构建，写入后失效）。
type bm25Index struct {
	postings map[string]map[int]int // token -> chunk -> tf
	docLen   map[int]int
	idf      map[string]float64
	avgdl    float64
}

// Store SQLite 知识库存储：BM25 + 向量余弦，供上层做 RRF 混合检索。
// 单写连接串行化（与 Python 单连接语义一致），BM25/向量缓存用 RWMutex 保护。
type Store struct {
	db  *sql.DB
	mu  sync.RWMutex
	bm  *bm25Index
	vec map[int][]float32 // L2 归一化缓存
}

// Open 打开（或创建）索引库并建表。
func Open(path string) (*Store, error) {
	db, err := sql.Open("sqlite", path)
	if err != nil {
		return nil, err
	}
	// 单连接：写操作只在 ingest 时发生，串行化规避 SQLITE_BUSY
	db.SetMaxOpenConns(1)
	if err := execSchema(db); err != nil {
		db.Close()
		return nil, err
	}
	return &Store{db: db}, nil
}

// Close 关闭底层连接。
func (s *Store) Close() error { return s.db.Close() }

// UpsertDoc 幂等入库：同一 doc_id 重复导入时先清旧 chunk 与向量。
// vectors 为 nil 表示仅 BM25；否则按 chunk 顺序一一对应。
func (s *Store) UpsertDoc(docID, title, source, updated string, chunkTexts []string, vectors [][]float64) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	tx, err := s.db.Begin()
	if err != nil {
		return err
	}
	defer func() { _ = tx.Rollback() }()

	rows, err := tx.Query("SELECT id FROM chunks WHERE doc_id = ?", docID)
	if err != nil {
		return err
	}
	var oldIDs []int64
	for rows.Next() {
		var id int64
		if err := rows.Scan(&id); err != nil {
			rows.Close()
			return err
		}
		oldIDs = append(oldIDs, id)
	}
	rows.Close()
	if err := rows.Err(); err != nil {
		return err
	}
	for _, id := range oldIDs {
		if _, err := tx.Exec("DELETE FROM vectors WHERE chunk_id = ?", id); err != nil {
			return err
		}
	}
	if _, err := tx.Exec("DELETE FROM chunks WHERE doc_id = ?", docID); err != nil {
		return err
	}
	if _, err := tx.Exec(
		"INSERT OR REPLACE INTO docs (id, title, source, updated) VALUES (?, ?, ?, ?)",
		docID, title, source, updated,
	); err != nil {
		return err
	}
	for seq, text := range chunkTexts {
		res, err := tx.Exec("INSERT INTO chunks (doc_id, seq, text) VALUES (?, ?, ?)", docID, seq, text)
		if err != nil {
			return err
		}
		if vectors != nil && seq < len(vectors) {
			cid, err := res.LastInsertId()
			if err != nil {
				return err
			}
			blob, dim := packVector(vectors[seq])
			if _, err := tx.Exec("INSERT INTO vectors (chunk_id, dim, data) VALUES (?, ?, ?)", cid, dim, blob); err != nil {
				return err
			}
		}
	}
	if err := tx.Commit(); err != nil {
		return err
	}
	s.bm = nil
	s.vec = nil
	return nil
}

// packVector float64 列表 → (float32 小端字节, 维度)，与 numpy tobytes 兼容。
func packVector(v []float64) ([]byte, int) {
	buf := make([]byte, 4*len(v))
	for i, x := range v {
		binary.LittleEndian.PutUint32(buf[i*4:], math.Float32bits(float32(x)))
	}
	return buf, len(v)
}

func unpackVector(blob []byte, dim int) ([]float32, bool) {
	if len(blob) != dim*4 {
		return nil, false
	}
	out := make([]float32, dim)
	for i := range out {
		out[i] = math.Float32frombits(binary.LittleEndian.Uint32(blob[i*4:]))
	}
	return out, true
}

// ---------- BM25 ----------

// ensureBM25 懒构建倒排索引；需持有 mu（读或写）。并发首查可能重复构建一次，
// 结果幂等，最终一致。
func (s *Store) ensureBM25() error {
	if s.bm != nil {
		return nil
	}
	idx := &bm25Index{
		postings: map[string]map[int]int{},
		docLen:   map[int]int{},
		idf:      map[string]float64{},
	}
	rows, err := s.db.Query("SELECT id, text FROM chunks")
	if err != nil {
		return err
	}
	defer rows.Close()
	var totalLen float64
	for rows.Next() {
		var cid int
		var text string
		if err := rows.Scan(&cid, &text); err != nil {
			return err
		}
		tokens := Tokenize(text)
		if n := len(tokens); n < 1 {
			idx.docLen[cid] = 1
		} else {
			idx.docLen[cid] = n
		}
		totalLen += float64(idx.docLen[cid])
		counts := map[string]int{}
		for _, t := range tokens {
			counts[t]++
		}
		for t, tf := range counts {
			if idx.postings[t] == nil {
				idx.postings[t] = map[int]int{}
			}
			idx.postings[t][cid] = tf
		}
	}
	if err := rows.Err(); err != nil {
		return err
	}
	n := len(idx.docLen)
	if n == 0 {
		n = 1
	}
	idx.avgdl = totalLen / float64(n)
	for t, plist := range idx.postings {
		idx.idf[t] = math.Log(1 + (float64(n)-float64(len(plist))+0.5)/(float64(len(plist))+0.5))
	}
	s.bm = idx
	return nil
}

// BM25Search 返回按 BM25 得分降序的前 k 个 (chunkID, score)；k1=1.5, b=0.75。
func (s *Store) BM25Search(query string, k int) ([]Scored, error) {
	s.mu.RLock()
	err := s.ensureBM25()
	idx := s.bm
	s.mu.RUnlock()
	if err != nil || idx == nil {
		return nil, err
	}
	scores := map[int]float64{}
	const k1, b = 1.5, 0.75
	for _, token := range dedupKeepOrder(Tokenize(query)) {
		plist, ok := idx.postings[token]
		if !ok {
			continue
		}
		idf := idx.idf[token]
		for cid, tf := range plist {
			denom := float64(tf) + k1*(1-b+b*float64(idx.docLen[cid])/idx.avgdl)
			scores[cid] += idf * float64(tf) * (k1 + 1) / denom
		}
	}

	out := make([]Scored, 0, len(scores))
	for cid, sc := range scores {
		out = append(out, Scored{ID: cid, Score: sc})
	}
	sort.Slice(out, func(i, j int) bool {
		if out[i].Score != out[j].Score {
			return out[i].Score > out[j].Score
		}
		return out[i].ID < out[j].ID // 平分按 chunk id 升序（Go 版确定化，见 go-notes）
	})
	if len(out) > k {
		out = out[:k]
	}
	return out, nil
}

// ---------- 向量 ----------

func (s *Store) ensureVectors() error {
	if s.vec != nil {
		return nil
	}
	cache := map[int][]float32{}
	rows, err := s.db.Query("SELECT chunk_id, dim, data FROM vectors")
	if err != nil {
		return err
	}
	defer rows.Close()
	for rows.Next() {
		var cid, dim int
		var blob []byte
		if err := rows.Scan(&cid, &dim, &blob); err != nil {
			return err
		}
		v, ok := unpackVector(blob, dim)
		if !ok {
			continue
		}
		norm := l2norm32(v)
		if norm > 0 {
			for i := range v {
				v[i] /= norm
			}
		}
		cache[cid] = v
	}
	if err := rows.Err(); err != nil {
		return err
	}
	s.vec = cache
	return nil
}

func l2norm32(v []float32) float32 {
	var sum float64
	for _, x := range v {
		sum += float64(x) * float64(x)
	}
	return float32(math.Sqrt(sum))
}

// HasEmbeddings 库中是否存在向量。
func (s *Store) HasEmbeddings() (bool, error) {
	var n int
	if err := s.db.QueryRow("SELECT COUNT(*) FROM vectors").Scan(&n); err != nil {
		return false, err
	}
	return n > 0, nil
}

// VectorSearch 暴力余弦：千级 chunk 规模下延迟毫秒级。查询向量先归一化。
func (s *Store) VectorSearch(queryVec []float64, k int) ([]Scored, error) {
	s.mu.RLock()
	defer s.mu.RUnlock()
	if err := s.ensureVectors(); err != nil {
		return nil, err
	}
	if len(s.vec) == 0 || len(queryVec) == 0 {
		return nil, nil
	}
	q := make([]float32, len(queryVec))
	var norm float64
	for i, x := range queryVec {
		q[i] = float32(x)
		norm += x * x
	}
	if norm > 0 {
		inv := float32(1 / math.Sqrt(norm))
		for i := range q {
			q[i] *= inv
		}
	}
	out := make([]Scored, 0, len(s.vec))
	for cid, v := range s.vec {
		if len(v) != len(q) {
			continue // 维度不一致（换了嵌入模型）跳过
		}
		var dot float64
		for i := range v {
			dot += float64(v[i]) * float64(q[i])
		}
		out = append(out, Scored{ID: cid, Score: dot})
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

// ---------- 读取 ----------

// ChunkRows 批量取 chunk 行。
func (s *Store) ChunkRows(ids []int) (map[int]ChunkRow, error) {
	out := make(map[int]ChunkRow, len(ids))
	for _, cid := range ids {
		var r ChunkRow
		var id int
		err := s.db.QueryRow("SELECT id, doc_id, seq, text FROM chunks WHERE id = ?", cid).
			Scan(&id, &r.DocID, &r.Seq, &r.Text)
		if err == nil {
			out[id] = r
		} else if err != sql.ErrNoRows {
			return nil, err
		}
	}
	return out, nil
}

// DocMetaMap 批量取文档元信息。
func (s *Store) DocMetaMap(docIDs []string) (map[string]DocMeta, error) {
	out := make(map[string]DocMeta, len(docIDs))
	for _, docID := range docIDs {
		var m DocMeta
		var id string
		err := s.db.QueryRow("SELECT id, title, source, updated FROM docs WHERE id = ?", docID).
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
func (s *Store) ListDocs() ([]DocInfo, error) {
	rows, err := s.db.Query("SELECT id, title, source, updated FROM docs ORDER BY id")
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var out []DocInfo
	for rows.Next() {
		var d DocInfo
		if err := rows.Scan(&d.DocID, &d.Title, &d.Source, &d.Updated); err != nil {
			return nil, err
		}
		out = append(out, d)
	}
	if err := rows.Err(); err != nil {
		return nil, err
	}
	for i := range out {
		if err := s.db.QueryRow("SELECT COUNT(*) FROM chunks WHERE doc_id = ?", out[i].DocID).
			Scan(&out[i].Chunks); err != nil {
			return nil, err
		}
	}
	return out, nil
}

// GetStats 索引规模。
func (s *Store) GetStats() (Stats, error) {
	var st Stats
	if err := s.db.QueryRow("SELECT COUNT(*) FROM docs").Scan(&st.Docs); err != nil {
		return st, err
	}
	if err := s.db.QueryRow("SELECT COUNT(*) FROM chunks").Scan(&st.Chunks); err != nil {
		return st, err
	}
	emb, err := s.HasEmbeddings()
	st.Embedded = emb
	return st, err
}

// RRFFuse Reciprocal Rank Fusion：多路召回的排名融合，k=60。
// 平分时按首次出现顺序（与 Python dict 插入序 + 稳定排序一致）。
func RRFFuse(rankLists [][]int, k int) []int {
	type acc struct {
		score     float64
		firstSeen int
	}
	order := 0
	scores := map[int]*acc{}
	for _, lst := range rankLists {
		for rank, cid := range lst {
			a, ok := scores[cid]
			if !ok {
				a = &acc{firstSeen: order}
				order++
				scores[cid] = a
			}
			a.score += 1.0 / float64(k+rank+1)
		}
	}
	out := make([]int, 0, len(scores))
	for cid := range scores {
		out = append(out, cid)
	}
	sort.SliceStable(out, func(i, j int) bool {
		a, b := scores[out[i]], scores[out[j]]
		if a.score != b.score {
			return a.score > b.score
		}
		return a.firstSeen < b.firstSeen
	})
	return out
}
