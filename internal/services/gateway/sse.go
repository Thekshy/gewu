package gateway

import (
	"encoding/json"
	"strings"

	orchestratorv1 "gewu/pkg/gen/gewu/orchestrator/v1"
)

// SSE 帧编码：proto 事件 → PARITY §3 契约 JSON（字段序、不转义 UTF-8/HTML）。
// 这是前端与评测依赖的逐字节形状，golden 用例锁死（sse_test.go）。

type sseRoute struct {
	Type   string `json:"type"`
	Route  string `json:"route"`
	Reason string `json:"reason"`
	ByLLM  bool   `json:"by_llm"`
}

type sseStatus struct {
	Type string `json:"type"`
	Text string `json:"text"`
}

type sseStep struct {
	Type        string   `json:"type"`
	Index       int32    `json:"index"`
	Subquestion string   `json:"subquestion"`
	Sources     []string `json:"sources"`
}

type sseAnswerDelta struct {
	Type string `json:"type"`
	Text string `json:"text"`
}

type sseCitationItem struct {
	N      int32  `json:"n"`
	DocID  string `json:"doc_id"`
	Title  string `json:"title"`
	Source string `json:"source"`
}

type sseCitations struct {
	Type  string            `json:"type"`
	Items []sseCitationItem `json:"items"`
}

type sseSlotQuestion struct {
	Type     string `json:"type"`
	Slot     string `json:"slot"`
	Question string `json:"question"`
}

type ssePendingAction struct {
	Type  string       `json:"type"`
	Tool  string       `json:"tool"`
	Label string       `json:"label"`
	Args  *orderedArgs `json:"args"`
}

type sseActionResult struct {
	Type    string  `json:"type"`
	Tool    string  `json:"tool"`
	Success bool    `json:"success"`
	Message string  `json:"message"`
	Receipt *string `json:"receipt"` // nil → null
}

type sseError struct {
	Type    string `json:"type"`
	Message string `json:"message"`
}

type sseDone struct {
	Type      string `json:"type"`
	LatencyMS int64  `json:"latency_ms"`
}

// orderedArgs 有序参数表：marshal 成 JSON 对象且保插入序。
// encoding/json 对 map 按 key 排序输出，会打乱「场馆/日期/时段/…」的展示顺序
// （PARITY §9.3.3 契约），故自定义 MarshalJSON（与冻结单体 events.go 一致）。
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

// eventJSON 把 proto 事件序列化为 SSE data 负载（UTF-8/HTML 不转义）。
func eventJSON(ev *orchestratorv1.ChatResponse) ([]byte, error) {
	var payload any
	switch k := ev.GetKind().(type) {
	case *orchestratorv1.ChatResponse_Route:
		payload = sseRoute{"route", k.Route.GetRoute(), k.Route.GetReason(), k.Route.GetByLlm()}
	case *orchestratorv1.ChatResponse_Status:
		payload = sseStatus{"status", k.Status.GetText()}
	case *orchestratorv1.ChatResponse_Step:
		sources := k.Step.GetSources()
		if sources == nil {
			sources = []string{}
		}
		payload = sseStep{"step", k.Step.GetIndex(), k.Step.GetSubquestion(), sources}
	case *orchestratorv1.ChatResponse_AnswerDelta:
		payload = sseAnswerDelta{"answer_delta", k.AnswerDelta.GetText()}
	case *orchestratorv1.ChatResponse_Citations:
		items := make([]sseCitationItem, 0, len(k.Citations.GetItems()))
		for _, c := range k.Citations.GetItems() {
			items = append(items, sseCitationItem{c.GetN(), c.GetDocId(), c.GetTitle(), c.GetSource()})
		}
		payload = sseCitations{"citations", items} // 空也输出 []
	case *orchestratorv1.ChatResponse_SlotQuestion:
		payload = sseSlotQuestion{"slot_question", k.SlotQuestion.GetSlot(), k.SlotQuestion.GetQuestion()}
	case *orchestratorv1.ChatResponse_PendingAction:
		args := newOrderedArgs()
		for _, kv := range k.PendingAction.GetArgs() { // repeated KV 保插入序
			args.set(kv.GetKey(), kv.GetValue())
		}
		payload = ssePendingAction{"pending_action", k.PendingAction.GetTool(), k.PendingAction.GetLabel(), args}
	case *orchestratorv1.ChatResponse_ActionResult:
		r := k.ActionResult
		var receipt *string
		if r.GetReceipt() != "" { // 空串 = 无凭证 → null
			s := r.GetReceipt()
			receipt = &s
		}
		// alternatives 不在 SSE 契约里（PARITY §3）——只用于编排侧追问文案
		payload = sseActionResult{"action_result", r.GetTool(), r.GetSuccess(), r.GetMessage(), receipt}
	case *orchestratorv1.ChatResponse_Error:
		payload = sseError{"error", k.Error.GetMessage()}
	case *orchestratorv1.ChatResponse_Done:
		payload = sseDone{"done", k.Done.GetLatencyMs()}
	default:
		payload = sseError{"error", "未知事件类型"}
	}
	return marshalNoEscape(payload)
}

// marshalNoEscape 序列化事件：不转义 HTML 字符也保留 UTF-8 原文
// （与 Python json.dumps(ensure_ascii=False) 的输出对齐；冻结单体同款）。
func marshalNoEscape(v any) ([]byte, error) {
	var sb strings.Builder
	enc := json.NewEncoder(&sb)
	enc.SetEscapeHTML(false)
	if err := enc.Encode(v); err != nil {
		return nil, err
	}
	// Encoder 会追加换行，去掉
	return []byte(strings.TrimRight(sb.String(), "\n")), nil
}
