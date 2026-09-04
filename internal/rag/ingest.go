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
	NoEmbed bool // 只建 BM25 索引
	Rebuild bool // 删除旧索引文件后重建
}

// Ingest 语料入库：corpus/*.md → 解析 → 分块 →（可选）向量化 → SQLite。
// 返回 (文档数, chunk 数, 是否向量化和索引路径)。无 key 自动降级为仅 BM25。
func Ingest(ctx context.Context, s *config.Settings, client *llm.Client, opt IngestOptions) (Stats, string, error) {
	if opt.Rebuild {
		if _, err := os.Stat(s.IndexPath); err == nil {
			if err := os.Remove(s.IndexPath); err != nil {
				return Stats{}, s.IndexPath, err
			}
		}
	}
	if err := os.MkdirAll(filepath.Dir(s.IndexPath), 0o755); err != nil {
		return Stats{}, s.IndexPath, err
	}
	store, err := Open(s.IndexPath)
	if err != nil {
		return Stats{}, s.IndexPath, err
	}
	defer store.Close()

	useEmbed := !opt.NoEmbed && client != nil && client.HasKey()
	files, err := filepath.Glob(filepath.Join(s.CorpusDir, "*.md"))
	if err != nil {
		return Stats{}, s.IndexPath, err
	}
	sort.Strings(files)
	if len(files) == 0 {
		return Stats{}, s.IndexPath, fmt.Errorf("未找到语料文件：%s/*.md", s.CorpusDir)
	}

	for _, f := range files {
		doc, err := ParseDoc(f)
		if err != nil {
			return Stats{}, s.IndexPath, err
		}
		chunks := ChunkText(doc.Text, 450, 80)
		var vectors [][]float64
		if useEmbed && len(chunks) > 0 {
			vectors, err = EmbedBatched(ctx, client, chunks)
			if err != nil {
				// 端点不支持/无额度时，后续文档不再重试（与 Python 版一致）
				fmt.Printf("  !! 向量化不可用（%v），自动降级为仅 BM25 索引\n", err)
				useEmbed = false
				vectors = nil
			}
		}
		docID := strings.TrimSuffix(filepath.Base(f), ".md")
		title := doc.Meta["title"]
		if title == "" {
			title = docID
		}
		if err := store.UpsertDoc(docID, title, doc.Meta["source"], doc.Meta["updated"], chunks, vectors); err != nil {
			return Stats{}, s.IndexPath, err
		}
		mode := "仅BM25"
		if vectors != nil {
			mode = "BM25+向量"
		}
		fmt.Printf("  ✓ %s  %d 块  [%s]\n", docID, len(chunks), mode)
	}
	st, err := store.GetStats()
	return st, s.IndexPath, err
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
