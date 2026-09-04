package rag

import (
	"context"

	generatev1 "gewu/pkg/gen/gewu/generate/v1"

	"gewu/internal/llm"
)

// generateAdapter generate 服务的 LLMer 适配（决策 A 接口的服务端注入侧）：
// 检索编排（改写/embed）与冻结单体共用同一套代码，LLM 调用改经 RPC。
type generateAdapter struct {
	cli    generatev1.GenerateServiceClient
	hasKey bool
}

func newGenerateAdapter(cli generatev1.GenerateServiceClient) *generateAdapter {
	return &generateAdapter{cli: cli}
}

// refreshKey 从 BudgetStatus 同步 key 状态（启动与每次检索前惰性刷新可接受）。
func (g *generateAdapter) refreshKey(ctx context.Context) {
	if resp, err := g.cli.BudgetStatus(ctx, &generatev1.BudgetStatusRequest{}); err == nil {
		g.hasKey = resp.GetHasKey()
	}
}

// HasKey 是否配置 LLM key。
func (g *generateAdapter) HasKey() bool { return g.hasKey }

// Chat 同步补全（查询改写用）。
func (g *generateAdapter) Chat(ctx context.Context, messages []llm.Message, o llm.Options) (string, error) {
	msgs := make([]*generatev1.Message, 0, len(messages))
	for _, m := range messages {
		msgs = append(msgs, &generatev1.Message{Role: m.Role, Content: m.Content})
	}
	resp, err := g.cli.Chat(ctx, &generatev1.ChatRequest{Messages: msgs, Options: toProtoOptions(o)})
	if err != nil {
		return "", err
	}
	return resp.GetContent(), nil
}

// Embed 批量向量化。
func (g *generateAdapter) Embed(ctx context.Context, texts []string) ([][]float64, error) {
	resp, err := g.cli.Embed(ctx, &generatev1.EmbedRequest{Texts: texts})
	if err != nil {
		return nil, err
	}
	out := make([][]float64, 0, len(resp.GetVectors()))
	for _, v := range resp.GetVectors() {
		out = append(out, v.GetValues())
	}
	return out, nil
}

func toProtoOptions(o llm.Options) *generatev1.Options {
	return &generatev1.Options{
		JsonMode:    o.JSONMode,
		Temperature: o.Temperature,
		MaxTokens:   int32(o.MaxTokens),
		Small:       o.Small,
	}
}
