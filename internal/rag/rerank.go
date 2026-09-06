package rag

import (
	"context"
	"encoding/json"
	"fmt"
	"strconv"
	"strings"

	"gewu/internal/llm"
)

// P6 阶段3：LLM 精排（rerank）。
//
// 漏斗：BM25/向量各取 20 → RRF 粗排 topN → rerank 精排到 topK → 父子扩展。
// Reranker 抽象成接口：当前实现是 glm-5.3-flash 批量 pointwise 打分
// （全部候选拼进一个 prompt，只 1 次 LLM 调用）；以后可换 cross-encoder
// （bge-reranker / Cohere rerank）而不动检索编排。

// rerankSystem 精排打分提示词：输出与候选一一对应的整数分数数组。
const rerankSystem = `你是检索结果的相关性打分器。给你一个查询和若干编号候选段落，对每条候选打 0~10 的整数相关性分：
- 10：直接包含回答该查询所需的核心条款/数字/流程；
- 5：主题相关但只是背景信息；
- 0：与查询无关。
只输出 JSON：{"scores":[n1, n2, ...]}，scores 与候选编号一一对应、长度相同。`

// Reranker 精排器接口：对候选文本逐条打分，返回与输入等长且同序的分数。
type Reranker interface {
	Rerank(ctx context.Context, query string, candidates []string) ([]float64, error)
}

// LLMReranker 基于 OpenAI 兼容 Chat 端点的批量 pointwise 精排器。
// 小模型（Options.Small）一次调用；解析失败返回错误，由调用方退回粗排顺序（不阻断）。
type LLMReranker struct {
	Client LLMer
}

// NewLLMReranker 构造 LLM 精排器。
func NewLLMReranker(client LLMer) *LLMReranker {
	return &LLMReranker{Client: client}
}

// Rerank 批量打分：候选带编号拼入一个 prompt，解析 {"scores":[...]}。
func (r *LLMReranker) Rerank(ctx context.Context, query string, candidates []string) ([]float64, error) {
	if len(candidates) == 0 {
		return nil, nil
	}
	var sb strings.Builder
	sb.WriteString("查询：" + query + "\n\n候选段落：\n")
	for i, c := range candidates {
		sb.WriteString(fmt.Sprintf("[%d] %s\n\n", i+1, c))
	}
	raw, err := r.Client.Chat(ctx, []llm.Message{
		{Role: "system", Content: rerankSystem},
		{Role: "user", Content: sb.String()},
	}, llm.Options{JSONMode: true, Temperature: 0, MaxTokens: 300, Small: true})
	if err != nil {
		return nil, err
	}
	return parseScores(raw, len(candidates))
}

// parseScores 解析 {"scores":[...]}，容错剥离围栏/前后缀（与 agent.jsonx 同策略）。
// 长度不符或含非法分数视为解析失败（调用方退回粗排顺序）。
func parseScores(raw string, n int) ([]float64, error) {
	s := strings.TrimSpace(raw)
	if start := strings.Index(s, "{"); start >= 0 {
		if end := strings.LastIndex(s, "}"); end > start {
			s = s[start : end+1]
		}
	}
	var obj struct {
		Scores []any `json:"scores"`
	}
	if err := json.Unmarshal([]byte(s), &obj); err != nil {
		return nil, err
	}
	if len(obj.Scores) != n {
		return nil, fmt.Errorf("分数个数 %d 与候选数 %d 不符", len(obj.Scores), n)
	}
	out := make([]float64, n)
	for i, v := range obj.Scores {
		f, ok := toFloat(v)
		if !ok || f < 0 || f > 10 {
			return nil, fmt.Errorf("第 %d 个分数非法: %v", i, v)
		}
		out[i] = f
	}
	return out, nil
}

// toFloat JSON 数值容错转换（LLM 偶尔会把分数输出成字符串）。
func toFloat(v any) (float64, bool) {
	switch x := v.(type) {
	case float64:
		return x, true
	case int:
		return float64(x), true
	case string:
		f, err := strconv.ParseFloat(strings.TrimSpace(x), 64)
		return f, err == nil
	}
	return 0, false
}
