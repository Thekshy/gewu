package rag

import (
	"context"
	"errors"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"gewu/internal/llm"
)

func writeFile(path, content string) error {
	return os.WriteFile(path, []byte(content), 0o644)
}

func testStore(t *testing.T) *Store {
	t.Helper()
	s, err := Open(filepath.Join(t.TempDir(), "index.db"))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = s.Close() })
	return s
}

func seedStore(t *testing.T) *Store {
	t.Helper()
	s := testStore(t)
	mustUpsert(t, s, "d1", "图书馆管理办法", "图书馆", []string{
		"图书馆开放时间为周一至周五 7:30 至 22:30，周末 8:30 至 21:30。本科生外借上限 10 册，借期 30 天。",
	})
	mustUpsert(t, s, "d2", "学生宿舍管理规定", "学生处", []string{
		"宿舍门禁时间为 23:00 至次日 6:00，晚归需登记并告知辅导员。",
	})
	return s
}

func mustUpsert(t *testing.T, s *Store, docID, title, source string, chunks []string) {
	t.Helper()
	if err := s.UpsertDoc(docID, title, source, "2026-01-01", flatRecords(chunks)); err != nil {
		t.Fatal(err)
	}
}

// flatRecords 纯文本 chunk → 无向量、无父子关系的 ChunkRecord（flat 兼容形态）。
func flatRecords(chunks []string) []ChunkRecord {
	records := make([]ChunkRecord, len(chunks))
	for i, c := range chunks {
		records[i] = ChunkRecord{Text: c, ParentIdx: -1}
	}
	return records
}

// mockLLM rag 检索管线的 LLM mock（不发真实网络请求）。
type mockLLM struct {
	embed func(texts []string) [][]float64
	chat  string
}

func (m *mockLLM) HasKey() bool { return true }

func (m *mockLLM) Chat(ctx context.Context, messages []llm.Message, o llm.Options) (string, error) {
	return m.chat, nil
}

func (m *mockLLM) Embed(ctx context.Context, texts []string) ([][]float64, error) {
	if m.embed == nil {
		return nil, nil
	}
	return m.embed(texts), nil
}

func TestTokenizeChineseBigramAndLatin(t *testing.T) {
	tokens := Tokenize("GPA 3.0 要求")
	hasGPA, hasReq := false, false
	for _, tok := range tokens {
		if tok == "gpa" {
			hasGPA = true
		}
		if tok == "要求" {
			hasReq = true
		}
	}
	if !hasGPA || !hasReq {
		t.Errorf("tokens = %v, 应含 gpa 与「要求」二元组", tokens)
	}
}

func TestTokenizeHanBigramAdjacent(t *testing.T) {
	// 「转专业」→ 转专 / 专业
	tokens := Tokenize("转专业")
	if len(tokens) != 2 || tokens[0] != "转专" || tokens[1] != "专业" {
		t.Errorf("tokens = %v", tokens)
	}
}

func TestBM25RetrievesRelevantChunk(t *testing.T) {
	s := seedStore(t)
	hits, err := s.BM25Search("图书馆几点开门", 2)
	if err != nil {
		t.Fatal(err)
	}
	if len(hits) == 0 {
		t.Fatal("应至少命中一条")
	}
	rows, err := s.ChunkRows([]int{hits[0].ID})
	if err != nil {
		t.Fatal(err)
	}
	if len(rows) == 0 || rows[hits[0].ID].DocID != "d1" {
		t.Errorf("top1 应为 d1, rows = %v", rows)
	}
}

func TestBM25EmptyIndexReturnsNothing(t *testing.T) {
	s := testStore(t)
	hits, err := s.BM25Search("任意", 5)
	if err != nil || len(hits) != 0 {
		t.Errorf("空库应无命中: %v %v", hits, err)
	}
}

func TestRRFFusePrefersConsensus(t *testing.T) {
	// chunk 2 在两路召回中都靠前（rank1+rank0），应压过只在一路靠前的 chunk 1（rank0+rank2）
	got := RRFFuse([][]int{{1, 2}, {2, 3}}, 60)
	if got[0] != 2 {
		t.Errorf("RRF 首位 = %d, want 2", got[0])
	}
}

func TestRRFFuseTieKeepsFirstSeenOrder(t *testing.T) {
	// 5 与 7 同为 1/61（平分，按首次出现 5 在前），6 为 1/62 排最后
	got := RRFFuse([][]int{{5, 6}, {7}}, 60)
	if len(got) != 3 || got[0] != 5 || got[1] != 7 || got[2] != 6 {
		t.Errorf("平分应按首次出现序: %v", got)
	}
}

func TestUpsertDocIsIdempotent(t *testing.T) {
	s := seedStore(t)
	mustUpsert(t, s, "d1", "图书馆管理办法", "图书馆", []string{"更新后的内容"})
	st, err := s.GetStats()
	if err != nil {
		t.Fatal(err)
	}
	if st.Docs != 2 || st.Chunks != 2 {
		t.Fatalf("docs=%d chunks=%d, want 2/2（旧 chunk 已替换）", st.Docs, st.Chunks)
	}
	docs, _ := s.ListDocs()
	for _, d := range docs {
		if d.DocID == "d1" && d.Chunks != 1 {
			t.Errorf("d1 chunks = %d, want 1", d.Chunks)
		}
	}
}

func TestVectorSearchCosine(t *testing.T) {
	s := testStore(t)
	// 两个 chunk：一个与查询同向，一个正交
	err := s.UpsertDoc("d1", "t", "s", "", []ChunkRecord{
		{Text: "a", Vec: []float64{1, 0}, ParentIdx: -1},
		{Text: "b", Vec: []float64{0, 1}, ParentIdx: -1},
	})
	if err != nil {
		t.Fatal(err)
	}
	hits, err := s.VectorSearch([]float64{2, 0}, 2) // 未归一化查询也应正确处理
	if err != nil {
		t.Fatal(err)
	}
	if len(hits) != 2 || hits[0].Score < 0.99 {
		t.Errorf("hits = %v, 同向向量应为 top1 且得分≈1", hits)
	}
	emb, _ := s.HasEmbeddings()
	if !emb {
		t.Error("应报告已有向量")
	}
}

func TestVectorBlobNumpyCompatible(t *testing.T) {
	// float32 小端字节序应与 numpy tobytes 一致：1.0 → 00 00 80 3F
	blob, dim := packVector([]float64{1.0})
	if dim != 1 || len(blob) != 4 || blob[0] != 0 || blob[1] != 0 || blob[2] != 0x80 || blob[3] != 0x3F {
		t.Errorf("packVector(1.0) = % X dim=%d", blob, dim)
	}
}

func TestChunkTextSplitsLongParagraph(t *testing.T) {
	text := strings.Repeat("长句。", 300) // 900 字
	chunks := ChunkText(text, 450, 80)
	if len(chunks) <= 1 {
		t.Fatal("超长段落应切分")
	}
	for _, c := range chunks {
		if len([]rune(c)) > 450 {
			t.Errorf("chunk 超长: %d", len([]rune(c)))
		}
	}
}

func TestChunkTextKeepsShortTextWhole(t *testing.T) {
	got := ChunkText("只有一段短文本。", 450, 80)
	if len(got) != 1 || got[0] != "只有一段短文本。" {
		t.Errorf("got = %v", got)
	}
}

func TestChunkTextAggregatesParagraphs(t *testing.T) {
	got := ChunkText("第一段。\n\n第二段。", 450, 80)
	if len(got) != 1 || got[0] != "第一段。\n\n第二段。" {
		t.Errorf("短段落应聚合, got = %q", got[0])
	}
}

func TestParseDocFrontmatter(t *testing.T) {
	f := filepath.Join(t.TempDir(), "0001-demo.md")
	content := "---\ntitle: 演示文档\nsource: 教务处\nupdated: 2026-01-01\n---\n\n正文第一段。\n\n正文第二段。"
	if err := writeFile(f, content); err != nil {
		t.Fatal(err)
	}
	doc, err := ParseDoc(f)
	if err != nil {
		t.Fatal(err)
	}
	if doc.Meta["title"] != "演示文档" || doc.Meta["source"] != "教务处" {
		t.Errorf("meta = %v", doc.Meta)
	}
	if !strings.HasPrefix(doc.Text, "正文第一段") {
		t.Errorf("text = %q", doc.Text)
	}
}

func TestParseDocDefaults(t *testing.T) {
	f := filepath.Join(t.TempDir(), "0002-plain.md")
	if err := writeFile(f, "没有 frontmatter 的正文。"); err != nil {
		t.Fatal(err)
	}
	doc, err := ParseDoc(f)
	if err != nil {
		t.Fatal(err)
	}
	if doc.Meta["title"] != "0002-plain" || doc.Meta["source"] != "钱塘大学" {
		t.Errorf("缺省 meta = %v", doc.Meta)
	}
}

func TestRetrieverMissingVectorsErrors(t *testing.T) {
	// P6 阶段0 去静默降级：索引无向量时不再退纯 BM25，明确报错提示重建。
	s := seedStore(t)
	r := NewRetriever(s, 6, &mockLLM{})
	_, err := r.Search(context.Background(), "宿舍门禁几点", 6)
	if !errors.Is(err, ErrMissingVectors) {
		t.Fatalf("err = %v, want ErrMissingVectors", err)
	}
}

func TestRetrieverBM25WithVectors(t *testing.T) {
	s := seedStore(t)
	// 与旧索引形态一致：每个 chunk 带同维向量（值不影响 BM25 命中结果）
	docs, _ := s.ListDocs()
	_ = docs
	rows, err := s.ChunkRows(nil)
	_ = rows
	// 直接重灌带向量的同语料
	if err := s.UpsertDoc("d1", "图书馆管理办法", "图书馆", "2026-01-01", []ChunkRecord{
		{Text: "图书馆开放时间为周一至周五 7:30 至 22:30，周末 8:30 至 21:30。本科生外借上限 10 册，借期 30 天。", Vec: []float64{1, 0}, ParentIdx: -1},
	}); err != nil {
		t.Fatal(err)
	}
	if err := s.UpsertDoc("d2", "学生宿舍管理规定", "学生处", "2026-01-01", []ChunkRecord{
		{Text: "宿舍门禁时间为 23:00 至次日 6:00，晚归需登记并告知辅导员。", Vec: []float64{1, 0}, ParentIdx: -1},
	}); err != nil {
		t.Fatal(err)
	}
	client := &mockLLM{embed: func(texts []string) [][]float64 {
		out := make([][]float64, len(texts))
		for i := range out {
			out[i] = []float64{0.6, 0.8}
		}
		return out
	}}
	r := NewRetriever(s, 6, client)
	hits, err := r.Search(context.Background(), "宿舍门禁几点", 6)
	if err != nil {
		t.Fatal(err)
	}
	if len(hits) == 0 || hits[0].DocID != "d2" {
		t.Fatalf("hits = %v", hits)
	}
	if hits[0].Title != "学生宿舍管理规定" || hits[0].Source != "学生处" {
		t.Errorf("命中应带元信息: %+v", hits[0])
	}
}
