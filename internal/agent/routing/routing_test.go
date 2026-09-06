package routing

import (
	"context"
	"strings"
	"sync"
	"testing"

	"gewu/internal/llm"
)

// scriptedChat 路由测试的 Chat-only mock：按序弹 resps，耗尽后返回 last。
type scriptedChat struct {
	mu    sync.Mutex
	resps []string
	last  string
	calls int
}

func (s *scriptedChat) HasKey() bool { return true }

func (s *scriptedChat) Chat(_ context.Context, _ []llm.Message, _ llm.Options) (string, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.calls++
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

func (s *scriptedChat) count() int {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.calls
}

// ---------- classic：启发式 ----------

func TestHeuristicRouter(t *testing.T) {
	if r := HeuristicRoute("我挂过一门课，还能申请转专业吗，转完学分怎么算"); r.Route != "research" {
		t.Errorf("复合问题应 research: %+v", r)
	}
	if r := HeuristicRoute("请问学校对于本科生申请国际交换项目的绩点要求和语言成绩要求分别是什么？"); r.Route != "research" {
		t.Errorf("长问题应 research: %+v", r)
	}
	if r := HeuristicRoute("图书馆几点开门"); r.Route != "factual" {
		t.Errorf("简单事实应 factual: %+v", r)
	}
	if r := HeuristicRoute("校园卡丢了怎么补办"); r.Route != "factual" {
		t.Errorf("简单事实应 factual: %+v", r)
	}
	if r := HeuristicRoute("帮我预约明天晚上的羽毛球馆"); r.Route != "transaction" {
		t.Errorf("办理诉求应 transaction: %+v", r)
	}
	if r := HeuristicRoute("帮我预约场馆，有什么要求吗"); r.Route != "hybrid" {
		t.Errorf("办理+咨询应 hybrid: %+v", r)
	}
	// 「请假一周找谁批」无第一人称请求词、含咨询词「谁」→ 落入知识问答（factual），
	// 启发式刻意不把「咨询政策」判为办理（与冻结基线行为一致）。
	if r := HeuristicRoute("我请假一周需要找谁审批？"); r.Route != "factual" {
		t.Errorf("仅咨询政策应落知识问答: %+v", r)
	}
}

// ---------- cascade：三级级联 ----------

func TestCascadeRouteL0Rule(t *testing.T) {
	d := &Deps{LLM: &scriptedChat{}} // L0 命中不依赖 LLM
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
	d := &Deps{LLM: &scriptedChat{resps: []string{
		`{"scores":{"factual":0.9,"research":0.05,"transaction":0.02,"hybrid":0.02,"refusal":0.01},"reason":"单一事实"}`,
	}}}
	dec := d.CascadeRoute(context.Background(), "图书馆几点开门")
	if dec.Route != "factual" || dec.Layer != "L1-llm" || dec.Confidence != 0.9 || !dec.PreRAG {
		t.Fatalf("dec = %+v", dec)
	}
	if !dec.ByLLM {
		t.Error("L1 判定应标记 by_llm")
	}
}

func TestCascadeRouteMarginTriggersL2(t *testing.T) {
	d := &Deps{LLM: &scriptedChat{resps: []string{
		// L1：top1=0.5、margin=0.08 → 类别纠缠，升级 L2
		`{"scores":{"factual":0.5,"research":0.42,"transaction":0.05,"hybrid":0.02,"refusal":0.01},"reason":"拿不准"}`,
		// L2：主模型判定
		`{"route":"research","reason":"涉及多份文件"}`,
	}}}
	dec := d.CascadeRoute(context.Background(), "转专业后绩点怎么算，影响保研吗")
	if dec.Route != "research" || dec.Layer != "L2-main" {
		t.Fatalf("dec = %+v", dec)
	}
	if dec.ModelTier != "flagship" {
		t.Errorf("ModelTier = %s", dec.ModelTier)
	}
}

func TestCascadeRouteL2UncertainFallsBack(t *testing.T) {
	d := &Deps{LLM: &scriptedChat{resps: []string{
		`{"scores":{"factual":0.3,"research":0.25,"transaction":0.2,"hybrid":0.15,"refusal":0.1},"reason":"完全拿不准"}`,
		`这不是JSON输出`, // L2 解析失败
	}}}
	dec := d.CascadeRoute(context.Background(), "随便说点什么")
	if dec.Route != "factual" || dec.Layer != "L2-uncertain" || dec.ModelTier != "flagship" {
		t.Fatalf("dec = %+v", dec)
	}
}

func TestCascadeRouteMiddleBandAccepted(t *testing.T) {
	d := &Deps{LLM: &scriptedChat{resps: []string{
		// top1=0.6（介于 low/high 之间）但 margin=0.3 足够 → 直接采信 L1，不升级 L2
		`{"scores":{"factual":0.6,"research":0.3,"transaction":0.05,"hybrid":0.03,"refusal":0.02},"reason":"偏事实"}`,
	}}}
	dec := d.CascadeRoute(context.Background(), "奖学金什么时候评定")
	if dec.Route != "factual" || dec.Layer != "L1-llm" {
		t.Fatalf("dec = %+v", dec)
	}
	if n := d.LLM.(*scriptedChat).count(); n != 1 {
		t.Errorf("L1 直接采信时应只有 1 次 LLM 调用, got %d", n)
	}
}

func TestCascadeRouteRefusalSafetyNet(t *testing.T) {
	d := &Deps{LLM: &scriptedChat{resps: []string{
		// L1 高置信 refusal，但问题带办理强动词 → 强制升级 L2 复核
		`{"scores":{"factual":0.05,"research":0.03,"transaction":0.02,"hybrid":0.02,"refusal":0.88},"reason":"误判"}`,
		// L2 主模型纠正为 transaction
		`{"route":"transaction","reason":"明确办理诉求"}`,
	}}}
	dec := d.CascadeRoute(context.Background(), "帮我请下周一到下周二的事假")
	if dec.Route != "transaction" || dec.Layer != "L2-main" {
		t.Fatalf("refusal 安全网应升级 L2 并纠正: %+v", dec)
	}
	// 领域词安全网：无办理动词但含校园实体词的 refusal 同样升级 L2
	d2 := &Deps{LLM: &scriptedChat{resps: []string{
		`{"scores":{"factual":0.04,"research":0.03,"transaction":0.02,"hybrid":0.01,"refusal":0.9},"reason":"误判"}`,
		`{"route":"factual","reason":"转专业政策咨询"}`,
	}}}
	dec2 := d2.CascadeRoute(context.Background(), "我的情况符合转专业申请条件吗？")
	if dec2.Route != "factual" || dec2.Layer != "L2-main" {
		t.Fatalf("领域词 refusal 应升级 L2: %+v", dec2)
	}
	// 无强动词且无领域词的 refusal 维持 L1 直判
	d3 := &Deps{LLM: &scriptedChat{resps: []string{
		`{"scores":{"factual":0.02,"research":0.02,"transaction":0.02,"hybrid":0.02,"refusal":0.92},"reason":"无关问题"}`,
	}}}
	dec3 := d3.CascadeRoute(context.Background(), "今天A股行情怎么样")
	if dec3.Route != "refusal" || dec3.Layer != "L1-llm" {
		t.Fatalf("无关问题 refusal 应直接采信 L1: %+v", dec3)
	}
}

func TestCascadeRouteNoLLMFallsBackHeuristic(t *testing.T) {
	d := &Deps{} // 无 LLM：L0 不命中的问题退启发式
	dec := d.CascadeRoute(context.Background(), "转专业和保研分别有什么要求")
	if dec.Route != "research" || dec.Layer != "heuristic-fallback" {
		t.Fatalf("无 LLM 应退启发式: %+v", dec)
	}
}

// ---------- agent-first：triage ----------

func TestTriageRuleFastPath(t *testing.T) {
	d := &Deps{LLM: &scriptedChat{}} // 不应有任何 LLM 调用
	dec := d.TriageRoute(context.Background(), "帮我预约明天晚上的羽毛球馆")
	if dec.Route != "agent" || dec.Layer != "triage-rule" || dec.Confidence != 1.0 {
		t.Fatalf("dec = %+v", dec)
	}
	if n := d.LLM.(*scriptedChat).count(); n != 0 {
		t.Errorf("规则快路径不应调 LLM: %d", n)
	}
}

func TestTriageLLMChoices(t *testing.T) {
	cases := []struct {
		resp  string
		q     string
		route string
	}{
		{`{"choice":"direct","reason":"单一政策查询"}`, "图书馆几点开门", "factual"},
		{`{"choice":"agent","reason":"需要业务系统"}`, "查一下我的预约", "agent"},
		{`{"choice":"refusal","reason":"无关"}`, "推荐几部悬疑电影", "refusal"},
	}
	for _, c := range cases {
		d := &Deps{LLM: &scriptedChat{resps: []string{c.resp}}}
		dec := d.TriageRoute(context.Background(), c.q)
		if dec.Route != c.route || dec.Layer != "triage-llm" {
			t.Errorf("TriageRoute(%q) = %+v, want %s", c.q, dec, c.route)
		}
	}
}

func TestTriageRefusalGuardFailsOpen(t *testing.T) {
	// refusal 判定与校园领域词矛盾 → fail-open 到 agent（与级联路由安全网同逻辑）
	d := &Deps{LLM: &scriptedChat{resps: []string{
		`{"choice":"refusal","reason":"误判"}`,
	}}}
	dec := d.TriageRoute(context.Background(), "我的情况符合转专业申请条件吗？")
	if dec.Route != "agent" || dec.Layer != "triage-guard" {
		t.Fatalf("领域词 refusal 应 fail-open: %+v", dec)
	}
}

func TestTriageFailOpenOnErrors(t *testing.T) {
	// LLM 报错 / 输出非法 / 未知选项 → 一律 fail-open 到 agent
	for _, sl := range []*scriptedChat{
		{}, // 无 resps → Chat 返回空串（解析失败）
		{resps: []string{"不是JSON"}},
		{resps: []string{`{"choice":"quantum","reason":"？"}`}},
	} {
		d := &Deps{LLM: sl}
		dec := d.TriageRoute(context.Background(), "图书馆几点开门")
		if dec.Route != "agent" || dec.Layer != "triage-open" {
			t.Errorf("应 fail-open 到 agent: %+v", dec)
		}
	}
}

func TestTriageHeuristicFallback(t *testing.T) {
	// 无 LLM：旧启发式映射到三策略
	d := &Deps{}
	for q, want := range map[string]string{
		"帮我预约明天晚上的羽毛球馆": "agent", // heuristic transaction
		"转专业和保研分别有什么要求": "agent", // heuristic research
		"图书馆几点开门":       "factual",
	} {
		dec := d.TriageRoute(context.Background(), q)
		if dec.Route != want {
			t.Errorf("TriageRoute(%q) = %s, want %s", q, dec.Route, want)
		}
	}
}

// ---------- Decide：模式分发 ----------

func TestDecideDispatchesByMode(t *testing.T) {
	q := "图书馆几点开门"
	// cascade（缺省）
	dc := &Deps{LLM: &scriptedChat{resps: []string{
		`{"scores":{"factual":0.95,"research":0.02,"transaction":0.01,"hybrid":0.01,"refusal":0.01},"reason":"x"}`,
	}}}
	if dec := dc.Decide(context.Background(), q); dec.Layer != "L1-llm" {
		t.Errorf("缺省应 cascade: %+v", dec)
	}
	// classic
	dk := &Deps{Mode: "classic", LLM: &scriptedChat{resps: []string{
		`{"route":"factual","reason":"x"}`,
	}}}
	if dec := dk.Decide(context.Background(), q); dec.Layer != "classic" {
		t.Errorf("classic 分发错误: %+v", dec)
	}
	// agent-first
	da := &Deps{Mode: "agent-first", LLM: &scriptedChat{resps: []string{
		`{"choice":"direct","reason":"x"}`,
	}}}
	if dec := da.Decide(context.Background(), q); dec.Layer != "triage-llm" {
		t.Errorf("agent-first 分发错误: %+v", dec)
	}
}

func TestReactPlanSignal(t *testing.T) {
	if !ReactPlanSignal("帮我安排一下明天的场馆，顺便把请假也定了") {
		t.Error("规划信号词应命中")
	}
	if ReactPlanSignal("图书馆几点开门") {
		t.Error("普通问题不应命中")
	}
}

// ---------- FillPolicy：用户指定模式复用 ----------

func TestFillPolicyUserSpecified(t *testing.T) {
	dec := RouteDecision{Route: "research", Layer: "user-specified", PreRAG: true}
	dec.FillPolicy()
	if dec.ModelTier != "flagship" || !dec.PreRAG {
		t.Errorf("research 策略 = %+v", dec)
	}
	if !strings.Contains(dec.Layer, "user") {
		t.Errorf("layer 不应被覆盖: %+v", dec)
	}
}
