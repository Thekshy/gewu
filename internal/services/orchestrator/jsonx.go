package orchestrator

import (
	"encoding/json"
	"strings"

	"io"

	"google.golang.org/grpc/status"
)

// errEOF 统一 EOF 判定（gRPC 流结束）。
var errEOF = io.EOF

// errText 取错误的可展示文本（gRPC 状态消息优先）。
func errText(err error) string {
	if s, ok := status.FromError(err); ok && s.Message() != "" {
		return s.Message()
	}
	return err.Error()
}

// parseJSONObject 容错解析 LLM 输出的 JSON 对象（拷贝自冻结 internal/agent/jsonx.go，
// 行为逐字一致）：剥离 ```json 围栏、截取首个 { 到末个 } 之间的内容再解析。
func parseJSONObject(raw string) (map[string]any, error) {
	s := strings.TrimSpace(raw)
	if s == "" {
		return nil, errEmptyJSON
	}
	if start := strings.Index(s, "{"); start >= 0 {
		if end := strings.LastIndex(s, "}"); end > start {
			s = s[start : end+1]
		}
	}
	var obj map[string]any
	if err := json.Unmarshal([]byte(s), &obj); err != nil {
		return nil, err
	}
	return obj, nil
}

// jsonStr 取对象字符串字段（缺失/类型不符返回空串）。
func jsonStr(obj map[string]any, key string) string {
	v, ok := obj[key].(string)
	if !ok {
		return ""
	}
	return v
}

// jsonStrSlice 取对象字符串数组字段（非空项）。
func jsonStrSlice(obj map[string]any, key string) []string {
	arr, ok := obj[key].([]any)
	if !ok {
		return nil
	}
	out := make([]string, 0, len(arr))
	for _, item := range arr {
		if s, ok := item.(string); ok && strings.TrimSpace(s) != "" {
			out = append(out, s)
		}
	}
	return out
}

// errEmptyJSON 空输出（调用方各自降级）。
var errEmptyJSON = &emptyJSONError{}

type emptyJSONError struct{}

func (*emptyJSONError) Error() string { return "LLM 返回为空" }
