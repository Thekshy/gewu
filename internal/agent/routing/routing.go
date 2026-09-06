// Package routing 实现意图路由与执行策略分流。
//
// P9 从 agent 包拆出的真实接缝：路由只依赖 LLM 的 Chat 能力（级联 L1/L2、
// triage 三分类、classic 五分类三套实现 + 各自测试自成一体），不依赖编排域
// 的会话/工具/事件——lint-arch 守护 routing ↛ agent/rag/business。
//
// 三种模式（ROUTER_MODE，编排层经 Decide 分发）：
//
//	cascade（默认）：L0 规则快路径 → L1 五类概率（双阈值+margin）→ L2 主模型兜底；
//	classic：旧单次五分类（可回退基线）；
//	agent-first：三选一执行策略（refusal/direct/agent），低置信 fail-open 到 agent。
//
// 用户显式指定的 direct/research/react 模式由编排层处理，不经本包。
package routing

import (
	"context"

	"gewu/internal/llm"
)

// LLMer 路由对模型访问层的最小依赖（agent.LLMer 的子集，注入方天然满足）。
type LLMer interface {
	HasKey() bool
	Chat(ctx context.Context, messages []llm.Message, o llm.Options) (string, error)
}

// Deps 路由器依赖。
type Deps struct {
	LLM  LLMer
	Mode string // cascade（缺省）| classic | agent-first
}

// Decide 按 Mode 分发路由实现，输出路由决策包。
func (d *Deps) Decide(ctx context.Context, question string) RouteDecision {
	switch d.Mode {
	case "classic":
		return decisionFromClassic(d.RouteQuestion(ctx, question))
	case "agent-first":
		return d.TriageRoute(ctx, question)
	default:
		return d.CascadeRoute(ctx, question)
	}
}

// decisionFromClassic 旧 RouteResult → 决策包（classic 模式的适配层）。
func decisionFromClassic(r RouteResult) RouteDecision {
	dec := RouteDecision{Route: r.Route, Layer: "classic", Reason: r.Reason, ByLLM: r.ByLLM}
	dec.FillPolicy()
	return dec
}
