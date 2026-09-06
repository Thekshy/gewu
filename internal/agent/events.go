// Package agent 实现会话编排：路由 → 直答 / 深度研究 / 拒答 / 业务办理 / 混合 → 统一事件流。
//
// SSE 接口与评测复用同一入口。业务办理（transaction/hybrid）带跨轮状态：槽位收集
// 与确认阶段，用户下一条消息优先按流程回复解释，切话题则自动放弃流程。
package agent

import (
	"encoding/json"

	"gewu/internal/agent/routing"
)

// Citation 引用条目（citations 事件 items 元素）。
type Citation struct {
	N      int    `json:"n"`
	DocID  string `json:"doc_id"`
	Title  string `json:"title"`
	Source string `json:"source"`
}

// 各事件的字段集与 JSON 形状是前端与评测的契约（PARITY §3），逐字段对照实现。

type routeEvent struct {
	Type   string   `json:"type"`
	Route  string   `json:"route"`
	Reason string   `json:"reason"`
	ByLLM  bool     `json:"by_llm"`
	Layer  string   `json:"layer,omitempty"`      // P6 级联路由层级（classic/续轮路径为空，形状不变）
	Conf   *float64 `json:"confidence,omitempty"` // 指针区分 0 与未提供
}

type statusEvent struct {
	Type string `json:"type"`
	Text string `json:"text"`
}

type stepEvent struct {
	Type        string   `json:"type"`
	Index       int      `json:"index"`
	Subquestion string   `json:"subquestion"`
	Sources     []string `json:"sources"`
}

type answerDeltaEvent struct {
	Type string `json:"type"`
	Text string `json:"text"`
}

type citationsEvent struct {
	Type  string     `json:"type"`
	Items []Citation `json:"items"`
}

type slotQuestionEvent struct {
	Type     string `json:"type"`
	Slot     string `json:"slot"`
	Question string `json:"question"`
}

type pendingActionEvent struct {
	Type  string       `json:"type"`
	Tool  string       `json:"tool"`
	Label string       `json:"label"`
	Args  *orderedArgs `json:"args"`
}

// orderedArgs 是确认摘要的有序参数表：marshal 成 JSON 对象且保插入序。
// encoding/json 对 map 按 key 排序输出，会打乱「场馆/日期/时段/…」的展示顺序，
// 故自定义 MarshalJSON。
type orderedArgs struct {
	keys []string
	vals map[string]string
}

func newOrderedArgs() *orderedArgs { return &orderedArgs{vals: map[string]string{}} }

func (a *orderedArgs) set(k, v string) {
	if _, ok := a.vals[k]; !ok {
		a.keys = append(a.keys, k)
	}
	a.vals[k] = v
}

func (a *orderedArgs) MarshalJSON() ([]byte, error) {
	buf := []byte{'{'}
	for i, k := range a.keys {
		if i > 0 {
			buf = append(buf, ',')
		}
		kb, _ := json.Marshal(k)
		vb, _ := json.Marshal(a.vals[k])
		buf = append(buf, kb...)
		buf = append(buf, ':')
		buf = append(buf, vb...)
	}
	buf = append(buf, '}')
	return buf, nil
}

type actionResultEvent struct {
	Type    string  `json:"type"`
	Tool    string  `json:"tool"`
	Success bool    `json:"success"`
	Message string  `json:"message"`
	Receipt *string `json:"receipt"` // nil → null（缺省即无凭证）
}

type errorEvent struct {
	Type    string `json:"type"`
	Message string `json:"message"`
}

type doneEvent struct {
	Type      string `json:"type"`
	LatencyMS int64  `json:"latency_ms"`
}

// ---------- 构造器 ----------

func routeEvt(route, reason string, byLLM bool) routeEvent {
	return routeEvent{Type: "route", Route: route, Reason: reason, ByLLM: byLLM}
}

// routeDecisionEvt 决策包 → route 事件（级联模式下带 layer/confidence）。
func routeDecisionEvt(dec routing.RouteDecision) routeEvent {
	ev := routeEvent{Type: "route", Route: dec.Route, Reason: dec.Reason, ByLLM: dec.ByLLM, Layer: dec.Layer}
	if dec.Layer != "" {
		conf := dec.Confidence
		ev.Conf = &conf
	}
	return ev
}

func statusEvt(text string) statusEvent { return statusEvent{Type: "status", Text: text} }

func stepEvt(index int, sub string, sources []string) stepEvent {
	return stepEvent{Type: "step", Index: index, Subquestion: sub, Sources: sources}
}

func answerEvt(text string) answerDeltaEvent {
	return answerDeltaEvent{Type: "answer_delta", Text: text}
}

// citationsEvt items 必须是数组（空也要 []，不能是 null）。
func citationsEvt(items []Citation) citationsEvent {
	if items == nil {
		items = []Citation{}
	}
	return citationsEvent{Type: "citations", Items: items}
}

func slotQuestionEvt(slot, question string) slotQuestionEvent {
	return slotQuestionEvent{Type: "slot_question", Slot: slot, Question: question}
}

func pendingActionEvt(tool, label string, args *orderedArgs) pendingActionEvent {
	return pendingActionEvent{Type: "pending_action", Tool: tool, Label: label, Args: args}
}

func actionResultEvt(tool string, success bool, message string, receipt *string) actionResultEvent {
	return actionResultEvent{Type: "action_result", Tool: tool, Success: success, Message: message, Receipt: receipt}
}

func errorEvt(message string) errorEvent { return errorEvent{Type: "error", Message: message} }

func doneEvt(ms int64) doneEvent { return doneEvent{Type: "done", LatencyMS: ms} }
