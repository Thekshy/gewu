package agent

import (
	"context"
	"errors"
	"fmt"
	"path/filepath"
	"strings"
	"sync"
	"testing"

	"gewu/internal/business"
	"gewu/internal/config"
	"gewu/internal/llm"
	"gewu/internal/rag"
)

// ---------- mock：脚本化 LLM（不发真实请求），同时满足 agent.LLMer 与 rag.LLMer ----------

type scriptedLLM struct {
	mu     sync.Mutex
	resps  []string // Chat 按序弹出；耗尽后返回 last
	last   string
	stream string // ChatStream 的固定输出
	// streamFinish：ChatStream 返回的结束原因（P10 截断链路注入 "length" 用）。
	streamFinish string
	embedV       []float64
	chats        [][2]string // 记录 [system, user]

	// comps：ChatWithTools（原生 tool-calling）按序弹出的补全；耗尽后返回
	// &Completion{Content: last}（即"不再调用工具，直接给最终回答"）。
	comps []*llm.Completion
	// compMsgs：ChatWithTools 每次收到的完整消息（P10-2 截断回填断言用）。
	compMsgs [][]llm.Message
}

func (s *scriptedLLM) HasKey() bool { return true }

func (s *scriptedLLM) Chat(_ context.Context, messages []llm.Message, _ llm.Options) (string, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	sys, usr := "", ""
	if len(messages) > 0 {
		sys = messages[0].Content
	}
	if len(messages) > 1 {
		usr = messages[len(messages)-1].Content
	}
	s.chats = append(s.chats, [2]string{sys, usr})
	if len(s.resps) == 0 {
		return s.last, nil
	}
	r := s.resps[0]
	if len(s.resps) > 1 {
		s.resps = s.resps[1:]
	} else {
		s.last = r
	}
	return r, nil
}

// ChatWithTools 原生工具调用的脚本化实现：按序弹 comps。
func (s *scriptedLLM) ChatWithTools(_ context.Context, messages []llm.Message, _ llm.Options, tools []llm.ToolDef) (*llm.Completion, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.compMsgs = append(s.compMsgs, append([]llm.Message{}, messages...))
	sys, usr := "", ""
	if len(messages) > 0 {
		sys = messages[0].Content
	}
	if len(messages) > 1 {
		usr = messages[len(messages)-1].Content
	}
	s.chats = append(s.chats, [2]string{sys, usr + fmt.Sprintf(" [tools=%d]", len(tools))})
	if len(s.comps) == 0 {
		return &llm.Completion{Content: s.last}, nil
	}
	c := s.comps[0]
	if len(s.comps) > 1 {
		s.comps = s.comps[1:]
	} else {
		s.last = c.Content
	}
	return c, nil
}

// chatLog 锁保护地复制调用记录（异步记忆固化 goroutine 可能仍在写）。
func (s *scriptedLLM) chatLog() [][2]string {
	s.mu.Lock()
	defer s.mu.Unlock()
	return append([][2]string{}, s.chats...)
}

func (s *scriptedLLM) ChatStream(_ context.Context, _ []llm.Message, _ llm.Options, onDelta func(string) error) (string, error) {
	if err := onDelta(s.stream); err != nil {
		return "", err
	}
	return s.streamFinish, nil
}

func (s *scriptedLLM) Embed(_ context.Context, texts []string) ([][]float64, error) {
	out := make([][]float64, len(texts))
	for i := range out {
		out[i] = s.embedV
	}
	return out, nil
}

// depsWithLLM 构造带向量索引与脚本 LLM 的完整编排依赖（sl 为 nil 时用空脚本）。
func depsWithLLM(t *testing.T, sl *scriptedLLM) *Deps {
	t.Helper()
	if sl == nil {
		sl = &scriptedLLM{}
	}
	sl.embedV = []float64{1, 0}
	return depsWithLLMer(t, sl)
}

// depsWithLLMer 任意 LLMer 实现（failingLLM 等无 embed 能力的 mock）版本的装配。
func depsWithLLMer(t *testing.T, lc LLMer) *Deps {
	t.Helper()
	store, err := rag.Open(filepath.Join(t.TempDir(), "index.db"))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = store.Close() })
	if err := store.UpsertDoc("d1", "转专业管理办法", "教务处", "", []rag.ChunkRecord{
		{Text: "申请转专业要求绩点不低于 3.0，且无不及格课程记录。", Vec: []float64{1, 0}, ParentIdx: -1},
	}); err != nil {
		t.Fatal(err)
	}
	biz, err := business.Open(filepath.Join(t.TempDir(), "biz.db"))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = biz.Close() })
	s := config.Default()
	r := rag.NewRetriever(store, s.RetrievalK, lc)
	d := NewDeps(s, lc, r, biz, nil)
	if err := d.Business.Reset(); err != nil {
		t.Fatal(err)
	}
	return d
}

// ---------- P7-1：办理工具识别收紧（预约心理咨询不再误入场馆流） ----------

func TestDetectToolVenueGuard(t *testing.T) {
	cases := []struct {
		q    string
		want string
	}{
		{"我想预约一次心理咨询", ""},              // 负向：裸"预约X"不再误选 book_venue
		{"帮我预约一次挂号", ""},                // 负向：就医类
		{"帮我预约明天晚上的羽毛球馆", "book_venue"}, // 回归：场馆办理仍命中
		{"帮我订个研讨间301", "book_venue"},    // 回归：研讨间仍命中
		{"帮我提交明天的事假", "submit_leave"},   // 回归：请假优先
		{"现在有哪些场馆可以预约", "query_venues"}, // 回归：读查询
	}
	for _, c := range cases {
		if got := DetectTool(c.q); got != c.want {
			t.Errorf("DetectTool(%q) = %q, want %q", c.q, got, c.want)
		}
	}
}

func TestStartFlowConsultFallsBackToKnowledge(t *testing.T) {
	sl := &scriptedLLM{resps: []string{
		`{"tool":"book_venue"}`, // LLM 无视提示词强行挑工具（真实 GLM 观测到的行为）
	}, stream: "心理咨询相关资料[1]。"}
	d := depsWithLLM(t, sl)
	events := ask(t, d, "s-consult", "我想预约一次心理咨询", "student")
	// 不应出现 book_venue 的"想预约哪个场馆"追问——LLM 强选也会被负向双保险拦截
	for _, ev := range events {
		if sq, ok := ev.(slotQuestionEvent); ok && sq.Slot == "venue" {
			t.Fatalf("不应进入场馆办理流: %+v", events)
		}
	}
	ans := answerText(events)
	if !strings.Contains(ans, "转知识库检索") {
		t.Errorf("应走 fallbackKnowledge 转知识库: %q", ans)
	}
	if !strings.Contains(ans, "心理咨询相关资料") {
		t.Errorf("应由 RAG 流式作答: %q", ans)
	}
	var cites citationsEvent
	for _, ev := range events {
		if c, ok := ev.(citationsEvent); ok {
			cites = c
		}
	}
	if len(cites.Items) == 0 {
		t.Errorf("citations 应非空: %+v", cites.Items)
	}
}

// ---------- P7-4：ReAct 到顶兜底不留裸错误 / 记忆 Facts 上限 ----------

func TestReActMaxTurnsConvergenceFailureGivesPartialAnswer(t *testing.T) {
	// 8 轮读工具循环耗尽；第 9 次（强制收敛）ChatWithTools 报错 →
	// 用已累积 observations 组织部分结论，不留裸错误。
	sl := &scriptedLLM{}
	loop := toolCall("c1", "query_venues", `{}`)
	for i := 0; i < reactMaxTurns; i++ {
		sl.comps = append(sl.comps, loop)
	}
	d := reActDeps(t, sl)
	d.LLM = &flakyAfterQueue{inner: sl, failAfter: reactMaxTurns}
	var events []any
	err := d.RunReAct(context.Background(), func(ev any) error { events = append(events, ev); return nil },
		"把所有场馆信息给我", "student", "u1", "s1", nil)
	if err != nil {
		t.Fatalf("有中间结果时到顶不应返回错误: %v", err)
	}
	ans := answerText(events)
	if !strings.Contains(ans, "暂未完全办成") || !strings.Contains(ans, "query_venues") {
		t.Errorf("应用已累积观察组织部分结论: %q", ans)
	}
}

func TestReActMaxTurnsNoObservationsReturnsError(t *testing.T) {
	// 首次 ChatWithTools 即失败且无任何中间结果 → 返回 error
	d := reActDeps(t, nil)
	d.LLM = &flakyAlways{}
	err := d.RunReAct(context.Background(), func(ev any) error { return nil },
		"随便", "student", "u1", "s1", nil)
	if err == nil {
		t.Fatal("无任何可用中间结果时应返回 error")
	}
}

// flakyAfterQueue 前 n 次 Chat 正常走 inner（脚本队列），之后返回错误。
type flakyAfterQueue struct {
	inner     *scriptedLLM
	failAfter int
	calls     int
}

func (f *flakyAfterQueue) HasKey() bool { return true }

func (f *flakyAfterQueue) Chat(ctx context.Context, m []llm.Message, o llm.Options) (string, error) {
	f.calls++
	if f.calls > f.failAfter {
		return "", errors.New("收敛调用失败")
	}
	return f.inner.Chat(ctx, m, o)
}

func (f *flakyAfterQueue) ChatStream(ctx context.Context, m []llm.Message, o llm.Options, onDelta func(string) error) (string, error) {
	return f.inner.ChatStream(ctx, m, o, onDelta)
}

func (f *flakyAfterQueue) Embed(ctx context.Context, texts []string) ([][]float64, error) {
	return f.inner.Embed(ctx, texts)
}

func (f *flakyAfterQueue) ChatWithTools(ctx context.Context, m []llm.Message, o llm.Options, tools []llm.ToolDef) (*llm.Completion, error) {
	return f.inner.ChatWithTools(ctx, m, o, tools)
}

// flakyAlways 每次 Chat 都失败。
type flakyAlways struct{}

func (flakyAlways) HasKey() bool { return true }

func (flakyAlways) Chat(context.Context, []llm.Message, llm.Options) (string, error) {
	return "", errors.New("网络错误")
}

func (flakyAlways) ChatStream(context.Context, []llm.Message, llm.Options, func(string) error) (string, error) {
	return "", errors.New("网络错误")
}

func (flakyAlways) Embed(context.Context, []string) ([][]float64, error) {
	return nil, errors.New("网络错误")
}

func (flakyAlways) ChatWithTools(context.Context, []llm.Message, llm.Options, []llm.ToolDef) (*llm.Completion, error) {
	return nil, errors.New("网络错误")
}

func TestMemoryBlockFactsCapped(t *testing.T) {
	d := testDeps(t)
	mem, err := OpenMemory(filepath.Join(t.TempDir(), "memory.db"))
	if err != nil {
		t.Fatal(err)
	}
	defer mem.Close()
	d.Memory = mem

	// 直写 25 条带递增时间戳的事实（绕过 datetime('now') 的秒级粒度）
	for i := 0; i < 25; i++ {
		_, err := mem.db.Exec(
			`INSERT INTO memory_fact (user_id, kind, key, value, updated_at)
			 VALUES (?, 'profile', ?, ?, ?)`,
			"u1", fmt.Sprintf("fact%02d", i), fmt.Sprintf("v%02d", i),
			fmt.Sprintf("2026-09-01 00:00:%02d", i))
		if err != nil {
			t.Fatal(err)
		}
	}
	block := d.memoryBlock("u1", "s1")
	if strings.Count(block, "- profile/fact") != maxFactsInContext {
		t.Errorf("应只注入最近 %d 条: %d", maxFactsInContext, strings.Count(block, "- profile/fact"))
	}
	// 最新固化的保留，最旧的被挤出 prompt（仍在库）
	if !strings.Contains(block, "fact24") || strings.Contains(block, "fact04") {
		t.Errorf("应注入最新 20 条（含 fact24、不含 fact04）: %s", block)
	}
	facts, _ := mem.Facts("u1")
	if len(facts) != 25 {
		t.Errorf("库中仍应保留全部 25 条: %d", len(facts))
	}
}

// ---------- P6 阶段2：级联路由 ----------

func TestRouteEventCarriesLayer(t *testing.T) {
	d := depsWithLLM(t, nil)
	sl := &scriptedLLM{
		resps: []string{
			`{"scores":{"factual":0.95,"research":0.02,"transaction":0.01,"hybrid":0.01,"refusal":0.01},"reason":"单点"}`,
		},
		stream: "依据资料回答[1]。",
	}
	d.LLM = sl
	var routeEv routeEvent
	events := ask(t, d, "s-layer", "转专业绩点要求", "student")
	for _, ev := range events {
		if e, ok := ev.(routeEvent); ok {
			routeEv = e
		}
	}
	if routeEv.Layer != "L1-llm" || !routeEv.ByLLM || routeEv.Conf == nil || *routeEv.Conf != 0.95 {
		t.Fatalf("route 事件 = %+v", routeEv)
	}
	if !strings.Contains(answerText(events), "依据资料回答") {
		t.Errorf("answer = %q", answerText(events))
	}
}

// ---------- P6 阶段4：长期记忆 ----------

func TestMemoryFactUpsertOverwrite(t *testing.T) {
	mem, err := OpenMemory(filepath.Join(t.TempDir(), "memory.db"))
	if err != nil {
		t.Fatal(err)
	}
	defer mem.Close()
	if err := mem.UpsertFacts("u1", []Fact{{Kind: "profile", Key: "major", Value: "机械工程"}}); err != nil {
		t.Fatal(err)
	}
	if err := mem.UpsertFacts("u1", []Fact{{Kind: "profile", Key: "major", Value: "计算机科学"}}); err != nil {
		t.Fatal(err)
	}
	facts, err := mem.Facts("u1")
	if err != nil {
		t.Fatal(err)
	}
	if len(facts) != 1 || facts[0].Value != "计算机科学" {
		t.Fatalf("facts = %+v, want 同 key 只留最新值", facts)
	}
}

func TestConsolidateExtractsFacts(t *testing.T) {
	d := testDeps(t)
	mem, err := OpenMemory(filepath.Join(t.TempDir(), "memory.db"))
	if err != nil {
		t.Fatal(err)
	}
	defer mem.Close()
	d.Memory = mem
	d.LLM = &scriptedLLM{resps: []string{
		`{"facts":[{"kind":"profile","key":"major","value":"计算机专业"},{"kind":"profile","key":"gpa","value":"3.8"}]}`,
	}}
	if err := d.Consolidate(context.Background(), "u1", "s1", "我是计算机专业的，绩点 3.8", "好的，已记录。"); err != nil {
		t.Fatal(err)
	}
	facts, _ := mem.Facts("u1")
	if len(facts) != 2 {
		t.Fatalf("facts = %+v", facts)
	}
	episodes, _ := mem.RecentEpisodes("s1", 10)
	if len(episodes) != 2 || !strings.HasPrefix(episodes[0], "用户：") || !strings.HasPrefix(episodes[1], "助手：") {
		t.Fatalf("episodes = %v", episodes)
	}
}

func TestConsolidateFailureDoesNotAffectMainChain(t *testing.T) {
	d := testDeps(t)
	mem, err := OpenMemory(filepath.Join(t.TempDir(), "memory.db"))
	if err != nil {
		t.Fatal(err)
	}
	defer mem.Close()
	d.Memory = mem
	d.LLM = &failingLLM{}
	if err := d.Consolidate(context.Background(), "u1", "s1", "问题", "回答"); err == nil {
		t.Fatal("LLM 失败应返回错误（异步路径只记日志）")
	}
	// episodic 原文已留存
	episodes, _ := mem.RecentEpisodes("s1", 10)
	if len(episodes) != 2 {
		t.Fatalf("episodic 应先落库: %v", episodes)
	}
}

type failingLLM struct{}

func (failingLLM) HasKey() bool { return true }

func (failingLLM) Chat(context.Context, []llm.Message, llm.Options) (string, error) {
	return "", errors.New("网络错误")
}

func (failingLLM) ChatStream(context.Context, []llm.Message, llm.Options, func(string) error) (string, error) {
	return "", errors.New("网络错误")
}

func (failingLLM) Embed(context.Context, []string) ([][]float64, error) {
	return nil, errors.New("网络错误")
}

func (failingLLM) ChatWithTools(context.Context, []llm.Message, llm.Options, []llm.ToolDef) (*llm.Completion, error) {
	return nil, errors.New("网络错误")
}

func TestAssembleMessagesMemoryInjection(t *testing.T) {
	d := testDeps(t)
	mem, err := OpenMemory(filepath.Join(t.TempDir(), "memory.db"))
	if err != nil {
		t.Fatal(err)
	}
	defer mem.Close()

	// 无记忆：两条消息，与历史版本逐字一致
	msgs := d.assembleMessages("u1", "s1", "问题", "资料")
	if len(msgs) != 2 {
		t.Fatalf("无记忆消息数 = %d", len(msgs))
	}
	// 有事实：system → 记忆 → user
	if err := mem.UpsertFacts("u1", []Fact{{Kind: "profile", Key: "gpa", Value: "3.8"}}); err != nil {
		t.Fatal(err)
	}
	d.Memory = mem
	msgs = d.assembleMessages("u1", "s1", "我符合转专业要求吗", "资料")
	if len(msgs) != 3 {
		t.Fatalf("有记忆消息数 = %d", len(msgs))
	}
	if msgs[0].Role != "system" || msgs[1].Role != "system" || msgs[2].Role != "user" {
		t.Fatalf("消息角色序 = %v", msgs)
	}
	if !strings.Contains(msgs[1].Content, "已知用户信息") || !strings.Contains(msgs[1].Content, "3.8") {
		t.Errorf("记忆块 = %q", msgs[1].Content)
	}
}

// ---------- agent-first：原生 tool-calling 的 ReAct 引擎 ----------

// toolCall 构造一次原生工具调用补全（测试 DSL）。
func toolCall(id, name, argsJSON string) *llm.Completion {
	return &llm.Completion{ToolCalls: []llm.ToolCall{{ID: id, Name: name, Arguments: argsJSON}}}
}

func finalAnswer(text string) *llm.Completion {
	return &llm.Completion{Content: text}
}

func reActDeps(t *testing.T, sl *scriptedLLM) *Deps {
	t.Helper()
	return depsWithLLM(t, sl)
}

func TestReActToolsThenFinal(t *testing.T) {
	sl := &scriptedLLM{comps: []*llm.Completion{
		toolCall("c1", "query_venues", `{"date":"2026-09-06"}`),
		finalAnswer("2026-09-06 可预约场馆包括羽毛球馆、游泳馆。"),
	}}
	d := reActDeps(t, sl)
	var events []any
	err := d.RunReAct(context.Background(), func(ev any) error { events = append(events, ev); return nil },
		"帮我看看 9 月 6 日有什么能约的", "student", "u1", "s1", nil)
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(answerText(events), "羽毛球馆") {
		t.Errorf("answer = %q", answerText(events))
	}
	if n := len(eventsOf(events, "status")); n != 1 {
		t.Fatalf("应恰好一次工具调用 status: %d", n)
	}
	last := events[len(events)-1].(citationsEvent)
	if last.Type != "citations" {
		t.Errorf("末事件 = %v", last)
	}
}

func TestReActRepeatFingerprintConverges(t *testing.T) {
	same := toolCall("c1", "parse_date", `{"text":"明天"}`)
	sl := &scriptedLLM{comps: []*llm.Completion{
		same, same, same, // 第 3 次被拒
		finalAnswer("明天已解析过，不再重复。"),
	}}
	d := reActDeps(t, sl)
	var events []any
	err := d.RunReAct(context.Background(), func(ev any) error { events = append(events, ev); return nil },
		"明天是几号", "student", "u1", "s1", nil)
	if err != nil {
		t.Fatal(err)
	}
	statuses := eventsOf(events, "status")
	if len(statuses) != 2 {
		t.Fatalf("parse_date 应只执行 2 次: %d", len(statuses))
	}
	if !strings.Contains(answerText(events), "不再重复") {
		t.Errorf("answer = %q", answerText(events))
	}
}

func TestReActMaxTurnsFallback(t *testing.T) {
	loop := toolCall("c1", "query_venues", `{}`)
	var comps []*llm.Completion
	for i := 0; i < reactMaxTurns; i++ {
		comps = append(comps, loop)
	}
	comps = append(comps, finalAnswer("根据已查到的信息：场馆均可预约。"))
	sl := &scriptedLLM{comps: comps}
	d := reActDeps(t, sl)
	var events []any
	err := d.RunReAct(context.Background(), func(ev any) error { events = append(events, ev); return nil },
		"把所有场馆信息给我", "student", "u1", "s1", nil)
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(answerText(events), "根据已查到的信息") {
		t.Errorf("maxTurns 兜底应给出结论: %q", answerText(events))
	}
}

func TestReActToolErrorFedBack(t *testing.T) {
	sl := &scriptedLLM{comps: []*llm.Completion{
		// 必填参数缺失的写调用 → observation 提示收集
		toolCall("c1", "book_venue", `{"venue":"羽毛球馆"}`),
		// 不存在的工具 → observation
		toolCall("c2", "no_such_tool", `{}`),
		finalAnswer("请提供预约的日期和时段。"),
	}}
	d := reActDeps(t, sl)
	var events []any
	err := d.RunReAct(context.Background(), func(ev any) error { events = append(events, ev); return nil },
		"帮我预约", "student", "u1", "s1", nil)
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(answerText(events), "请提供预约的日期和时段") {
		t.Errorf("observation 回填后应继续收敛: %q", answerText(events))
	}
	// 两次失败都以 observation 回填给后续轮次
	if n := len(sl.chatLog()); n != 3 {
		t.Fatalf("LLM 调用次数 = %d", n)
	}
	if !strings.Contains(sl.chatLog()[1][1], "缺少必填参数") {
		t.Errorf("缺参应回填 observation: %q", sl.chatLog()[1][1])
	}
	if !strings.Contains(sl.chatLog()[2][1], "工具不存在") {
		t.Errorf("未知工具应提示: %q", sl.chatLog()[2][1])
	}
}

func TestReActToolsetConstraint(t *testing.T) {
	d := reActDeps(t, nil)
	tools := d.reactTools("student", "u1", []string{"book_venue"}, &reactState{})
	if _, ok := tools["query_venues"]; ok {
		t.Error("toolset 未包含的工具应被排除")
	}
	if _, ok := tools["book_venue"]; !ok {
		t.Error("toolset 内的工具应保留")
	}
	if _, ok := tools["search_knowledge"]; !ok {
		t.Error("search_knowledge 始终可用")
	}
	// 辅导员独占工具受角色权限约束（即使 toolset 放行）
	tools2 := d.reactTools("student", "u1", []string{"approve_leave"}, &reactState{})
	if _, ok := tools2["approve_leave"]; ok {
		t.Error("角色越权工具不应出现")
	}
	// 空 toolset 不限制（角色内全部）
	tools3 := d.reactTools("counselor", "u1", nil, &reactState{})
	if _, ok := tools3["approve_leave"]; !ok {
		t.Error("counselor 应可见 approve_leave")
	}
}

func TestReActSearchRecordsCitations(t *testing.T) {
	sl := &scriptedLLM{comps: []*llm.Completion{
		toolCall("c1", "search_knowledge", `{"query":"转专业绩点"}`),
		finalAnswer("绩点要求 3.0[1]。"),
	}}
	d := reActDeps(t, sl)
	var events []any
	err := d.RunReAct(context.Background(), func(ev any) error { events = append(events, ev); return nil },
		"转专业绩点要求是多少", "student", "u1", "s1", nil)
	if err != nil {
		t.Fatal(err)
	}
	var cites citationsEvent
	for _, ev := range events {
		if c, ok := ev.(citationsEvent); ok {
			cites = c
		}
	}
	if len(cites.Items) != 1 || cites.Items[0].DocID != "d1" {
		t.Fatalf("citations = %+v", cites.Items)
	}
}

func TestReActWriteToolGoesConfirmFlow(t *testing.T) {
	// agent 发起参数齐全的写调用 → pending_action 确认流；下一轮「确认」
	// 由既有 ClassifyReply/HandleReply 确定性接管 → 执行 → 回执。
	sl := &scriptedLLM{comps: []*llm.Completion{
		toolCall("c1", "book_venue", `{"venue":"羽毛球馆","date":"2026-09-08","slot":"19:00-21:00","purpose":"社团活动"}`),
	}}
	d := reActDeps(t, sl)
	d.Settings.RouterMode = "agent-first" // triage 规则快路径 → agent
	events := ask(t, d, "s-confirm", "帮我预约 9 月 8 日晚上 19 点的羽毛球馆", "student")

	var pending *pendingActionEvent
	for _, ev := range events {
		if p, ok := ev.(pendingActionEvent); ok {
			pending = &p
		}
	}
	if pending == nil || pending.Tool != "book_venue" {
		t.Fatalf("应转确认流 pending_action: %+v", events)
	}
	args, _ := pending.Args.MarshalJSON()
	if !strings.Contains(string(args), "羽毛球馆") || !strings.Contains(string(args), "19:00-21:00") {
		t.Errorf("确认摘要 = %s", string(args))
	}
	// 确认摘要后应停留在确认阶段（无 action_result）
	if len(eventsOf(events, "action_result")) != 0 {
		t.Fatal("确认前不应执行")
	}

	// 第二轮：确认 → 执行 → 回执（复用既有确认流机制）
	events2 := ask(t, d, "s-confirm", "确认", "student")
	var ar *actionResultEvent
	for _, ev := range events2 {
		if r, ok := ev.(actionResultEvent); ok {
			ar = &r
		}
	}
	if ar == nil || !ar.Success || ar.Receipt == nil || !strings.HasPrefix(*ar.Receipt, "VE-") {
		t.Fatalf("action_result = %+v", ar)
	}
}

// ---------- agent-first：pipeline 集成（triage 单元测试在 routing 包） ----------

func TestPipelineAgentFirstDispatch(t *testing.T) {
	// direct：triage 后走 AnswerDirect（流式 + 引用）
	sl := &scriptedLLM{resps: []string{
		`{"choice":"direct","reason":"单点查询"}`, // triage
		`查询改写结果`,                              // rag 改写
	}, stream: "依据资料作答[1]。"}
	d := depsWithLLM(t, sl)
	d.Settings.RouterMode = "agent-first"
	d.Settings.QueryRewrite = "off"
	events := ask(t, d, "s-af1", "图书馆几点开门", "student")
	route := firstEvent(events, "route").(routeEvent)
	if route.Route != "factual" || route.Layer != "triage-llm" {
		t.Fatalf("route = %+v", route)
	}
	if !strings.Contains(answerText(events), "依据资料作答") {
		t.Errorf("direct 应走 AnswerDirect 流式: %q", answerText(events))
	}
}

func TestPipelineAgentFirstReActPath(t *testing.T) {
	// agent：triage 后走 RunReAct（原生 tool-calling 调一次读工具后作答）
	sl := &scriptedLLM{resps: []string{
		`{"choice":"agent","reason":"实时业务数据"}`, // triage
	}, comps: []*llm.Completion{
		toolCall("c1", "my_bookings", `{}`),
		finalAnswer("你目前没有有效预约。"),
	}}
	d := depsWithLLM(t, sl)
	d.Settings.RouterMode = "agent-first"
	d.Settings.QueryRewrite = "off"
	events := ask(t, d, "s-af2", "查一下我的预约", "student")
	route := firstEvent(events, "route").(routeEvent)
	if route.Route != "agent" {
		t.Fatalf("route = %+v", route)
	}
	if !strings.Contains(answerText(events), "没有有效预约") {
		t.Errorf("agent 链路应完成工具调用后作答: %q", answerText(events))
	}
	if len(eventsOf(events, "status")) != 1 {
		t.Errorf("应有一次工具调用 status 事件")
	}
}

// ---------- P10-2：ReAct 截断防御（length 先于工具解析） ----------

// truncatedCall 构造一次撞 max_tokens 截断的工具调用补全（测试 DSL）。
func truncatedCall(id, name, argsJSON string) *llm.Completion {
	c := toolCall(id, name, argsJSON)
	c.FinishReason = "length"
	return c
}

// stopCall 构造 finish_reason=stop 的正常工具调用补全。
func stopCall(id, name, argsJSON string) *llm.Completion {
	c := toolCall(id, name, argsJSON)
	c.FinishReason = "stop"
	return c
}

func TestReActTruncatedToolCallsNotExecuted(t *testing.T) {
	// 第一轮截断（length + 不完整参数的读调用）→ 一律不执行、合成 observation
	// 回填；第二轮模型重发完整调用后正常作答。
	sl := &scriptedLLM{comps: []*llm.Completion{
		truncatedCall("c1", "query_venues", `{"date":"2026-09`), // 不完整 JSON
		stopCall("c2", "query_venues", `{"date":"2026-09-08"}`),
		finalAnswer("2026-09-08 可预约场馆包括羽毛球馆、游泳馆。"),
	}}
	d := reActDeps(t, sl)
	var events []any
	err := d.RunReAct(context.Background(), func(ev any) error { events = append(events, ev); return nil },
		"帮我看看 9 月 8 日有什么能约的", "student", "u1", "s1", nil)
	if err != nil {
		t.Fatal(err)
	}
	// 截断轮不产生任何 status（工具 Run 从未被触发，无副作用）；
	// 只有重发那一轮有一次"调用工具"status。
	if n := len(eventsOf(events, "status")); n != 1 {
		t.Fatalf("工具只应执行 1 次（截断轮不执行）: %d", n)
	}
	if !strings.Contains(answerText(events), "羽毛球馆") {
		t.Errorf("第二轮重发后应正常作答: %q", answerText(events))
	}
	// 回填断言：第二轮收到的 messages 末两条 = assistant(带截断的 tool_calls) + tool(合成 observation)
	msgs := sl.compMsgs[1]
	if len(msgs) < 2 {
		t.Fatalf("第二轮 messages = %d 条", len(msgs))
	}
	asst, toolMsg := msgs[len(msgs)-2], msgs[len(msgs)-1]
	if asst.Role != "assistant" || len(asst.ToolCalls) != 1 || asst.ToolCalls[0].ID != "c1" {
		t.Errorf("assistant 原样回填失败: %+v", asst)
	}
	if toolMsg.Role != "tool" || toolMsg.ToolCallID != "c1" ||
		!strings.Contains(toolMsg.Content, "token 上限被截断") {
		t.Errorf("合成 observation = %+v", toolMsg)
	}
}

func TestReActLengthWithoutCallsStillTerminates(t *testing.T) {
	// length 但无 tool_calls：最终回答被截断仍是回答，走既有终止分支
	sl := &scriptedLLM{comps: []*llm.Completion{
		{Content: "答案是 3.0（部分）", FinishReason: "length"},
	}}
	d := reActDeps(t, sl)
	var events []any
	err := d.RunReAct(context.Background(), func(ev any) error { events = append(events, ev); return nil },
		"转专业绩点要求", "student", "u1", "s1", nil)
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(answerText(events), "3.0") {
		t.Errorf("应按终止判据出答案: %q", answerText(events))
	}
}

func TestReActStopWithToolCallsStillExecutes(t *testing.T) {
	// finish_reason=stop + tool_calls：防御只针对 length，正常执行不受影响
	sl := &scriptedLLM{comps: []*llm.Completion{
		stopCall("c1", "query_venues", `{"date":"2026-09-08"}`),
		finalAnswer("场馆均可预约。"),
	}}
	d := reActDeps(t, sl)
	var events []any
	err := d.RunReAct(context.Background(), func(ev any) error { events = append(events, ev); return nil },
		"9 月 8 日有什么场馆", "student", "u1", "s1", nil)
	if err != nil {
		t.Fatal(err)
	}
	if n := len(eventsOf(events, "status")); n != 1 {
		t.Fatalf("stop + tool_calls 应正常执行 1 次: %d", n)
	}
	if !strings.Contains(answerText(events), "场馆均可预约") {
		t.Errorf("answer = %q", answerText(events))
	}
}

// ---------- P10-3：流式截断可见性 + done.reason ----------

// doneOf 取事件流里的 done 事件（要求恰好一个）。
func doneOf(t *testing.T, events []any) doneEvent {
	t.Helper()
	var ds []doneEvent
	for _, ev := range events {
		if d, ok := ev.(doneEvent); ok {
			ds = append(ds, d)
		}
	}
	if len(ds) != 1 {
		t.Fatalf("done 事件数 = %d, want 1: %v", len(ds), events)
	}
	return ds[0]
}

func TestDirectTruncatedMarksMaxTokensDone(t *testing.T) {
	// 直答流式 finish=length：事件序列含截断 status，done.reason=max_tokens，
	// answer 正文不被改动（避免影响评测判分）
	sl := &scriptedLLM{resps: []string{
		`{"scores":{"factual":0.95,"research":0.02,"transaction":0.01,"hybrid":0.01,"refusal":0.01},"reason":"单点"}`,
	}, stream: "部分答案[1]。", streamFinish: "length"}
	d := depsWithLLM(t, sl)
	events := ask(t, d, "s-trunc", "转专业绩点要求", "student")
	var sawTruncStatus bool
	for _, ev := range events {
		if s, ok := ev.(statusEvent); ok && strings.Contains(s.Text, "长度上限") {
			sawTruncStatus = true
		}
	}
	if !sawTruncStatus {
		t.Fatalf("应 emit 截断 status: %v", events)
	}
	if ans := answerText(events); ans != "部分答案[1]。" {
		t.Errorf("answer 正文不应被改动: %q", ans)
	}
	if done := doneOf(t, events); done.Reason != "max_tokens" {
		t.Fatalf("done.reason = %q, want max_tokens", done.Reason)
	}
}

func TestDirectNormalDoneReasonCompleted(t *testing.T) {
	sl := &scriptedLLM{resps: []string{
		`{"scores":{"factual":0.95,"research":0.02,"transaction":0.01,"hybrid":0.01,"refusal":0.01},"reason":"单点"}`,
	}, stream: "依据资料回答[1]。"}
	d := depsWithLLM(t, sl)
	events := ask(t, d, "s-normal", "图书馆几点开门", "student")
	if done := doneOf(t, events); done.Reason != "completed" {
		t.Fatalf("done.reason = %q, want completed", done.Reason)
	}
}

// streamFailLLM Chat 全部转发 inner（路由/检索正常），ChatStream 固定失败——
// 直答链路中途出错的注入点。
type streamFailLLM struct {
	inner *scriptedLLM
}

func (s *streamFailLLM) HasKey() bool { return true }

func (s *streamFailLLM) Chat(ctx context.Context, m []llm.Message, o llm.Options) (string, error) {
	return s.inner.Chat(ctx, m, o)
}

func (s *streamFailLLM) ChatStream(context.Context, []llm.Message, llm.Options, func(string) error) (string, error) {
	return "", errors.New("流式中断")
}

func (s *streamFailLLM) Embed(ctx context.Context, texts []string) ([][]float64, error) {
	return s.inner.Embed(ctx, texts)
}

func (s *streamFailLLM) ChatWithTools(ctx context.Context, m []llm.Message, o llm.Options, tools []llm.ToolDef) (*llm.Completion, error) {
	return s.inner.ChatWithTools(ctx, m, o, tools)
}

func TestErrorPathDoneReasonErrorSingleDone(t *testing.T) {
	// 链路中途失败（直答流式中断）：error 事件后仍恰一个 done，reason=error
	sl := &scriptedLLM{resps: []string{
		`{"scores":{"factual":0.95,"research":0.02,"transaction":0.01,"hybrid":0.01,"refusal":0.01},"reason":"单点"}`,
	}}
	sl.embedV = []float64{1, 0}
	d := depsWithLLMer(t, &streamFailLLM{inner: sl})
	events := ask(t, d, "s-err", "转专业绩点要求", "student")
	if len(eventsOf(events, "error")) == 0 {
		t.Fatalf("应有 error 事件: %v", events)
	}
	if done := doneOf(t, events); done.Reason != "error" {
		t.Fatalf("done.reason = %q, want error", done.Reason)
	}
}
