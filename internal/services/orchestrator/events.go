package orchestrator

import (
	orchestratorv1 "gewu/pkg/gen/gewu/orchestrator/v1"
)

// 事件构造器：proto oneof 形态与 PARITY §3 的 SSE 事件一一对应
// （gateway 侧负责按契约字段序序列化为 SSE JSON 帧）。

func routeEvent(route, reason string, byLLM bool) *orchestratorv1.ChatResponse {
	return &orchestratorv1.ChatResponse{Kind: &orchestratorv1.ChatResponse_Route{
		Route: &orchestratorv1.RouteEvent{Route: route, Reason: reason, ByLlm: byLLM},
	}}
}

func statusEvent(text string) *orchestratorv1.ChatResponse {
	return &orchestratorv1.ChatResponse{Kind: &orchestratorv1.ChatResponse_Status{
		Status: &orchestratorv1.StatusEvent{Text: text},
	}}
}

func stepEvent(index int32, sub string, sources []string) *orchestratorv1.ChatResponse {
	return &orchestratorv1.ChatResponse{Kind: &orchestratorv1.ChatResponse_Step{
		Step: &orchestratorv1.StepEvent{Index: index, Subquestion: sub, Sources: sources},
	}}
}

func answerEvent(text string) *orchestratorv1.ChatResponse {
	return &orchestratorv1.ChatResponse{Kind: &orchestratorv1.ChatResponse_AnswerDelta{
		AnswerDelta: &orchestratorv1.AnswerDeltaEvent{Text: text},
	}}
}

// citationsEvent items 空也必须是空数组语义（nil → 空 slice）。
func citationsEvent(items []*orchestratorv1.Citation) *orchestratorv1.ChatResponse {
	if items == nil {
		items = []*orchestratorv1.Citation{}
	}
	return &orchestratorv1.ChatResponse{Kind: &orchestratorv1.ChatResponse_Citations{
		Citations: &orchestratorv1.CitationsEvent{Items: items},
	}}
}

func slotQuestionEvent(slot, question string) *orchestratorv1.ChatResponse {
	return &orchestratorv1.ChatResponse{Kind: &orchestratorv1.ChatResponse_SlotQuestion{
		SlotQuestion: &orchestratorv1.SlotQuestionEvent{Slot: slot, Question: question},
	}}
}

// pendingActionEvent args 用 repeated KV 保持插入序（PARITY §9.3.3 展示契约）。
func pendingActionEvent(tool, label string, args []*orchestratorv1.ArgPair) *orchestratorv1.ChatResponse {
	return &orchestratorv1.ChatResponse{Kind: &orchestratorv1.ChatResponse_PendingAction{
		PendingAction: &orchestratorv1.PendingActionEvent{Tool: tool, Label: label, Args: args},
	}}
}

func actionResultEvent(tool string, success bool, message, receipt string) *orchestratorv1.ChatResponse {
	return &orchestratorv1.ChatResponse{Kind: &orchestratorv1.ChatResponse_ActionResult{
		ActionResult: &orchestratorv1.ActionResultEvent{
			Tool: tool, Success: success, Message: message, Receipt: receipt,
		},
	}}
}

func errorEvent(message string) *orchestratorv1.ChatResponse {
	return &orchestratorv1.ChatResponse{Kind: &orchestratorv1.ChatResponse_Error{
		Error: &orchestratorv1.ErrorEvent{Message: message},
	}}
}

func doneEvent(ms int64) *orchestratorv1.ChatResponse {
	return &orchestratorv1.ChatResponse{Kind: &orchestratorv1.ChatResponse_Done{
		Done: &orchestratorv1.DoneEvent{LatencyMs: ms},
	}}
}
