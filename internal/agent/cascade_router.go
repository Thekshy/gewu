package agent

import (
	"context"
	"log"
	"regexp"
	"sort"

	"gewu/internal/llm"
)

// P6 阶段2：三级级联意图路由（业界主流形态）。
//
//	L0 规则快路径：只接"几乎不可能错"的高置信精确 case，命中省一次 LLM；
//	L1 小 LLM 分类：输出五类概率分布（不再是单个 label），双阈值 + top1-top2
//	   margin 判定，类别纠缠（margin 不足）或低置信升级 L2；
//	L2 主模型灰度兜底：只吃 L1 落灰度区的少量流量，few-shot 二次判定；
//	   仍不确定 → factual + flagship 档（转通用链路，不做自由发挥）。
//
// 输出是路由决策包 RouteDecision（不只是 label）：下游编排据此决定
// agent/工具集/是否前置 RAG/模型档。ROUTER_MODE=classic 保留旧单次分类可回退。

// RouteDecision 路由决策包。
type RouteDecision struct {
	Route      string  // factual|research|transaction|hybrid|refusal
	Confidence float64 // 0~1，L0=1.0；L1 来自模型概率；L2 来自主模型判定
	Layer      string  // L0-rule | L1-llm | L2-main | L2-uncertain | heuristic-fallback
	Reason     string
	ByLLM      bool     // route 事件 by_llm 语义：是否由模型判定
	PreRAG     bool     // 是否前置检索
	Toolset    []string // 允许的工具子集（空=不限制）
	ModelTier  string   // small | standard | flagship
}

// 双阈值与 margin（生产可挪进 agent_config 热调；当前为编译期常量保证确定性）。
const (
	confHigh  = 0.80 // ≥ 且 margin 足够 → 直接路由
	confLow   = 0.55 // < → 不相信 L1，走 L2/兜底
	marginMin = 0.15 // top1-top2 概率间隔，小于则视为"类别纠缠"
)

// routeOrder 固定类别序：解析与排序的确定性基础（平局按此序取先）。
var routeOrder = []string{"factual", "research", "transaction", "hybrid", "refusal"}

// campusDomainRe 校园领域实体词：问题命中领域词时 L1 的 refusal 判定不可信
// （拒答是代价最高的误路由，"我的情况符合转专业条件吗"曾被 flash 高置信误判
// refusal），升级 L2 复核——与办理强动词安全网同一逻辑。
var campusDomainRe = regexp.MustCompile(`转专业|绩点|保研|研究生|推免|奖学金|助学金|图书馆|借阅|宿舍|门禁|校历|学分|选课|考试|挂科|补考|辅修|双学位|交流|交换|论文|答辩|学位|体测|体育|医保|校医|心理咨询|一卡通|校园卡|请假|销假|场馆|体育馆|羽毛球|篮球|游泳|研讨间`)

// exactTxRe L0 高置信精确规则：办理强动词开头 + 明确办理动作，
// 只接"几乎不可能错"的 case（区别于 HeuristicRoute 的宽信号）。
var exactTxRe = regexp.MustCompile(`^(帮我|我要|我想|给我|麻烦).*(预约|预订|请假|销假|退订|取消预约|提交请假)`)

// reactPlanRe "目标明确但路径不定"的办理信号：要求系统自主规划/安排，
// REACT_MODE=on 时把这类 transaction 问题交给 ReAct 引擎。
var reactPlanRe = regexp.MustCompile(`安排|规划|推荐一下|帮我定|帮我挑|顺便|把.*都|一并`)

// transactionToolset 办理类路由允许的工具子集（与 toolsFor 的真实工具名对齐，
// ReAct 引擎与续轮流程都受它约束——最小权限）。
var transactionToolset = []string{
	"query_venues", "my_bookings", "leave_status", "pending_leaves",
	"book_venue", "cancel_booking", "submit_leave", "approve_leave",
}

// CascadeRoute 三级级联路由。LLM 缺失（测试/健壮性兜底）时退启发式。
func (d *Deps) CascadeRoute(ctx context.Context, question string) RouteDecision {
	// L0：规则快路径（毫秒、零成本、高置信）。
	// 办理强动词 + 明确咨询并存 → hybrid（与冻结基线 HeuristicRoute 同规则，26 题验证）；
	// 纯办理指令 → transaction。
	if exactTxRe.MatchString(question) {
		if consultRe.MatchString(question) {
			dec := RouteDecision{Route: "hybrid", Confidence: 1.0, Layer: "L0-rule",
				Reason: "规则快路径：办理诉求 + 政策咨询", ByLLM: false, ModelTier: "small"}
			dec.fillPolicy()
			return dec
		}
		return RouteDecision{Route: "transaction", Confidence: 1.0, Layer: "L0-rule",
			Reason: "规则快路径：明确办理指令", ByLLM: false,
			PreRAG: false, Toolset: append([]string{}, transactionToolset...), ModelTier: "small"}
	}

	// 无 key（正常启动已拦截，这里只为测试与健壮性）退启发式。
	if d.LLM == nil || !d.LLM.HasKey() {
		h := HeuristicRoute(question)
		return RouteDecision{Route: h.Route, Confidence: 0.5, Layer: "heuristic-fallback",
			Reason: h.Reason, ByLLM: false, ModelTier: "small"}
	}

	// L1：小 LLM 输出概率分布。
	dec, top1, top2 := d.l1Classify(ctx, question)
	margin := top1 - top2
	// 安全网：refusal 是代价最高的误路由（直接拒绝服务）。问题带办理/请求
	// 强动词或校园领域实体词时，refusal 判定与之直接矛盾——不直接采信，升级 L2 复核。
	if dec.Route == "refusal" && (reqRe.MatchString(question) || txVerbsRe.MatchString(question) || campusDomainRe.MatchString(question)) {
		top1 = 0 // 降入灰度区，走下方 L2 逻辑
		margin = 0
	}
	if top1 >= confHigh && margin >= marginMin {
		dec.Layer, dec.ByLLM = "L1-llm", true
		dec.fillPolicy()
		return dec
	}
	if top1 < confLow || margin < marginMin {
		// L2：主模型二次判定（灰度区，只吃少量流量）。
		if l2, ok := d.l2Arbitrate(ctx, question); ok {
			l2.Layer, l2.ByLLM = "L2-main", true
			l2.fillPolicy()
			return l2
		}
		// 仍不确定：转通用链路并提高模型档（不自由发挥、不反问阻断）。
		return RouteDecision{Route: "factual", Confidence: top1, Layer: "L2-uncertain",
			Reason: "灰度区未决，转通用链路并提高模型档", ByLLM: true,
			PreRAG: true, ModelTier: "flagship"}
	}
	// 中间带：置信足够且 margin 足够，直接采信 L1。
	dec.Layer, dec.ByLLM = "L1-llm", true
	dec.fillPolicy()
	return dec
}

// l1Classify 小模型输出五类概率（不输出单个 label）；返回决策与 top1/top2 概率。
func (d *Deps) l1Classify(ctx context.Context, q string) (RouteDecision, float64, float64) {
	raw, err := d.LLM.Chat(ctx, []llm.Message{
		{Role: "system", Content: RouterSystemCascade},
		{Role: "user", Content: q},
	}, llm.Options{JSONMode: true, Temperature: 0, MaxTokens: 200, Small: true})
	if err != nil {
		log.Printf("[agent] L1 分类调用失败，降级启发式：%v", err)
		h := HeuristicRoute(q)
		return RouteDecision{Route: h.Route, Layer: "heuristic-fallback", Reason: h.Reason,
			ModelTier: "small"}, hConf, 0
	}
	return parseRouteScores(raw)
}

// l2Arbitrate 主模型 few-shot 二次判定：复用经典 RouterSystem（内含丰富判定
// 准则与示例），输出单个 route；解析失败返回 ok=false 交由上层兜底。
func (d *Deps) l2Arbitrate(ctx context.Context, q string) (RouteDecision, bool) {
	raw, err := d.LLM.Chat(ctx, []llm.Message{
		{Role: "system", Content: RouterSystem},
		{Role: "user", Content: q},
	}, llm.Options{JSONMode: true, Temperature: 0, MaxTokens: 200})
	if err != nil {
		log.Printf("[agent] L2 二次判定调用失败：%v", err)
		return RouteDecision{}, false
	}
	obj, perr := parseJSONObject(raw)
	if perr != nil {
		return RouteDecision{}, false
	}
	route := jsonStr(obj, "route")
	if !validRoutes[route] {
		return RouteDecision{}, false
	}
	reason := jsonStr(obj, "reason")
	if len([]rune(reason)) > 100 {
		reason = string([]rune(reason)[:100])
	}
	return RouteDecision{Route: route, Confidence: l2Confidence, Reason: reason}, true
}

// 置信度取值：L2 主模型判定成功给固定高置信；L1 失败退启发式时给最低档。
const (
	l2Confidence  = 0.9
	hConf         = 0.5
	reasonLimitRN = 100
)

// parseRouteScores 解析 {"scores":{五类概率},"reason":..}。
// 按固定类别序稳定排序取 top1/top2（平局取先出现者，保证确定性）。
func parseRouteScores(raw string) (RouteDecision, float64, float64) {
	obj, err := parseJSONObject(raw)
	if err != nil {
		h := HeuristicRoute(raw)
		return RouteDecision{Route: h.Route, Layer: "heuristic-fallback", Reason: h.Reason}, hConf, 0
	}
	scores, _ := obj["scores"].(map[string]any)
	type pair struct {
		route string
		p     float64
	}
	var pairs []pair
	for _, r := range routeOrder {
		if v, ok := toFloatAny(scores[r]); ok {
			pairs = append(pairs, pair{r, v})
		}
	}
	if len(pairs) == 0 {
		h := HeuristicRoute("")
		return RouteDecision{Route: h.Route, Layer: "heuristic-fallback", Reason: h.Reason}, hConf, 0
	}
	sort.SliceStable(pairs, func(i, j int) bool { return pairs[i].p > pairs[j].p })
	top1, top2 := pairs[0].p, 0.0
	if len(pairs) > 1 {
		top2 = pairs[1].p
	}
	reason := jsonStr(obj, "reason")
	if len([]rune(reason)) > reasonLimitRN {
		reason = string([]rune(reason)[:reasonLimitRN])
	}
	return RouteDecision{Route: pairs[0].route, Confidence: top1, Reason: reason}, top1, top2
}

// fillPolicy 路由类别 → 处理策略（决策包的"策略"部分，集中维护）。
func (r *RouteDecision) fillPolicy() {
	switch r.Route {
	case "factual":
		r.PreRAG, r.ModelTier = true, "standard"
	case "research":
		r.PreRAG, r.ModelTier = true, "flagship"
	case "transaction", "hybrid":
		r.Toolset, r.ModelTier = append([]string{}, transactionToolset...), "small"
	case "refusal":
		r.ModelTier = "small"
	}
}

// toFloatAny JSON 数值容错（概率可能输出为字符串）。
func toFloatAny(v any) (float64, bool) {
	switch x := v.(type) {
	case float64:
		return x, true
	case int:
		return float64(x), true
	case string:
		// 字符串数字不参与概率判定（模型未按格式输出时宁可走兜底）
		return 0, false
	}
	return 0, false
}
