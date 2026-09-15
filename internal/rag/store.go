package rag

import (
	"context"
	"encoding/json"
	"fmt"
	"math"
	"net/url"
	"os"
	"sort"
	"strconv"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/jackc/pgx/v5/pgxpool"
	"github.com/pgvector/pgvector-go"

	"gewu/internal/config"
)

// EmbedDim 向量列维度（halfvec(2048)），与火山 doubao-embedding 输出对齐。
// 换嵌入模型（维度不同）须整库重建——维度不符会在入库时明确报错。
const EmbedDim = 2048

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
// ParentIdx 指向同批 records 中父块的下标（父块自身为 -1），由 rag_upsert_doc
// 存储函数换算成父块真实 chunk id 写入 parent_id 列——避免调用方预知自增 id
// 造成错位。json 标签即 rag_upsert_doc 的 JSONB 载荷契约。
type ChunkRecord struct {
	Text        string    `json:"text"`
	SectionPath string    `json:"section_path"`
	IsParent    bool      `json:"is_parent"`
	ParentIdx   int       `json:"parent_idx"` // -1 = 无父（父块自身或 flat 模式）
	Vec         []float64 `json:"vec,omitempty"`
}

// Stats 索引规模统计（/api/health）。
type Stats struct {
	Docs     int
	Chunks   int
	Embedded bool
}

// Store PG 知识库存储：FTS + pgvector，供上层做 RRF 混合检索。
type Store struct {
	pool *pgxpool.Pool
}

// openTimeout 连接/建 schema 的容忍窗口。
const openTimeout = 5 * time.Second

// Open 连接（或初始化 schema）PG 索引库。dsn 形如 PG_DSN（config 缺省指向本地 5433）。
func Open(dsn string) (*Store, error) {
	ctx, cancel := context.WithTimeout(context.Background(), openTimeout)
	defer cancel()
	pool, err := pgxpool.New(ctx, dsn)
	if err != nil {
		return nil, fmt.Errorf("解析 DSN 失败: %w", err)
	}
	if err := pool.Ping(ctx); err != nil {
		pool.Close()
		return nil, fmt.Errorf("连接 PG 失败（先 make pg-up）: %w", err)
	}
	if err := execSchema(ctx, pool); err != nil {
		pool.Close()
		return nil, err
	}
	return &Store{pool: pool}, nil
}

// Close 关闭底层连接池。
func (s *Store) Close() error {
	s.pool.Close()
	return nil
}

// Wipe 清空索引库（-rebuild / 测试隔离用）。DELETE 走行锁 + FK 级联而非
// TRUNCATE（后者要三表 AccessExclusive 锁，并行测试包同时清库会死锁）；
// setval 重置序列让 chunk id 重新从 1 计数（等价 RESTART IDENTITY）。
func (s *Store) Wipe() error {
	ctx := context.Background()
	if _, err := s.pool.Exec(ctx, `DELETE FROM docs`); err != nil {
		return err
	}
	if _, err := s.pool.Exec(ctx, `SELECT setval(pg_get_serial_sequence($1, $2), 1, false)`,
		"chunks", "id"); err != nil {
		return err
	}
	return nil
}

// UpsertDoc 幂等入库：同一 doc_id 重复导入时先清旧 chunk 与向量（FK 级联删除）。
// 载荷 json.Marshal 后整体交 rag_upsert_doc 存储函数执行（幂等替换、父块不建
// 向量、parent_idx 语义见 schema.go）；维度预检给出比 PG 报错更友好的提示。
func (s *Store) UpsertDoc(docID, title, source, updated string, records []ChunkRecord) error {
	for seq, rec := range records {
		if rec.IsParent || len(rec.Vec) == 0 {
			continue
		}
		if len(rec.Vec) != EmbedDim {
			return fmt.Errorf("chunk %d 向量维度 %d 与 halfvec(%d) 不符（换嵌入模型须清库重建）", seq, len(rec.Vec), EmbedDim)
		}
	}
	doc, err := json.Marshal(map[string]string{"id": docID, "title": title, "source": source, "updated": updated})
	if err != nil {
		return err
	}
	payload, err := json.Marshal(records)
	if err != nil {
		return err
	}
	var n int
	return s.pool.QueryRow(context.Background(),
		`SELECT rag_upsert_doc($1, $2)`, doc, payload).Scan(&n)
}

// toFloat32 float64 → float32（pgvector 参数类型）。
func toFloat32(v []float64) []float32 {
	out := make([]float32, len(v))
	for i, x := range v {
		out[i] = float32(x)
	}
	return out
}

// ---------- 关键词检索（PG 原生 FTS，逻辑收口在 rag_fts_search 存储函数） ----------

// BM25Search 关键词检索：查询原文直入 rag_fts_search（服务端 rag_tokenize 分词，
// 任一 token 命中即召回 = OR 语义，长 query 不会被 AND 过滤成空；得分 = 各命中
// token 的 ts_rank_cd 之和）。方法名沿用 BM25 避免调用方漂移；ts_rank 与 BM25
// 公式存在差异（词频饱和/长度归一），迁移对账见 eval/reports/migration-p12-pg.md。
func (s *Store) BM25Search(query string, k int) ([]Scored, error) {
	if query == "" || k <= 0 {
		return nil, nil
	}
	rows, err := s.pool.Query(context.Background(),
		`SELECT id, score FROM rag_fts_search($1, $2, $3)`, "simple", query, k)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var out []Scored
	for rows.Next() {
		var sc Scored
		if err := rows.Scan(&sc.ID, &sc.Score); err != nil {
			return nil, err
		}
		out = append(out, sc)
	}
	return out, rows.Err()
}

// ---------- 向量检索（pgvector halfvec HNSW） ----------

// VectorSearch 向量检索：L2 归一化后 cosine（<=>），HNSW 近似最近邻。
// 平局按 chunk_id 升序保持确定性（与 SQLite 版语义一致）。
func (s *Store) VectorSearch(queryVec []float64, k int) ([]Scored, error) {
	if len(queryVec) == 0 || k <= 0 {
		return nil, nil
	}
	if len(queryVec) != EmbedDim {
		return nil, fmt.Errorf("查询向量维度 %d 与 halfvec(%d) 不符", len(queryVec), EmbedDim)
	}
	q := toFloat32(queryVec)
	var norm float64
	for _, x := range queryVec {
		norm += x * x
	}
	if norm > 0 {
		inv := float32(1 / math.Sqrt(norm))
		for i := range q {
			q[i] *= inv
		}
	}
	rows, err := s.pool.Query(context.Background(), `
		SELECT chunk_id, 1 - (embedding <=> $1) AS score
		FROM vectors
		ORDER BY embedding <=> $1, chunk_id ASC
		LIMIT $2`, pgvector.NewHalfVector(q), k)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var out []Scored
	for rows.Next() {
		var sc Scored
		if err := rows.Scan(&sc.ID, &sc.Score); err != nil {
			return nil, err
		}
		out = append(out, sc)
	}
	return out, rows.Err()
}

// ---------- 读取 ----------

// ChunkRows 批量取 chunk 行（含父子块字段）。
func (s *Store) ChunkRows(ids []int) (map[int]ChunkRow, error) {
	out := make(map[int]ChunkRow, len(ids))
	if len(ids) == 0 {
		return out, nil
	}
	rows, err := s.pool.Query(context.Background(), `
		SELECT id, doc_id, seq, text, COALESCE(parent_id, ''), section_path, is_parent
		FROM chunks WHERE id = ANY($1)`, toInt64s(ids))
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	for rows.Next() {
		var id int
		var r ChunkRow
		if err := rows.Scan(&id, &r.DocID, &r.Seq, &r.Text, &r.ParentID, &r.SectionPath, &r.IsParent); err != nil {
			return nil, err
		}
		out[id] = r
	}
	return out, rows.Err()
}

// ParentRows 按 parent_id（父块 chunk id 的文本形式）批量取父块行。
func (s *Store) ParentRows(parentIDs []string) (map[string]ChunkRow, error) {
	out := make(map[string]ChunkRow, len(parentIDs))
	ids := make([]int64, 0, len(parentIDs))
	for _, pid := range parentIDs {
		if n, err := strconv.Atoi(pid); err == nil {
			ids = append(ids, int64(n))
		} // 非法 id 直接跳过（不产生幽灵命中）
	}
	if len(ids) == 0 {
		return out, nil
	}
	rows, err := s.pool.Query(context.Background(), `
		SELECT id, doc_id, seq, text, COALESCE(parent_id, ''), section_path, is_parent
		FROM chunks WHERE id = ANY($1)`, ids)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	for rows.Next() {
		var id int64
		var r ChunkRow
		if err := rows.Scan(&id, &r.DocID, &r.Seq, &r.Text, &r.ParentID, &r.SectionPath, &r.IsParent); err != nil {
			return nil, err
		}
		out[strconv.FormatInt(id, 10)] = r
	}
	return out, rows.Err()
}

// DocMetaMap 批量取文档元信息。
func (s *Store) DocMetaMap(docIDs []string) (map[string]DocMeta, error) {
	out := make(map[string]DocMeta, len(docIDs))
	if len(docIDs) == 0 {
		return out, nil
	}
	rows, err := s.pool.Query(context.Background(), `
		SELECT id, title, source, updated FROM docs WHERE id = ANY($1)`, docIDs)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	for rows.Next() {
		var id string
		var m DocMeta
		if err := rows.Scan(&id, &m.Title, &m.Source, &m.Updated); err != nil {
			return nil, err
		}
		out[id] = m
	}
	return out, rows.Err()
}

// ListDocs 全部文档（按 doc_id 升序，含 chunk 计数）。
func (s *Store) ListDocs() ([]DocInfo, error) {
	rows, err := s.pool.Query(context.Background(), `
		SELECT d.id, d.title, d.source, d.updated, COUNT(c.id) AS chunks
		FROM docs d LEFT JOIN chunks c ON c.doc_id = d.id
		GROUP BY d.id, d.title, d.source, d.updated
		ORDER BY d.id`)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var out []DocInfo
	for rows.Next() {
		var d DocInfo
		if err := rows.Scan(&d.DocID, &d.Title, &d.Source, &d.Updated, &d.Chunks); err != nil {
			return nil, err
		}
		out = append(out, d)
	}
	return out, rows.Err()
}

// GetStats 索引规模。
func (s *Store) GetStats() (Stats, error) {
	var st Stats
	if err := s.pool.QueryRow(context.Background(), `
		SELECT (SELECT COUNT(*) FROM docs), (SELECT COUNT(*) FROM chunks)`).
		Scan(&st.Docs, &st.Chunks); err != nil {
		return st, err
	}
	emb, err := s.HasEmbeddings()
	st.Embedded = emb
	return st, err
}

// HasEmbeddings 库中是否存在向量。
func (s *Store) HasEmbeddings() (bool, error) {
	var exists bool
	if err := s.pool.QueryRow(context.Background(),
		`SELECT EXISTS(SELECT 1 FROM vectors)`).Scan(&exists); err != nil {
		return false, err
	}
	return exists, nil
}

func toInt64s(ids []int) []int64 {
	out := make([]int64, len(ids))
	for i, id := range ids {
		out[i] = int64(id)
	}
	return out
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

// ---------- 测试基建（agent/api 测试共用） ----------

// defaultTestDSN 测试库（与业务库 gewu 分离——测试会 Wipe 清库，绝不能指向真索引）。
const defaultTestDSN = "postgres://gewu:gewu@127.0.0.1:5433/gewu_test?sslmode=disable"

// OpenTest 打开测试库并清空（agent/api 测试共用）。测试库不存在或 PG 不可达时
// Skip——门禁口径是 make test（pg-up 前置）/CI service，裸 go test 允许跳过。
// 测试库须预先建好（CI 有建库步骤；宿主 brew 路径见 runbook T1 备注）。
//
// 跨包并行共用测试库的串行化：进程级一次性会话锁（sync.Once + 专用连接持有到
// 进程退出，连接断开 PG 自动释放）。不能按 Store 粒度加锁——同一测试可能构造
// 多个 Store（如 agent 测试的 testDeps + depsWithLLM），两把锁会互相等待死锁。
func OpenTest(t testing.TB) *Store {
	t.Helper()
	if err := lockTestDB(); err != nil {
		t.Skipf("PG 测试库不可用（%v）——跳过 PG 依赖用例；门禁请跑 make test", err)
	}
	s, err := Open(testDSN())
	if err != nil {
		t.Skipf("PG 测试库不可用（%v）——跳过 PG 依赖用例；门禁请跑 make test", err)
	}
	if err := s.Wipe(); err != nil {
		s.Close()
		t.Fatalf("清空测试库失败: %v", err)
	}
	t.Cleanup(func() { _ = s.Close() })
	return s
}

// testLockKey 测试库串行锁的固定 key（任意常量，全仓库唯一即可）。
const testLockKey = 941012

var (
	testLockOnce sync.Once
	testLockPool *pgxpool.Pool // 专用池：生命周期同进程（关掉它就等于放锁）
	testLockConn *pgxpool.Conn // 专用连接：持锁到进程退出，断开自动释放
	testLockErr  error
)

// lockTestDB 进程级一次：连测试库拿会话咨询锁并永不释放（进程退出即释放）。
func lockTestDB() error {
	testLockOnce.Do(func() {
		pool, err := pgxpool.New(context.Background(), testDSN())
		if err != nil {
			testLockErr = err
			return
		}
		conn, err := pool.Acquire(context.Background())
		if err != nil {
			pool.Close()
			testLockErr = err
			return
		}
		if _, err := conn.Exec(context.Background(), `SELECT pg_advisory_lock($1)`, testLockKey); err != nil {
			conn.Release()
			pool.Close()
			testLockErr = err
			return
		}
		testLockPool, testLockConn = pool, conn // 故意不关：进程退出 = 锁释放
	})
	return testLockErr
}

// testDSN 测试库连接串：PG_TEST_DSN > PG_DSN（OS 环境变量或仓库 .env，经
// config.Load 与主程序同源——go test 进程不会自己读 .env）推导 <db>_test > 缺省。
func testDSN() string {
	if v := os.Getenv("PG_TEST_DSN"); v != "" {
		return v
	}
	base := config.Load().PGDSN
	u, err := url.Parse(base)
	db := strings.TrimPrefix(u.Path, "/")
	if err != nil || db == "" {
		return defaultTestDSN
	}
	u.Path = "/" + db + "_test"
	return u.String()
}

// UnitVec 返回 EmbedDim 维单位向量，第 i 位为 1——测试/演示用（入库向量维度
// 必须是 2048，与 vectors.embedding 的 halfvec(2048) 对齐）。
func UnitVec(i int) []float64 {
	v := make([]float64, EmbedDim)
	v[((i%EmbedDim)+EmbedDim)%EmbedDim] = 1
	return v
}
