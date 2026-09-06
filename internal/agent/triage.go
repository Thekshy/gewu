package agent

import (
	"context"
	"log"

	"gewu/internal/llm"
)

// P7+/agent-first 改造：执行策略分流器（triage）。
//
// 动机：五分类（factual/research/transaction/hybrid/refusal）把"业务意图"当成
// 一次性前置决策——hybrid 是组合意图的补丁、误路由会锁死后续路径、每加一类
// 意图都要改代码。agent-first 把分类空间塌缩为三条**执行策略**：
//
//	refusal：与校园无关（安全护栏，不可塌缩）→ 固定拒答；
//	direct ：一次知识检索即可回答 → 便宜链路 AnswerDirect（与 workflow 的
//	         factual 链路同一条，成本最低）；
//	agent  ：需要业务工具 / 多步 / 组合 / 不确定 → ReAct 引擎自主组合工具
//	         （写操作外挂确认流），低置信 fail-open 到 agent 而不是猜错窄路。
//
// ROUTER_MODE=agent-first 启用；cascade/classic 保留为可回退基线。
// route 事件标签：refusal | factual(direct) | agent——对前端/评测仍是字符串
// route 字段，语义从"业务意图"变为"执行策略"。

// TriageSystem 三选一执行策略提示词。
const TriageSystem = `你是校园问答系统「格物」的执行策略分流器。判断用户消息应走哪条执行链路：

- refusal：与大学校园学习生活完全无关（股市行情、写代码、写邮件、闲聊）。
  注意：只要涉及校园政策/教务/生活服务（转专业、保研、图书馆、请假、场馆、
  医保、宿舍……），无论多复杂、多口语化都不是 refusal。
- direct：单一政策/事实查询，一次知识检索即可回答；同一主题的并列小问也算 direct。
- agent：以下任一：① 需要调用业务系统（预约/取消场馆、请假、查我的预约、
  待审批列表等实时业务数据）；② 需要多步或跨多份文件综合比较；
  ③ 办理与咨询混合（如"帮我请假，顺便问下政策"）；④ 拿不准时选 agent。

只输出 JSON：{"choice":"refusal|direct|agent","reason":"一句话"}`

// TriageRoute 三选一执行策略分流：规则快路径 → LLM 三分类 → 失败 fail-open 到 agent。
func (d *Deps) TriageRoute(ctx context.Context, question string) RouteDecision {
	// 规则快路径：明确办理指令直接进 agent（省一次 LLM；agent 自然处理
	// 办理+咨询组合，不再需要 hybrid 这个补丁类别）。
	if exactTxRe.MatchString(question) {
		dec := RouteDecision{Route: "agent", Confidence: 1.0, Layer: "triage-rule",
			Reason: "规则快路径：明确办理指令", ByLLM: false, ModelTier: "flagship"}
		return dec
	}

	// 无 LLM（测试/健壮性兜底）：启发式映射到三策略。
	if d.LLM == nil || !d.LLM.HasKey() {
		h := HeuristicRoute(question)
		return RouteDecision{Route: strategyFromHeuristic(h.Route), Confidence: 0.5,
			Layer: "heuristic-fallback", Reason: h.Reason, ModelTier: "standard"}
	}

	raw, err := d.LLM.Chat(ctx, []llm.Message{
		{Role: "system", Content: TriageSystem},
		{Role: "user", Content: question},
	}, llm.Options{JSONMode: true, Temperature: 0, MaxTokens: 150, Small: true})
	if err != nil {
		log.Printf("[agent] triage 调用失败，fail-open 到 agent：%v", err)
		return triageDecision("agent", 0, "triage-open", "分流器不可用，交给 agent 自主处理")
	}
	obj, perr := parseJSONObject(raw)
	if perr != nil {
		return triageDecision("agent", 0, "triage-open", "分流器输出无法解析，交给 agent 自主处理")
	}
	choice := jsonStr(obj, "choice")
	reason := jsonStr(obj, "reason")
	if len([]rune(reason)) > 100 {
		reason = string([]rune(reason)[:100])
	}
	switch choice {
	case "refusal":
		// 安全网（与级联路由同逻辑）：refusal 是代价最高的误路由。问题带办理
		// 强动词或校园领域词时与 refusal 直接矛盾——fail-open 到 agent。
		if reqRe.MatchString(question) || txVerbsRe.MatchString(question) || campusDomainRe.MatchString(question) {
			return triageDecision("agent", 0, "triage-guard",
				"refusal 判定与校园领域词矛盾，交给 agent 处理")
		}
		return triageDecision("refusal", 0.9, "triage-llm", reason)
	case "direct":
		return triageDecision("factual", 0.9, "triage-llm", reason)
	case "agent":
		return triageDecision("agent", 0.9, "triage-llm", reason)
	default:
		return triageDecision("agent", 0, "triage-open", "分流器输出未知选项，交给 agent 自主处理")
	}
}

// triageDecision 三策略决策包：agent 全能力（flagship + 不限工具集），
// direct 走标准直答，refusal 轻量。
func triageDecision(route string, conf float64, layer, reason string) RouteDecision {
	dec := RouteDecision{Route: route, Confidence: conf, Layer: layer, Reason: reason, ByLLM: layer != "triage-rule"}
	switch route {
	case "agent":
		dec.ModelTier = "flagship" // 自主循环用主模型
	case "factual":
		dec.PreRAG, dec.ModelTier = true, "standard"
	case "refusal":
		dec.ModelTier = "small"
	}
	return dec
}

// strategyFromHeuristic 旧五分类启发式 → 三策略映射（无 LLM 兜底用）。
func strategyFromHeuristic(route string) string {
	switch route {
	case "transaction", "hybrid", "research":
		return "agent"
	case "refusal":
		return "refusal"
	default:
		return "factual"
	}
}
