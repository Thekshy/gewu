package rag

import (
	"regexp"
	"strings"
)

// P6 阶段1 / P7 加固：Markdown 结构切分 + 父子块（方向 A：粗父细子）。
//
// 父块（is_parent）按"父边界层级"聚合：parentHeadingLevel 及以上的标题开启
// 新父块，其后所有更深层级小节的正文归并进该父块（小节标题以纯文本行保留，
// 让子块也知道归属）；父块带 breadcrumb、只入库不建向量不进召回，命中子块后
// 按 parent_id 回取其文本进上下文。
// 子块 = 父块聚合文本内按段落聚合的小块，是向量/BM25 索引与匹配的真正单元。
// 所有长度按 rune 计数，遍历顺序固定，输出确定可复现。

// parentHeadingLevel 父边界层级：Level<=该值的标题开启新父块。
// 校规语料只有 H1/H2 两级（H2 是条款小节），取 1 让整篇文档聚合为父块、
// 各 H2 小节标题并入父块正文——多数父块对应多个子块，做到真 1:N 的
// small-to-big；若按 H2 聚合，短文档会退化成 1:1（父块不比子块大）。
const parentHeadingLevel = 1

// Node 标题切分出的 section 节点。
type Node struct {
	Level int    // 标题层级 1~6；0 = 无标题文档的整篇
	Title string // 标题文本
	Path  string // breadcrumb，如 "转专业管理办法 > 申请条件"；无标题为空
	Body  string // 正文，不含标题行本身
}

// headingRe 匹配 Markdown 标题行（行首 1~6 个 # + 空格 + 标题文本）。
var headingRe = regexp.MustCompile(`(?m)^(#{1,6})[ \t]+(.*)$`)

// ParseMarkdownTree 按标题把文档切成带 breadcrumb 的 section 节点。
// Body 从标题行结束、吃掉行尾换行之后开始——不含 "# 标题" 行本身，
// breadcrumb 只由 withPath 的【路径】承担一次，不重复标题。
// 无任何标题时返回单个 Level=0 节点（整篇为一个 section，兼容纯文本语料）。
func ParseMarkdownTree(text string) []Node {
	matches := headingRe.FindAllStringSubmatchIndex(text, -1)
	if len(matches) == 0 {
		return []Node{{Level: 0, Body: strings.TrimSpace(text)}}
	}
	var nodes []Node
	stack := []string{} // 当前标题栈，用于生成 breadcrumb
	for i, m := range matches {
		lvl := len(text[m[2]:m[3]])
		title := strings.TrimSpace(text[m[4]:m[5]])
		bodyStart := m[1]
		if bodyStart < len(text) && text[bodyStart] == '\r' {
			bodyStart++
		}
		if bodyStart < len(text) && text[bodyStart] == '\n' {
			bodyStart++
		}
		bodyEnd := len(text)
		if i+1 < len(matches) {
			bodyEnd = matches[i+1][0]
		}
		body := strings.TrimSpace(text[bodyStart:bodyEnd])
		for len(stack) >= lvl {
			stack = stack[:len(stack)-1]
		}
		stack = append(stack, title)
		nodes = append(nodes, Node{Level: lvl, Title: title, Path: strings.Join(stack, " > "), Body: body})
	}
	return nodes
}

// Chunk 父子块两层切分的输出单元（入库前中间形态）。
type Chunk struct {
	Text        string
	SectionPath string
	IsParent    bool
	// ParentIdx 指向 HierarchicalChunks 输出中父块的下标；父块自身为 -1。
	// 入库时由 UpsertDoc 换成父块真实 chunk id 写入 parent_id 列。
	ParentIdx int
}

// 父子块默认尺寸：父块聚合上限 800 rune、子块 200 rune、滑切 overlap 40。
// 父 > 子拉开粒度差（粗父细子）。
const (
	parentLimit = 800
	childLimit  = 200
	hierOverlap = 40
)

// parentGroup 父块聚合的中间态：路径 + 归并后的正文。
type parentGroup struct {
	path string
	body string
}

// aggregateParents 按 parentHeadingLevel 两级聚合：
// Level<=parentHeadingLevel（或无标题 Level=0）开启新父块，把其后所有
// 更深层级小节的正文归并进来，小节标题以纯文本行保留作上下文；
// 聚合后为空的 section 不产出父块。
func aggregateParents(text string) []parentGroup {
	var groups []parentGroup
	for _, n := range ParseMarkdownTree(text) {
		if n.Level == 0 || n.Level <= parentHeadingLevel {
			groups = append(groups, parentGroup{path: n.Path, body: n.Body})
			continue
		}
		if len(groups) == 0 {
			groups = append(groups, parentGroup{}) // 文档以小节标题开头：先开一个匿名父
		}
		g := &groups[len(groups)-1]
		section := n.Body
		if n.Title != "" {
			section = n.Title + "\n" + n.Body
		}
		if g.body != "" {
			g.body += "\n\n"
		}
		g.body += section
	}
	var out []parentGroup
	for _, g := range groups {
		if strings.TrimSpace(g.body) != "" {
			out = append(out, g)
		}
	}
	return out
}

// HierarchicalChunks 父子两层切分：父=按标题层级聚合的正文（超限滑切），
// 子=父块文本内按段落聚合到 childLimit。子块在输出中紧跟其父块，
// 整体顺序为"父-子-子…-父-子"。
func HierarchicalChunks(text string, parentLimit, childLimit, overlap int) []Chunk {
	var out []Chunk
	for _, group := range aggregateParents(text) {
		parents := ChunkText(group.body, parentLimit, overlap)
		for _, p := range parents {
			parentIdx := len(out)
			out = append(out, Chunk{
				Text:        withPath(group.path, p),
				SectionPath: group.path,
				IsParent:    true,
				ParentIdx:   -1,
			})
			for _, c := range ChunkText(p, childLimit, overlap) {
				out = append(out, Chunk{Text: c, SectionPath: group.path, IsParent: false, ParentIdx: parentIdx})
			}
		}
	}
	return out
}

// withPath 父块文本前缀 breadcrumb，使回取进上下文时自带标题定位。
func withPath(path, body string) string {
	if path == "" {
		return body
	}
	return "【" + path + "】\n" + body
}
