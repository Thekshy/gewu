package rag

import (
	"context"
	"fmt"
	"io/fs"
	"math"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strings"

	"gewu/internal/config"
	"gewu/internal/llm"
)

// frontmatterRe 匹配文件头部的 YAML frontmatter（(?s) 使 . 跨行，等价 Python re.DOTALL）。
var frontmatterRe = regexp.MustCompile(`(?s)\A---\s*\n(.*?)\n---\s*\n?`)

// embedBatch 向量化分批大小（与 Python 版一致）。
const embedBatch = 32

// ParsedDoc 解析后的语料文档。
type ParsedDoc struct {
	Meta map[string]string
	Text string
}

// ParseDoc 解析带 frontmatter 的 Markdown：title / source / updated + 正文。
// 缺省 title=文件名去扩展、source=钱塘大学、updated=""。
func ParseDoc(path string) (ParsedDoc, error) {
	raw, err := os.ReadFile(path)
	if err != nil {
		return ParsedDoc{}, err
	}
	meta := map[string]string{"title": strings.TrimSuffix(filepath.Base(path), ".md"), "source": "钱塘大学", "updated": ""}
	text := string(raw)
	if m := frontmatterRe.FindStringSubmatch(text); m != nil {
		for _, line := range strings.Split(m[1], "\n") {
			key, val, found := strings.Cut(line, ":")
			if found {
				meta[strings.ToLower(strings.TrimSpace(key))] = strings.TrimSpace(val)
			}
		}
		text = text[len(m[0]):]
	}
	return ParsedDoc{Meta: meta, Text: strings.TrimSpace(text)}, nil
}

// ChunkText 按段落聚合切块；超长单段滑动硬切，保留 overlap 字符衔接。
// 字符数按 rune 计（与 Python len() 语义一致）。
func ChunkText(text string, limit, overlap int) []string {
	paras := splitParagraphs(text)
	var chunks []string
	buf := ""
	for _, para := range paras {
		candidate := para
		if buf != "" {
			candidate = strings.TrimSpace(buf + "\n\n" + para)
		}
		if len([]rune(candidate)) <= limit {
			buf = candidate
			continue
		}
		if buf != "" {
			chunks = append(chunks, buf)
		}
		r := []rune(para)
		for len(r) > limit {
			chunks = append(chunks, string(r[:limit]))
			r = r[limit-overlap:]
		}
		buf = string(r)
	}
	if buf != "" {
		chunks = append(chunks, buf)
	}
	return chunks
}

// splitParagraphs 空行分段并去空白段。
func splitParagraphs(text string) []string {
	var out []string
	for _, p := range regexp.MustCompile(`\n\s*\n`).Split(text, -1) {
		if t := strings.TrimSpace(p); t != "" {
			out = append(out, t)
		}
	}
	return out
}

// EmbedBatched 分批向量化并做 L2 归一化（client 为 LLMer——决策 A 同族接口化，
// 单体注入 *llm.Client 原样工作）。端点不支持/无额度时返回错误由调用方降级。
func EmbedBatched(ctx context.Context, client LLMer, chunks []string) ([][]float64, error) {
	var all [][]float64
	for i := 0; i < len(chunks); i += embedBatch {
		end := i + embedBatch
		if end > len(chunks) {
			end = len(chunks)
		}
		vecs, err := client.Embed(ctx, chunks[i:end])
		if err != nil {
			return nil, err
		}
		all = append(all, vecs...)
	}
	for _, v := range all {
		var norm float64
		for _, x := range v {
			norm += x * x
		}
		norm = math.Sqrt(norm)
		if norm > 0 {
			for i := range v {
				v[i] /= norm
			}
		}
	}
	return all, nil
}

// IngestOptions 入库 CLI 选项。
type IngestOptions struct {
	NoEmbed bool // 只建 BM25 索引（显式手动选项）
	Rebuild bool // 删除旧索引文件后重建
}

// Ingest 语料入库：corpus/*.md → 解析 → 分块 →（可选）向量化 → PostgreSQL。
// 返回 (文档数, chunk 数, 是否向量化和 DSN)。
// P6 阶段0 去静默降级：非 -no-embed 时向量化失败直接报错退出，
// 不再"假成功"成纯 BM25 索引（向量缺失在检索侧也会被明确拒绝）。
func Ingest(ctx context.Context, s *config.Settings, client *llm.Client, opt IngestOptions) (Stats, string, error) {
	store, err := Open(s.PGDSN)
	if err != nil {
		return Stats{}, s.PGDSN, err
	}
	defer store.Close()
	if opt.Rebuild {
		if err := store.Wipe(); err != nil {
			return Stats{}, s.PGDSN, err
		}
	}

	hierarchical := s.ChunkMode != "flat"
	useEmbed := !opt.NoEmbed
	if useEmbed && (client == nil || !client.HasEmbedKey()) {
		return Stats{}, s.PGDSN, fmt.Errorf("未配置 EMBED_API_KEY/LLM_API_KEY，无法向量化；确要仅建 BM25 请显式加 -no-embed")
	}
	files, err := filepath.Glob(filepath.Join(s.CorpusDir, "*.md"))
	if err != nil {
		return Stats{}, s.PGDSN, err
	}
	sort.Strings(files)
	if len(files) == 0 {
		return Stats{}, s.PGDSN, fmt.Errorf("未找到语料文件：%s/*.md", s.CorpusDir)
	}

	modeLabel := "BM25+向量"
	if !useEmbed {
		modeLabel = "仅BM25（-no-embed）"
	}
	for _, f := range files {
		doc, err := ParseDoc(f)
		if err != nil {
			return Stats{}, s.PGDSN, err
		}
		docID := strings.TrimSuffix(filepath.Base(f), ".md")
		records, err := buildRecords(ctx, docID, doc, client, useEmbed, hierarchical)
		if err != nil {
			return Stats{}, s.PGDSN, err
		}
		title := doc.Meta["title"]
		if title == "" {
			title = docID
		}
		if err := store.UpsertDoc(docID, title, doc.Meta["source"], doc.Meta["updated"], records); err != nil {
			return Stats{}, s.PGDSN, err
		}
		fmt.Printf("  ✓ %s  %d 块（父 %d/子 %d）  [%s]\n", docID, len(records),
			countParents(records), len(records)-countParents(records), modeLabel)
	}
	st, err := store.GetStats()
	return st, s.PGDSN, err
}

// buildRecords 单文档切分 + 向量化：
// hierarchical（默认）父子两层——只对子块向量化，父块仅入库供回取；
// flat 旧路径逐字保留 ChunkText(450,80) 单层切分，行为与冻结基线一致。
func buildRecords(ctx context.Context, docID string, doc ParsedDoc, client *llm.Client, useEmbed, hierarchical bool) ([]ChunkRecord, error) {
	if !hierarchical {
		texts := ChunkText(doc.Text, 450, 80)
		var vectors [][]float64
		if useEmbed && len(texts) > 0 {
			var err error
			vectors, err = EmbedBatched(ctx, client, texts)
			if err != nil {
				return nil, fmt.Errorf("向量化失败: %w", err)
			}
		}
		records := make([]ChunkRecord, len(texts))
		for i, t := range texts {
			records[i] = ChunkRecord{Text: t, ParentIdx: -1}
			if vectors != nil && i < len(vectors) {
				records[i].Vec = vectors[i]
			}
		}
		return records, nil
	}
	chunks := HierarchicalChunks(doc.Text, parentLimit, childLimit, hierOverlap)
	var childTexts []string
	childIdx := []int{}
	for i, c := range chunks {
		if !c.IsParent {
			childTexts = append(childTexts, c.Text)
			childIdx = append(childIdx, i)
		}
	}
	var childVecs [][]float64
	if useEmbed && len(childTexts) > 0 {
		var err error
		childVecs, err = EmbedBatched(ctx, client, childTexts)
		if err != nil {
			return nil, fmt.Errorf("子块向量化失败: %w", err)
		}
	}
	records := make([]ChunkRecord, len(chunks))
	vecAt := map[int][]float64{}
	for j, idx := range childIdx {
		if j < len(childVecs) {
			vecAt[idx] = childVecs[j]
		}
	}
	for i, c := range chunks {
		records[i] = ChunkRecord{
			Text:        c.Text,
			SectionPath: c.SectionPath,
			IsParent:    c.IsParent,
			ParentIdx:   c.ParentIdx,
			Vec:         vecAt[i],
		}
	}
	return records, nil
}

func countParents(records []ChunkRecord) int {
	n := 0
	for _, r := range records {
		if r.IsParent {
			n++
		}
	}
	return n
}

// CorpusFiles 列出语料文件（供测试与统计）。
func CorpusFiles(dir string) []fs.FileInfo {
	entries, err := os.ReadDir(dir)
	if err != nil {
		return nil
	}
	var out []fs.FileInfo
	for _, e := range entries {
		if strings.HasSuffix(e.Name(), ".md") {
			if info, err := e.Info(); err == nil {
				out = append(out, info)
			}
		}
	}
	return out
}
