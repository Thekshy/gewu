package rag

import (
	"database/sql"
	"encoding/binary"
	"fmt"
	"math"
	"sort"
	"strconv"
	"sync"

	_ "modernc.org/sqlite" // 纯 Go SQLite 驱动，免 CGO
)

// Hit 一条检索命中：chunk 及其所属文档元信息。
// hierarchical 模式下 Text 为父块文本（自带 breadcrumb），SectionPath 为命中子块
// 所在 section 的标题路径；flat 模式与旧版一致（SectionPath 为空）。
type Hit struct {
	ChunkID     int
	DocID       string
	Seq         int
	Text        string
	Title       string
	Source      string
	SectionPath string
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

// ChunkRow chunk 基本行（P6 起含父子块字段；flat 模式 ParentID 为空串）。
type ChunkRow struct {
	DocID       string
	Seq         int
	Text        string
	ParentID    string
	SectionPath string
	IsParent    bool
}

// ChunkRecord 入库的单块记录：文本 + 检索属性 + 可选向量。
// ParentIdx 指向同批 records 中父块的下标（父块自身为 -1），由 UpsertDoc
// 换算成父块真实 chunk id 写入 parent_id 列——避免调用方预知自增 id 造成错位。
type ChunkRecord struct {
	Text        string
	SectionPath string
	IsParent    bool
	ParentIdx   int // -1 = 无父（父块自身或 flat 模式）
	Vec         []float64
}

// Stats 索引规模统计（/api/health）。
type Stats struct {
	Docs     int
	Chunks   int
	Embedded bool
}

// bm25IndexCache 等旧字段见 Store；BM25 倒排结构已提取到 BM25Index
// （同一份公式代码，SQLite Store 与微服务 PG 数据源共用）。

// Store SQLite 知识库存储：BM25 + 向量余弦，供上层做 RRF 混合检索。
// 单写连接串行化（与 Python 单连接语义一致），BM25/向量缓存用 RWMutex 保护。
type Store struct {
	db  *sql.DB
	mu  sync.RWMutex
	bm  *BM25Index
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
// records 按序写入；父块（IsParent=true）不写向量；子块 Vec 非 nil 时写向量。
// 子块的 ParentIdx 必须指向本批中先于它出现的父块下标。
func (s *Store) UpsertDoc(docID, title, source, updated string, records []ChunkRecord) error {
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
	// ids[i] = 第 i 条 record 落库后的 chunk id（子块写 parent_id 时回查）。
	ids := make([]int64, len(records))
	for seq, rec := range records {
		var parentID any // nil → SQL NULL（父块自身 / flat 模式）
		if rec.ParentIdx >= 0 {
			if rec.ParentIdx >= len(ids) || ids[rec.ParentIdx] == 0 {
				return fmt.Errorf("chunk %d 的父块下标 %d 非法（父块须先于子块写入）", seq, rec.ParentIdx)
			}
			parentID = strconv.FormatInt(ids[rec.ParentIdx], 10)
		}
		res, err := tx.Exec(
			"INSERT INTO chunks (doc_id, seq, text, parent_id, section_path, is_parent) VALUES (?, ?, ?, ?, ?, ?)",
			docID, seq, rec.Text, parentID, rec.SectionPath, boolToInt(rec.IsParent),
		)
		if err != nil {
			return err
		}
		cid, err := res.LastInsertId()
		if err != nil {
			return err
		}
		ids[seq] = cid
		if rec.IsParent || len(rec.Vec) == 0 {
			continue // 父块只入库不建向量；子块无向量（-no-embed）同样跳过
		}
		blob, dim := packVector(rec.Vec)
		if _, err := tx.Exec("INSERT INTO vectors (chunk_id, dim, data) VALUES (?, ?, ?)", cid, dim, blob); err != nil {
			return err
		}
	}
	if err := tx.Commit(); err != nil {
		return err
	}
	s.bm = nil
	s.vec = nil
	return nil
}

func boolToInt(b bool) int {
	if b {
		return 1
	}
	return 0
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
// 结果幂等，最终一致。只索引子块（is_parent=0）——父块仅供回取，进了倒排
// 会被直接命中、破坏"子块匹配/父块回答"的漏斗。
func (s *Store) ensureBM25() error {
	if s.bm != nil {
		return nil
	}
	idx := NewBM25Index()
	rows, err := s.db.Query("SELECT id, text FROM chunks WHERE is_parent = 0")
	if err != nil {
		return err
	}
	defer rows.Close()
	for rows.Next() {
		var cid int
		var text string
		if err := rows.Scan(&cid, &text); err != nil {
			return err
		}
		idx.AddDoc(cid, text)
	}
	if err := rows.Err(); err != nil {
		return err
	}
	idx.Finalize()
	s.bm = idx
	return nil
}

// BM25Search 返回按 BM25 得分降序的前 k 个 (chunkID, score)（委托 BM25Index）。
func (s *Store) BM25Search(query string, k int) ([]Scored, error) {
	s.mu.RLock()
	err := s.ensureBM25()
	idx := s.bm
	s.mu.RUnlock()
	if err != nil || idx == nil {
		return nil, err
	}
	return idx.Search(query, k), nil
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

// ChunkRows 批量取 chunk 行（含父子块字段）。
func (s *Store) ChunkRows(ids []int) (map[int]ChunkRow, error) {
	out := make(map[int]ChunkRow, len(ids))
	for _, cid := range ids {
		var r ChunkRow
		var id int
		var parentID sql.NullString
		err := s.db.QueryRow("SELECT id, doc_id, seq, text, parent_id, section_path, is_parent FROM chunks WHERE id = ?", cid).
			Scan(&id, &r.DocID, &r.Seq, &r.Text, &parentID, &r.SectionPath, &r.IsParent)
		if err == nil {
			r.ParentID = parentID.String
			out[id] = r
		} else if err != sql.ErrNoRows {
			return nil, err
		}
	}
	return out, nil
}

// ParentRows 按 parent_id（父块 chunk id 的文本形式）批量取父块行。
func (s *Store) ParentRows(parentIDs []string) (map[string]ChunkRow, error) {
	out := make(map[string]ChunkRow, len(parentIDs))
	for _, pid := range parentIDs {
		cid, err := strconv.Atoi(pid)
		if err != nil {
			continue // 非法 id 直接跳过（不产生幽灵命中）
		}
		var r ChunkRow
		var id int
		var parentNull sql.NullString
		err = s.db.QueryRow("SELECT id, doc_id, seq, text, parent_id, section_path, is_parent FROM chunks WHERE id = ?", cid).
			Scan(&id, &r.DocID, &r.Seq, &r.Text, &parentNull, &r.SectionPath, &r.IsParent)
		if err == nil {
			r.ParentID = parentNull.String
			out[pid] = r
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
