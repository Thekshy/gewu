package routing

import (
	"encoding/json"
	"strings"
)

// 容错解析 LLM 输出的 JSON（与 agent/jsonx.go 同源复制——routing 不得反向
// 依赖编排域，两个 ~30 行的通用 helper 不值得为此抽第三个包）。

// parseJSONObject 剥离 ```json 围栏、截取首个 { 到末个 } 之间的内容再解析。
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

// errEmptyJSON 空输出（调用方各自降级）。
var errEmptyJSON = &emptyJSONError{}

type emptyJSONError struct{}

func (*emptyJSONError) Error() string { return "LLM 返回为空" }
