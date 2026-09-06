package agent

import (
	"context"
	"path/filepath"
	"strings"
	"testing"
)

// ---------- P7+：上下文补全（多轮指代消解） ----------

// seedContextMem 构造带一轮对话历史（可选带事实）的记忆库并挂到 d 上。
func seedContextMem(t *testing.T, d *Deps, withFact bool) *MemoryStore {
	t.Helper()
	mem, err := OpenMemory(filepath.Join(t.TempDir(), "memory.db"))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = mem.Close() })
	if err := mem.AppendEpisode("s-ctx", "u1", "user", "转专业有哪些申请条件？"); err != nil {
		t.Fatal(err)
	}
	if err := mem.AppendEpisode("s-ctx", "u1", "assistant", "申请时已修课程平均绩点（GPA）不低于 3.0，且无不及格课程记录。"); err != nil {
		t.Fatal(err)
	}
	if withFact {
		if err := mem.UpsertFacts("u1", []Fact{{Kind: "profile", Key: "gpa", Value: "3.8"}}); err != nil {
			t.Fatal(err)
		}
	}
	d.Memory = mem
	return mem
}

func TestResolveQueryRewritesAnaphora(t *testing.T) {
	sl := &scriptedLLM{resps: []string{
		`{"rewritten":"钱塘大学转专业申请条件的第二条具体是什么？"}`,
	}}
	d := depsWithLLM(t, sl)
	seedContextMem(t, d, false)

	resolved, ok := d.ResolveQuery(context.Background(), "那第二条具体是什么？", "u1", "s-ctx")
	if !ok || resolved != "钱塘大学转专业申请条件的第二条具体是什么？" {
		t.Fatalf("resolved = %q, ok = %v", resolved, ok)
	}
	// 补全 prompt 应带最近对话（指代消解的依据）
	if !strings.Contains(sl.chatLog()[0][1], "转专业有哪些申请条件") {
		t.Errorf("补全 prompt 应包含对话历史: %q", sl.chatLog()[0][1])
	}
}

func TestResolveQueryInjectsFactsForMySituation(t *testing.T) {
	sl := &scriptedLLM{resps: []string{
		`{"rewritten":"绩点3.8的学生符合钱塘大学转专业申请条件吗？"}`,
	}}
	d := depsWithLLM(t, sl)
	seedContextMem(t, d, true)

	resolved, ok := d.ResolveQuery(context.Background(), "我的情况符合转专业要求吗", "u1", "s-ctx")
	if !ok {
		t.Fatal("应触发补全")
	}
	// "我的情况"类指代要靠用户事实补全
	if !strings.Contains(sl.chatLog()[0][1], "gpa：3.8") {
		t.Errorf("补全 prompt 应包含用户事实: %q", sl.chatLog()[0][1])
	}
	if resolved == "我的情况符合转专业要求吗" {
		t.Error("应返回补全后的问题")
	}
}

func TestResolveQueryGates(t *testing.T) {
	// 门控 1：开关关闭
	sl := &scriptedLLM{}
	d := depsWithLLM(t, sl)
	seedContextMem(t, d, false)
	d.Settings.QueryRewrite = "off"
	if _, ok := d.ResolveQuery(context.Background(), "那第二条是什么", "u1", "s-ctx"); ok {
		t.Error("QUERY_REWRITE=off 不应补全")
	}
	d.Settings.QueryRewrite = "on"

	// 门控 2：无对话历史（单轮会话零成本，不调 LLM）
	sl2 := &scriptedLLM{}
	d2 := depsWithLLM(t, sl2)
	seedContextMem(t, d2, false)
	if _, ok := d2.ResolveQuery(context.Background(), "那第二条是什么", "u1", "s-empty"); ok {
		t.Error("无历史会话不应补全")
	}
	if len(sl2.chatLog()) != 0 {
		t.Errorf("不应发生 LLM 调用: %d", len(sl2.chatLog()))
	}

	// 门控 3：无指代信号词
	sl3 := &scriptedLLM{}
	d3 := depsWithLLM(t, sl3)
	seedContextMem(t, d3, false)
	if _, ok := d3.ResolveQuery(context.Background(), "图书馆几点开门", "u1", "s-ctx"); ok {
		t.Error("自包含问题不应补全")
	}
	if len(sl3.chatLog()) != 0 {
		t.Errorf("不应发生 LLM 调用: %d", len(sl3.chatLog()))
	}

	// 门控 4：无记忆库（Memory=nil）
	sl4 := &scriptedLLM{}
	d4 := depsWithLLM(t, sl4)
	if _, ok := d4.ResolveQuery(context.Background(), "那第二条是什么", "u1", "s-ctx"); ok {
		t.Error("无记忆库不应补全")
	}
}

func TestResolveQueryFailureFallbacks(t *testing.T) {
	// LLM 输出非法 JSON → 原样返回
	sl := &scriptedLLM{resps: []string{"这不是JSON"}}
	d := depsWithLLM(t, sl)
	seedContextMem(t, d, false)
	resolved, ok := d.ResolveQuery(context.Background(), "那第二条是什么", "u1", "s-ctx")
	if ok || resolved != "那第二条是什么" {
		t.Fatalf("解析失败应回退原问题: %q %v", resolved, ok)
	}

	// LLM 调用报错 → 原样返回
	d2 := depsWithLLMer(t, &failingLLM{})
	seedContextMem(t, d2, false)
	resolved2, ok2 := d2.ResolveQuery(context.Background(), "那第二条是什么", "u1", "s-ctx")
	if ok2 || resolved2 != "那第二条是什么" {
		t.Fatalf("调用失败应回退原问题: %q %v", resolved2, ok2)
	}

	// 补全输出超长 → 保守回退
	long := strings.Repeat("问题", 400)
	sl3 := &scriptedLLM{resps: []string{`{"rewritten":"` + long + `"}`}}
	d3 := depsWithLLM(t, sl3)
	seedContextMem(t, d3, false)
	if _, ok := d3.ResolveQuery(context.Background(), "那第二条是什么", "u1", "s-ctx"); ok {
		t.Error("超长补全应回退")
	}
}

func TestPipelineRoutesAndSearchesWithResolvedQuery(t *testing.T) {
	// 集成：多轮追问 → 补全 → 路由吃到补全后的问题；route 事件标注补全。
	// scriptedLLM 调用序：①ResolveQuery ②L1 分类 ③rag 改写（Search 内）。
	sl := &scriptedLLM{
		resps: []string{
			`{"rewritten":"钱塘大学转专业申请条件的第二条具体是什么？"}`,                                                                     // ① 补全
			`{"scores":{"factual":0.95,"research":0.02,"transaction":0.01,"hybrid":0.01,"refusal":0.01},"reason":"单点"}`, // ② L1
			`改写结果`, // ③ rag 查询改写（内容不重要）
		},
		last:   `改写结果`,
		stream: "第二条为 GPA 不低于 3.0[1]。",
	}
	d := depsWithLLM(t, sl)
	seedContextMem(t, d, false)

	events := ask(t, d, "s-ctx", "那第二条具体是什么？", "student")

	// L1 分类的 user 输入应是补全后的问题（证明路由用补全问题）
	if !strings.Contains(sl.chatLog()[1][1], "申请条件的第二条") {
		t.Errorf("L1 应收到补全后的问题: %q", sl.chatLog()[1][1])
	}
	// route 事件 reason 标注补全
	var route routeEvent
	for _, ev := range events {
		if r, ok := ev.(routeEvent); ok {
			route = r
		}
	}
	if !strings.Contains(route.Reason, "补全指代") {
		t.Errorf("route reason 应标注补全: %q", route.Reason)
	}
	// 正常流式作答 + 引用
	if !strings.Contains(answerText(events), "3.0") {
		t.Errorf("answer = %q", answerText(events))
	}
	if cites := eventsOf(events, "citations"); len(cites) != 1 {
		t.Errorf("citations 事件数 = %d", len(cites))
	}
}

func TestConsolidateEpisodicSyncVisibleNextTurn(t *testing.T) {
	// episodic 原文同步落库：consolidateAsync 返回后立即可见（评测第二轮依赖此语义）
	sl := &scriptedLLM{resps: []string{`{"facts":[]}`}}
	d := depsWithLLM(t, sl)
	mem, err := OpenMemory(filepath.Join(t.TempDir(), "memory.db"))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = mem.Close() })
	d.Memory = mem

	d.consolidateAsync(context.Background(), "u1", "s-sync", "转专业绩点要求", "不低于 3.0。")
	episodes, err := mem.RecentEpisodes("s-sync", 10)
	if err != nil || len(episodes) != 2 {
		t.Fatalf("episodic 应同步可见: %v %v", episodes, err)
	}
	if !strings.HasPrefix(episodes[0], "用户：转专业绩点要求") {
		t.Errorf("episodes = %v", episodes)
	}
}
