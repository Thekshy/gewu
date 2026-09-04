package gateway

import (
	"testing"

	orchestratorv1 "gewu/pkg/gen/gewu/orchestrator/v1"
)

// golden：事件 JSON 的字段序与编码是前端/评测契约（PARITY §3），
// 以下用例与冻结单体输出逐字节对齐。

func TestEventJSONRoute(t *testing.T) {
	got, err := eventJSON(routeEvtForTest("factual", "启发式：短事实型问题", false))
	if err != nil {
		t.Fatal(err)
	}
	want := `{"type":"route","route":"factual","reason":"启发式：短事实型问题","by_llm":false}`
	if string(got) != want {
		t.Fatalf("route 事件不匹配\n got: %s\nwant: %s", got, want)
	}
}

func TestEventJSONAnswerKeepsUTF8AndHTML(t *testing.T) {
	got, err := eventJSON(answerEvtForTest("转专业需要 <GPA≥2.0>，且无违纪&处分"))
	if err != nil {
		t.Fatal(err)
	}
	want := `{"type":"answer_delta","text":"转专业需要 <GPA≥2.0>，且无违纪&处分"}`
	if string(got) != want {
		t.Fatalf("answer_delta 编码不匹配（HTML/UTF-8 不得转义）\n got: %s\nwant: %s", got, want)
	}
}

func TestEventJSONCitationsEmptyIsArray(t *testing.T) {
	got, err := eventJSON(citationsEvtForTest(nil))
	if err != nil {
		t.Fatal(err)
	}
	if string(got) != `{"type":"citations","items":[]}` {
		t.Fatalf("citations 空列表必须是 []：%s", got)
	}
}

func TestEventJSONCitationsItems(t *testing.T) {
	ev := &orchestratorv1.ChatResponse{Kind: &orchestratorv1.ChatResponse_Citations{
		Citations: &orchestratorv1.CitationsEvent{Items: []*orchestratorv1.Citation{
			{N: 1, DocId: "0001-transfer", Title: "转专业管理办法", Source: "钱塘大学"},
		}},
	}}
	got, _ := eventJSON(ev)
	want := `{"type":"citations","items":[{"n":1,"doc_id":"0001-transfer","title":"转专业管理办法","source":"钱塘大学"}]}`
	if string(got) != want {
		t.Fatalf("citations 不匹配\n got: %s\nwant: %s", got, want)
	}
}

func TestEventJSONPendingActionArgOrder(t *testing.T) {
	ev := &orchestratorv1.ChatResponse{Kind: &orchestratorv1.ChatResponse_PendingAction{
		PendingAction: &orchestratorv1.PendingActionEvent{
			Tool: "book_venue", Label: "预约场馆",
			Args: []*orchestratorv1.ArgPair{
				{Key: "场馆", Value: "羽毛球馆"},
				{Key: "日期", Value: "2026-09-05"},
				{Key: "时段", Value: "19:00-21:00"},
			},
		},
	}}
	got, _ := eventJSON(ev)
	want := `{"type":"pending_action","tool":"book_venue","label":"预约场馆","args":{"场馆":"羽毛球馆","日期":"2026-09-05","时段":"19:00-21:00"}}`
	if string(got) != want {
		t.Fatalf("pending_action 参数插入序不匹配\n got: %s\nwant: %s", got, want)
	}
}

func TestEventJSONActionResultReceiptNull(t *testing.T) {
	ev := &orchestratorv1.ChatResponse{Kind: &orchestratorv1.ChatResponse_ActionResult{
		ActionResult: &orchestratorv1.ActionResultEvent{Tool: "query_venues", Success: true, Message: "ok"},
	}}
	got, _ := eventJSON(ev)
	if string(got) != `{"type":"action_result","tool":"query_venues","success":true,"message":"ok","receipt":null}` {
		t.Fatalf("action_result 无凭证时 receipt 应为 null：%s", got)
	}
}

func TestEventJSONDone(t *testing.T) {
	got, _ := eventJSON(doneEvtForTest(1234))
	if string(got) != `{"type":"done","latency_ms":1234}` {
		t.Fatalf("done 不匹配：%s", got)
	}
}

// ---------- 构造辅助（不引 orchestrator 内部实现） ----------

func routeEvtForTest(route, reason string, byLLM bool) *orchestratorv1.ChatResponse {
	return &orchestratorv1.ChatResponse{Kind: &orchestratorv1.ChatResponse_Route{
		Route: &orchestratorv1.RouteEvent{Route: route, Reason: reason, ByLlm: byLLM},
	}}
}

func answerEvtForTest(text string) *orchestratorv1.ChatResponse {
	return &orchestratorv1.ChatResponse{Kind: &orchestratorv1.ChatResponse_AnswerDelta{
		AnswerDelta: &orchestratorv1.AnswerDeltaEvent{Text: text},
	}}
}

func citationsEvtForTest(items []*orchestratorv1.Citation) *orchestratorv1.ChatResponse {
	return &orchestratorv1.ChatResponse{Kind: &orchestratorv1.ChatResponse_Citations{
		Citations: &orchestratorv1.CitationsEvent{Items: items},
	}}
}

func doneEvtForTest(ms int64) *orchestratorv1.ChatResponse {
	return &orchestratorv1.ChatResponse{Kind: &orchestratorv1.ChatResponse_Done{
		Done: &orchestratorv1.DoneEvent{LatencyMs: ms},
	}}
}
