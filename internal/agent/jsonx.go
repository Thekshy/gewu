package agent

import (
	"encoding/json"
	"strings"
)

// parseJSONObject 容错解析 LLM 输出的 JSON 对象：
// 剥离 ```json 围栏、截取首个 { 到末个 } 之间的内容再解析。
// json_mode 正常输出无需容错；围栏截取只为个别端点的偏差兜底（不影响干净输入）。
func parseJSONObject(raw string) (map[string]any, error) {
	s := strings.TrimSpace(raw)
	if s == "" {
		return nil, ErrEmptyJSON
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

// jsonStrSlice 取对象字符串数组字段。
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

// jsonStrMap 取对象嵌套对象字段（槽位抽取 {"slots": {...}} 用）。
func jsonStrMap(obj map[string]any, key string) map[string]any {
	m, ok := obj[key].(map[string]any)
	if !ok {
		return map[string]any{}
	}
	return m
}

// errEmptyJSON 空输出（调用方各自降级）。
var ErrEmptyJSON = &emptyJSONError{}

type emptyJSONError struct{}

func (*emptyJSONError) Error() string { return "LLM 返回为空" }
