package agent

import (
	"context"
	"strings"
	"testing"

	"gewu/internal/llm"
)

// ---------- agent-first：三选一执行策略分流器 ----------

func TestTriageRuleFastPath(t *testing.T) {
	d := testDeps(t)
	d.LLM = &scriptedLLM{} // 不应有任何 LLM 调用
	dec := d.TriageRoute(context.Background(), "帮我预约明天晚上的羽毛球馆")
	if dec.Route != "agent" || dec.Layer != "triage-rule" || dec.Confidence != 1.0 {
		t.Fatalf("dec = %+v", dec)
	}
	if n := len(d.LLM.(*scriptedLLM).chatLog()); n != 0 {
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
		d := testDeps(t)
		d.LLM = &scriptedLLM{resps: []string{c.resp}}
		dec := d.TriageRoute(context.Background(), c.q)
		if dec.Route != c.route || dec.Layer != "triage-llm" {
			t.Errorf("TriageRoute(%q) = %+v, want %s", c.q, dec, c.route)
		}
	}
}

func TestTriageRefusalGuardFailsOpen(t *testing.T) {
	// refusal 判定与校园领域词矛盾 → fail-open 到 agent（与级联路由安全网同逻辑）
	d := testDeps(t)
	d.LLM = &scriptedLLM{resps: []string{
		`{"choice":"refusal","reason":"误判"}`,
	}}
	dec := d.TriageRoute(context.Background(), "我的情况符合转专业申请条件吗？")
	if dec.Route != "agent" || dec.Layer != "triage-guard" {
		t.Fatalf("领域词 refusal 应 fail-open: %+v", dec)
	}
}

func TestTriageFailOpenOnErrors(t *testing.T) {
	// LLM 报错 / 输出非法 / 未知选项 → 一律 fail-open 到 agent
	for _, sl := range []*scriptedLLM{
		{}, // 无 resps → Chat 返回空串（解析失败）
		{resps: []string{"不是JSON"}},
		{resps: []string{`{"choice":"quantum","reason":"？"}`}},
	} {
		d := testDeps(t)
		d.LLM = sl
		dec := d.TriageRoute(context.Background(), "图书馆几点开门")
		if dec.Route != "agent" || dec.Layer != "triage-open" {
			t.Errorf("应 fail-open 到 agent: %+v", dec)
		}
	}
}

func TestTriageHeuristicFallback(t *testing.T) {
	// 无 LLM：旧启发式映射到三策略
	d := testDeps(t)
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

func TestPipelineAgentFirstDispatch(t *testing.T) {
	// 集成：agent-first 模式下 direct → 直答链路；agent → ReAct 链路
	// ① direct：triage 后走 AnswerDirect（流式 + 引用）
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
	// ② agent：triage 后走 RunReAct（原生 tool-calling 调一次读工具后作答）
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
