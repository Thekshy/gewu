package rag

import (
	"context"
	"fmt"
	"strings"
	"testing"
)

// ---------- P6 阶段1：结构切分 + 父子块 ----------

func TestParseMarkdownTreeBreadcrumbs(t *testing.T) {
	text := "# 学籍管理\n\n总则内容。\n\n## 转专业\n\n转专业正文。\n\n### 申请条件\n\n绩点要求正文。"
	nodes := ParseMarkdownTree(text)
	if len(nodes) != 3 {
		t.Fatalf("nodes = %d, want 3", len(nodes))
	}
	if nodes[0].Level != 1 || nodes[0].Path != "学籍管理" {
		t.Errorf("node0 = %+v", nodes[0])
	}
	if nodes[1].Path != "学籍管理 > 转专业" {
		t.Errorf("node1.Path = %q", nodes[1].Path)
	}
	if nodes[2].Level != 3 || nodes[2].Path != "学籍管理 > 转专业 > 申请条件" {
		t.Errorf("node2 = %+v", nodes[2])
	}
	if nodes[2].Body != "绩点要求正文。" {
		t.Errorf("node2.Body = %q", nodes[2].Body)
	}
	// 兄弟标题正确弹栈
	text2 := "# A\n\na正文\n\n## B\n\nb正文\n\n## C\n\nc正文"
	nodes2 := ParseMarkdownTree(text2)
	if len(nodes2) != 3 || nodes2[2].Path != "A > C" {
		t.Errorf("兄弟标题 breadcrumb = %+v", nodes2)
	}
}

func TestParseMarkdownTreeNoHeading(t *testing.T) {
	nodes := ParseMarkdownTree("纯文本正文，没有标题。")
	if len(nodes) != 1 || nodes[0].Level != 0 || nodes[0].Body != "纯文本正文，没有标题。" {
		t.Errorf("无标题文档 = %+v", nodes)
	}
}

func TestParseMarkdownTreeBodyExcludesHeadingLine(t *testing.T) {
	// P7：Body 不以 "# 标题" 行开头（breadcrumb 由 withPath 承担一次，不重复标题）
	text := "# 政策\n\n总则正文。\n\n## 条款\n\n条款正文。"
	nodes := ParseMarkdownTree(text)
	if strings.HasPrefix(nodes[0].Body, "#") {
		t.Errorf("H1 Body 不应含标题行: %q", nodes[0].Body)
	}
	if nodes[0].Body != "总则正文。" {
		t.Errorf("H1 Body = %q", nodes[0].Body)
	}
	if nodes[1].Body != "条款正文。" {
		t.Errorf("H2 Body = %q", nodes[1].Body)
	}
}

func TestHierarchicalChunksParentChildMapping(t *testing.T) {
	// 1 个 H1 + 3 个 H2 短小节：按父边界层级聚合后应得到真 1:N（父块数 < 子块数）
	text := "# 学籍管理\n\n" +
		"## 转专业\n\n" + strings.Repeat("转专业条款内容。", 15) + "\n\n" +
		"## 保研\n\n" + strings.Repeat("保研条款内容。", 15) + "\n\n" +
		"## 学分认定\n\n" + strings.Repeat("学分认定条款内容。", 15)
	chunks := HierarchicalChunks(text, 800, 200, 40)

	// 结构断言：父块在前，子块紧跟其后且 ParentIdx 指向存在的父块
	var parentIdxs []int
	for i, c := range chunks {
		if c.IsParent {
			parentIdxs = append(parentIdxs, i)
			if c.ParentIdx != -1 {
				t.Errorf("父块 %d ParentIdx = %d, want -1", i, c.ParentIdx)
			}
			continue
		}
		if c.ParentIdx < 0 || c.ParentIdx >= len(chunks) || !chunks[c.ParentIdx].IsParent {
			t.Errorf("子块 %d ParentIdx = %d 非法", i, c.ParentIdx)
		}
	}
	if len(parentIdxs) != 1 {
		t.Fatalf("父块数 = %d, want 1（H1 聚合整篇）", len(parentIdxs))
	}
	children := len(chunks) - 1
	if children < 2 {
		t.Fatalf("子块数 = %d, 应 ≥2（真 1:N，small-to-big）", children)
	}
	// 父块文本明显大于单个子块（粗父细子）
	parentLen := len([]rune(chunks[parentIdxs[0]].Text))
	maxChild := 0
	for _, c := range chunks {
		if !c.IsParent && len([]rune(c.Text)) > maxChild {
			maxChild = len([]rune(c.Text))
		}
	}
	if parentLen <= maxChild {
		t.Errorf("父块 %d 应大于最大子块 %d（粗父细子）", parentLen, maxChild)
	}
	// 父块文本不含重复的 # 标题行；小节标题以纯文本行保留
	if strings.Contains(chunks[parentIdxs[0]].Text, "#") {
		t.Errorf("父块文本不应含 # 标题行: %q", chunks[parentIdxs[0]].Text[:80])
	}
	for _, title := range []string{"转专业", "保研", "学分认定"} {
		if !strings.Contains(chunks[parentIdxs[0]].Text, title) {
			t.Errorf("父块应保留小节标题 %q 作上下文", title)
		}
	}
	// breadcrumb 只出现一次；子块无 breadcrumb 前缀
	if strings.Count(chunks[parentIdxs[0]].Text, "【学籍管理】") != 1 {
		t.Errorf("breadcrumb 应只出现一次: %q", chunks[parentIdxs[0]].Text[:60])
	}
	if strings.HasPrefix(chunks[1].Text, "【") {
		t.Errorf("子块文本不应带 breadcrumb 前缀: %q", chunks[1].Text)
	}
	if chunks[0].SectionPath != "学籍管理" {
		t.Errorf("SectionPath = %q", chunks[0].SectionPath)
	}
	// 顺序为"父-子-子…"：首元素是父块
	if !chunks[0].IsParent {
		t.Fatal("输出应以父块开头")
	}
	// 顺序可复现：再切一次逐字一致
	again := HierarchicalChunks(text, 800, 200, 40)
	if fmt.Sprint(again) != fmt.Sprint(chunks) {
		t.Error("切分结果应确定可复现")
	}
}

func TestHierarchicalChunksDocStartsWithDeepHeading(t *testing.T) {
	// 文档以小节标题开头（无 H1/H2）：退化为单父多子
	text := "### 小节甲\n\n甲正文。" + strings.Repeat("内容续。", 80) +
		"\n\n### 小节乙\n\n乙正文。" + strings.Repeat("内容续。", 80)
	chunks := HierarchicalChunks(text, 800, 200, 40)
	parents, children := 0, 0
	for i, c := range chunks {
		if c.IsParent {
			parents++
			continue
		}
		children++
		if c.ParentIdx < 0 || !chunks[c.ParentIdx].IsParent {
			t.Errorf("子块 %d ParentIdx = %d 非法", i, c.ParentIdx)
		}
	}
	if parents != 1 || children < 2 {
		t.Fatalf("parents=%d children=%d, want 1 parent + ≥2 children", parents, children)
	}
}

func TestHierarchicalChunksNoHeadingDoc(t *testing.T) {
	// 无标题文档：退化为单父多子
	text := strings.Repeat("纯文本内容段落。", 100)
	chunks := HierarchicalChunks(text, 800, 200, 40)
	parents, children := 0, 0
	for _, c := range chunks {
		if c.IsParent {
			parents++
		} else {
			children++
		}
	}
	if parents != 1 || children < 2 {
		t.Fatalf("parents=%d children=%d, want 1 parent + ≥2 children", parents, children)
	}
	if chunks[0].SectionPath != "" {
		t.Errorf("无标题文档 SectionPath 应为空: %q", chunks[0].SectionPath)
	}
}

func TestHierarchicalChunksLongDocSplitsParents(t *testing.T) {
	text := "# 政策\n\n" + strings.Repeat("这是一个很长的段落，用于触发父块滑切。", 120) // 远超 800 rune
	chunks := HierarchicalChunks(text, 800, 200, 40)
	parents := 0
	for _, c := range chunks {
		if c.IsParent {
			parents++
			if len([]rune(c.Text)) > 800+len("【政策】\n") {
				t.Errorf("父块超长: %d", len([]rune(c.Text)))
			}
		}
	}
	if parents < 2 {
		t.Errorf("超长文档应滑切出多个父块, got %d", parents)
	}
}

func TestHierarchicalIngestInvariants(t *testing.T) {
	s := testStore(t)
	md := "# 图书馆\n\n## 开放时间\n\n开放时间为 7:30 至 22:30。\n\n## 借阅规则\n\n本科生外借上限 10 册，借期 30 天。"
	chunks := HierarchicalChunks(md, 800, 200, 40)
	records := make([]ChunkRecord, len(chunks))
	for i, c := range chunks {
		records[i] = ChunkRecord{Text: c.Text, SectionPath: c.SectionPath, IsParent: c.IsParent, ParentIdx: c.ParentIdx}
		if !c.IsParent {
			records[i].Vec = UnitVec(0) // 只有子块给向量
		}
	}
	if err := s.UpsertDoc("d1", "图书馆管理办法", "图书馆", "", records); err != nil {
		t.Fatal(err)
	}

	// 1) BM25 召回的全部是子块（父块不进倒排）。方向 A 下小节标题并入父块正文、
	// 随之进入子块文本——"借阅规则"应能命中子块。
	hits, err := s.BM25Search("借阅规则", 5)
	if err != nil || len(hits) == 0 {
		t.Fatalf("小节标题词应命中子块: %v %v", hits, err)
	}

	// 2) 子块正常命中，且命中的都不是父块。
	hits2, err := s.BM25Search("开放时间 22:30", 5)
	if err != nil || len(hits2) == 0 {
		t.Fatalf("子块应命中: %v %v", hits2, err)
	}
	rows, err := s.ChunkRows(idsOf(hits2))
	if err != nil {
		t.Fatal(err)
	}
	for _, h := range hits2 {
		if rows[h.ID].IsParent {
			t.Errorf("父块 %d 不应出现在 BM25 召回", h.ID)
		}
	}

	// 3) 向量只对应子块：VectorSearch 全量返回的 id 均非父块。
	vecHits, err := s.VectorSearch(UnitVec(0), 100)
	if err != nil {
		t.Fatal(err)
	}
	if len(vecHits) == 0 {
		t.Fatal("应有向量命中")
	}
	allRows, err := s.ChunkRows(idsOfScored(vecHits))
	if err != nil {
		t.Fatal(err)
	}
	for _, h := range vecHits {
		if allRows[h.ID].IsParent {
			t.Errorf("父块 %d 不应建向量", h.ID)
		}
	}

	// 4) 子块 parent_id 回取父块。
	var child ChunkRow
	for _, row := range allRows {
		if !row.IsParent && row.ParentID != "" {
			child = row
			break
		}
	}
	if child.ParentID == "" {
		t.Fatal("子块应有 parent_id")
	}
	prow, err := s.ParentRows([]string{child.ParentID})
	if err != nil || len(prow) != 1 || !prow[child.ParentID].IsParent {
		t.Fatalf("父块回取失败: %v %v", prow, err)
	}
	if !strings.HasPrefix(prow[child.ParentID].Text, "【图书馆】") {
		t.Errorf("父块文本应带 breadcrumb: %q", prow[child.ParentID].Text)
	}
}

func idsOf(hits []Scored) []int {
	out := make([]int, len(hits))
	for i, h := range hits {
		out[i] = h.ID
	}
	return out
}

func idsOfScored(hits []Scored) []int { return idsOf(hits) }

func TestUpsertParentIdxValidation(t *testing.T) {
	s := testStore(t)
	err := s.UpsertDoc("d1", "t", "s", "", []ChunkRecord{
		{Text: "子块", IsParent: false, ParentIdx: 5}, // 父块不存在
	})
	if err == nil {
		t.Fatal("非法 ParentIdx 应报错")
	}
}

// ---------- P6 阶段3：rerank + 父子扩展 ----------

func TestRerankScoresOrderAndFallback(t *testing.T) {
	client := &mockLLM{chat: `{"scores":[3, 9]}`}
	rr := NewLLMReranker(client)
	scores, err := rr.Rerank(context.Background(), "查询", []string{"甲", "乙"})
	if err != nil || len(scores) != 2 || scores[1] != 9 {
		t.Fatalf("scores = %v, %v", scores, err)
	}
	// 分数个数不符 → 报错（调用方退回 RRF 顺序）
	client2 := &mockLLM{chat: `{"scores":[1]}`}
	if _, err := NewLLMReranker(client2).Rerank(context.Background(), "q", []string{"甲", "乙"}); err == nil {
		t.Fatal("长度不符应报错")
	}
	// 分数越界 → 报错
	clientBad := &mockLLM{chat: `{"scores":[11, 0]}`}
	if _, err := NewLLMReranker(clientBad).Rerank(context.Background(), "q", []string{"甲", "乙"}); err == nil {
		t.Fatal("越界分数应报错")
	}
	// 围栏/噪声容错
	client3 := &mockLLM{chat: "好的\n```json\n{\"scores\":[10, 0]}\n```\n以上"}
	if scores, err := NewLLMReranker(client3).Rerank(context.Background(), "q", []string{"甲", "乙"}); err != nil || scores[0] != 10 {
		t.Errorf("围栏容错失败: %v %v", scores, err)
	}
	// 字符串分数容错
	client4 := &mockLLM{chat: `{"scores":["8", "2"]}`}
	if scores, err := NewLLMReranker(client4).Rerank(context.Background(), "q", []string{"甲", "乙"}); err != nil || scores[0] != 8 {
		t.Errorf("字符串分数容错失败: %v %v", scores, err)
	}
	// 空候选
	if scores, err := NewLLMReranker(client).Rerank(context.Background(), "q", nil); err != nil || scores != nil {
		t.Errorf("空候选 = %v, %v", scores, err)
	}
}

func TestRetrieverRerankReorders(t *testing.T) {
	s := testStore(t)
	if err := s.UpsertDoc("d1", "t", "s", "", []ChunkRecord{
		{Text: "甲：图书馆开放时间", Vec: UnitVec(0), ParentIdx: -1},
		{Text: "乙：转专业绩点要求 3.0", Vec: UnitVec(0), ParentIdx: -1},
	}); err != nil {
		t.Fatal(err)
	}
	// BM25 命中乙，向量两路都返回 → RRF 首位乙；候选按融合序为 [乙,甲]，
	// rerank 分数 [1,10] 把甲提到最前
	client := &mockLLM{
		chat:  `{"scores":[1, 10]}`,
		embed: func(texts []string) [][]float64 { return [][]float64{UnitVec(0)} },
	}
	r := NewRetriever(s, 1, client).WithReranker(NewLLMReranker(client))
	hits, err := r.Search(context.Background(), "转专业绩点", 1)
	if err != nil {
		t.Fatal(err)
	}
	if len(hits) != 1 || !strings.Contains(hits[0].Text, "甲") {
		t.Fatalf("rerank 后首位应为甲: %+v", hits)
	}
	// rerank 解析失败 → 退回 RRF 顺序不阻断
	clientFail := &mockLLM{chat: "不是JSON", embed: func(texts []string) [][]float64 { return [][]float64{UnitVec(0)} }}
	r2 := NewRetriever(s, 1, clientFail).WithReranker(NewLLMReranker(clientFail))
	hits2, err := r2.Search(context.Background(), "转专业绩点", 1)
	if err != nil || len(hits2) != 1 || !strings.Contains(hits2[0].Text, "乙") {
		t.Fatalf("rerank 失败应退回粗排首位乙: %v %v", hits2, err)
	}
}

func TestRetrieverParentExpansionDedup(t *testing.T) {
	s := testStore(t)
	// 一个父块 + 两个子块，两个子块都会被召回 → 扩展后只应出现 1 条父块命中
	parentText := "【转专业 > 申请条件】绩点不低于 3.0，且无不及格课程记录。"
	childA := "绩点不低于 3.0。"
	childB := "无不及格课程记录，艺术类原则上不得互转。"
	if err := s.UpsertDoc("d1", "转专业管理办法", "教务处", "", []ChunkRecord{
		{Text: parentText, SectionPath: "转专业 > 申请条件", IsParent: true, ParentIdx: -1, Vec: []float64{1, 0}},
		{Text: childA, SectionPath: "转专业 > 申请条件", IsParent: false, ParentIdx: 0, Vec: []float64{1, 0}},
		{Text: childB, SectionPath: "转专业 > 申请条件", IsParent: false, ParentIdx: 0, Vec: UnitVec(1)},
	}); err != nil {
		t.Fatal(err)
	}
	client := &mockLLM{embed: func(texts []string) [][]float64 { return [][]float64{UnitVec(0)} }}
	r := NewRetriever(s, 5, client)
	hits, err := r.Search(context.Background(), "绩点 不及格", 5)
	if err != nil {
		t.Fatal(err)
	}
	if len(hits) != 1 {
		t.Fatalf("同父多子块应去重为一条父块命中: %+v", hits)
	}
	if hits[0].Text != parentText {
		t.Errorf("应回取父块文本: %q", hits[0].Text)
	}
	if hits[0].SectionPath != "转专业 > 申请条件" {
		t.Errorf("section_path = %q", hits[0].SectionPath)
	}
	if hits[0].Title != "转专业管理办法" || hits[0].Source != "教务处" {
		t.Errorf("父块命中应带文档元信息: %+v", hits[0])
	}
}

func TestRetrieverFlatModeUnchanged(t *testing.T) {
	s := testStore(t)
	// flat 模式：parent_id 为空 → 命中即子块本身，逐字与旧行为一致
	if err := s.UpsertDoc("d1", "图书馆管理办法", "图书馆", "", []ChunkRecord{
		{Text: "图书馆开放时间为 7:30 至 22:30。", Vec: UnitVec(0), ParentIdx: -1},
		{Text: "本科生外借上限 10 册，借期 30 天。", Vec: UnitVec(0), ParentIdx: -1},
	}); err != nil {
		t.Fatal(err)
	}
	client := &mockLLM{embed: func(texts []string) [][]float64 { return [][]float64{UnitVec(0)} }}
	r := NewRetriever(s, 5, client)
	hits, err := r.Search(context.Background(), "图书馆开放时间", 5)
	if err != nil {
		t.Fatal(err)
	}
	if len(hits) != 2 {
		t.Fatalf("flat 模式应有 2 条命中: %+v", hits)
	}
	if hits[0].Text != "图书馆开放时间为 7:30 至 22:30。" {
		t.Errorf("flat 命中应为子块原文: %q", hits[0].Text)
	}
	if hits[0].SectionPath != "" {
		t.Errorf("flat 模式 section_path 应为空: %q", hits[0].SectionPath)
	}
}
