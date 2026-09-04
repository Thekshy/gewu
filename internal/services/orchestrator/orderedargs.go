package orchestrator

import (
	"encoding/json"

	orchestratorv1 "gewu/pkg/gen/gewu/orchestrator/v1"
)

// orderedArgs 有序参数表（拷贝自冻结 internal/agent/events.go 的同名实现，
// 并加 protoArgs 产出 pending_action 的 repeated KV）：
// encoding/json 对 map 按 key 排序输出，会打乱「场馆/日期/时段/…」的
// 展示顺序（PARITY §9.3.3 契约），故自定义 MarshalJSON。
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

// protoArgs 转 pending_action 事件的 repeated KV（保插入序）。
func (a *orderedArgs) protoArgs() []*orchestratorv1.ArgPair {
	out := make([]*orchestratorv1.ArgPair, 0, len(a.keys))
	for _, k := range a.keys {
		out = append(out, &orchestratorv1.ArgPair{Key: k, Value: a.vals[k]})
	}
	return out
}
