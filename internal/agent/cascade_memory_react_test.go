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
	embedV []float64
	chats  [][2]string // 记录 [system, user]

	// comps：ChatWithTools（原生 tool-calling）按序弹出的补全；耗尽后返回
	// &Completion{Content: last}（即"不再调用工具，直接给最终回答"）。
	comps []*llm.Completion
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

func (s *scriptedLLM) ChatStream(_ context.Context, _ []llm.Message, _ llm.Options, onDelta func(string) error) error {
	return onDelta(s.stream)
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

func (f *flakyAfterQueue) ChatStream(ctx context.Context, m []llm.Message, o llm.Options, onDelta func(string) error) error {
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

func (flakyAlways) ChatStream(context.Context, []llm.Message, llm.Options, func(string) error) error {
	return errors.New("网络错误")
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

func TestCascadeRouteL0Rule(t *testing.T) {
	d := testDeps(t) // L0 命中不依赖 LLM
	dec := d.CascadeRoute(context.Background(), "帮我预约明天晚上的羽毛球馆")
	if dec.Route != "transaction" || dec.Layer != "L0-rule" || dec.Confidence != 1.0 {
		t.Fatalf("dec = %+v", dec)
	}
	if dec.PreRAG || len(dec.Toolset) == 0 || dec.ModelTier != "small" {
		t.Errorf("决策包策略字段 = %+v", dec)
	}
	// 办理 + 政策咨询并存 → hybrid（与冻结基线启发式同规则）
	dec2 := d.CascadeRoute(context.Background(), "帮我提交明天一天的病假申请；另外请假超过 7 天是不是要教务处审批？")
	if dec2.Route != "hybrid" || dec2.Layer != "L0-rule" {
		t.Fatalf("办理+咨询应 L0 判 hybrid: %+v", dec2)
	}
	// 近似但不满足强信号的问句不走 L0
	dec3 := d.CascadeRoute(context.Background(), "预约场馆有什么要求")
	if dec3.Layer == "L0-rule" {
		t.Errorf("咨询类问题不应命中 L0: %+v", dec3)
	}
}

func TestCascadeRouteL1HighConfidence(t *testing.T) {
	d := testDeps(t)
	d.LLM = &scriptedLLM{resps: []string{
		`{"scores":{"factual":0.9,"research":0.05,"transaction":0.02,"hybrid":0.02,"refusal":0.01},"reason":"单一事实"}`,
	}}
	dec := d.CascadeRoute(context.Background(), "图书馆几点开门")
	if dec.Route != "factual" || dec.Layer != "L1-llm" || dec.Confidence != 0.9 || !dec.PreRAG {
		t.Fatalf("dec = %+v", dec)
	}
	if !dec.ByLLM {
		t.Error("L1 判定应标记 by_llm")
	}
}

func TestCascadeRouteMarginTriggersL2(t *testing.T) {
	d := testDeps(t)
	d.LLM = &scriptedLLM{resps: []string{
		// L1：top1=0.5、margin=0.08 → 类别纠缠，升级 L2
		`{"scores":{"factual":0.5,"research":0.42,"transaction":0.05,"hybrid":0.02,"refusal":0.01},"reason":"拿不准"}`,
		// L2：主模型判定
		`{"route":"research","reason":"涉及多份文件"}`,
	}}
	dec := d.CascadeRoute(context.Background(), "转专业后绩点怎么算，影响保研吗")
	if dec.Route != "research" || dec.Layer != "L2-main" {
		t.Fatalf("dec = %+v", dec)
	}
	if dec.ModelTier != "flagship" {
		t.Errorf("ModelTier = %s", dec.ModelTier)
	}
}

func TestCascadeRouteL2UncertainFallsBack(t *testing.T) {
	d := testDeps(t)
	d.LLM = &scriptedLLM{resps: []string{
		`{"scores":{"factual":0.3,"research":0.25,"transaction":0.2,"hybrid":0.15,"refusal":0.1},"reason":"完全拿不准"}`,
		`这不是JSON输出`, // L2 解析失败
	}}
	dec := d.CascadeRoute(context.Background(), "随便说点什么")
	if dec.Route != "factual" || dec.Layer != "L2-uncertain" || dec.ModelTier != "flagship" {
		t.Fatalf("dec = %+v", dec)
	}
}

func TestCascadeRouteRefusalSafetyNet(t *testing.T) {
	d := testDeps(t)
	d.LLM = &scriptedLLM{resps: []string{
		// L1 高置信 refusal，但问题带办理强动词 → 强制升级 L2 复核
		`{"scores":{"factual":0.05,"research":0.03,"transaction":0.02,"hybrid":0.02,"refusal":0.88},"reason":"误判"}`,
		// L2 主模型纠正为 transaction
		`{"route":"transaction","reason":"明确办理诉求"}`,
	}}
	dec := d.CascadeRoute(context.Background(), "帮我请下周一到下周二的事假")
	if dec.Route != "transaction" || dec.Layer != "L2-main" {
		t.Fatalf("refusal 安全网应升级 L2 并纠正: %+v", dec)
	}
	// 领域词安全网：无办理动词但含校园实体词的 refusal 同样升级 L2
	d2 := testDeps(t)
	d2.LLM = &scriptedLLM{resps: []string{
		`{"scores":{"factual":0.04,"research":0.03,"transaction":0.02,"hybrid":0.01,"refusal":0.9},"reason":"误判"}`,
		`{"route":"factual","reason":"转专业政策咨询"}`,
	}}
	dec2 := d2.CascadeRoute(context.Background(), "我的情况符合转专业申请条件吗？")
	if dec2.Route != "factual" || dec2.Layer != "L2-main" {
		t.Fatalf("领域词 refusal 应升级 L2: %+v", dec2)
	}
	// 无强动词且无领域词的 refusal 维持 L1 直判
	d3 := testDeps(t)
	d3.LLM = &scriptedLLM{resps: []string{
		`{"scores":{"factual":0.02,"research":0.02,"transaction":0.02,"hybrid":0.02,"refusal":0.92},"reason":"无关问题"}`,
	}}
	dec3 := d3.CascadeRoute(context.Background(), "今天A股行情怎么样")
	if dec3.Route != "refusal" || dec3.Layer != "L1-llm" {
		t.Fatalf("无关问题 refusal 应直接采信 L1: %+v", dec3)
	}
}

func TestCascadeRouteMiddleBandAccepted(t *testing.T) {
	d := testDeps(t)
	d.LLM = &scriptedLLM{resps: []string{
		// top1=0.6（介于 low/high 之间）但 margin=0.3 足够 → 直接采信 L1，不升级 L2
		`{"scores":{"factual":0.6,"research":0.3,"transaction":0.05,"hybrid":0.03,"refusal":0.02},"reason":"偏事实"}`,
	}}
	dec := d.CascadeRoute(context.Background(), "奖学金什么时候评定")
	if dec.Route != "factual" || dec.Layer != "L1-llm" {
		t.Fatalf("dec = %+v", dec)
	}
	// 只调用了一次 LLM（无 L2）
	if n := len(d.LLM.(*scriptedLLM).chatLog()); n != 1 {
		t.Errorf("L1 直接采信时应只有 1 次 LLM 调用, got %d", n)
	}
}

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

func (failingLLM) ChatStream(context.Context, []llm.Message, llm.Options, func(string) error) error {
	return errors.New("网络错误")
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
