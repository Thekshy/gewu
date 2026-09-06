package routing

import (
	"context"
	"log"
	"regexp"
	"strings"

	"gewu/internal/llm"
)

// classic 模式：单次小模型五分类（可回退基线，PARITY §5）。

// RouteResult 路由结论。
type RouteResult struct {
	Route  string // factual | research | refusal | transaction | hybrid
	Reason string
	ByLLM  bool
}

// researchHints 复合问题的信号词：出现并列/递进/多条件时倾向于研究链路。
var researchHints = []string{
	"并且", "同时", "以及", "分别", "然后", "还要", "再加上",
	"又想", "还能", "会不会", "能不能", "影响",
}

var (
	// 办理动词 + 第一人称请求 → 办理；只是问政策 → 知识。
	txVerbsRe = regexp.MustCompile(`预约|预订|退订|取消预约|请假|事假|病假|销假|假申请|我的预约|待审批|批准`)
	reqRe     = regexp.MustCompile(`帮我|给我|我想|我要|麻烦|想请|想约|想订|帮我查|帮我看`)
	consultRe = regexp.MustCompile(`什么|怎么|为什么|是不是|需不需要|能不能|多少|谁|规定|要求|政策|意思`)
)

// HeuristicRoute 免 LLM 的降级路由（PARITY §5.2，顺序判定不可调换）。
// cascade 的 L1 失败与 triage 的无 LLM 兜底都复用这套规则。
func HeuristicRoute(question string) RouteResult {
	q := question
	if txVerbsRe.MatchString(q) {
		wantsAction := reqRe.MatchString(q)
		consulting := consultRe.MatchString(q)
		if wantsAction && consulting {
			return RouteResult{"hybrid", "启发式：办理诉求 + 政策咨询", false}
		}
		if wantsAction || !consulting {
			return RouteResult{"transaction", "启发式：业务办理诉求", false}
		}
		// 只咨询政策：落入知识问答
	}
	if len([]rune(q)) > 32 || containsAny(q, researchHints) {
		return RouteResult{"research", "启发式：长问题或含并列/多条件信号", false}
	}
	return RouteResult{"factual", "启发式：短事实型问题", false}
}

func containsAny(s string, subs []string) bool {
	for _, sub := range subs {
		if strings.Contains(s, sub) {
			return true
		}
	}
	return false
}

var validRoutes = map[string]bool{
	"factual": true, "research": true, "refusal": true, "transaction": true, "hybrid": true,
}

// RouteQuestion classic 模式：单次 LLM 五分类，无 key 或调用失败降级启发式。
func (d *Deps) RouteQuestion(ctx context.Context, question string) RouteResult {
	if d.LLM == nil || !d.LLM.HasKey() {
		return HeuristicRoute(question)
	}
	raw, err := d.LLM.Chat(ctx, []llm.Message{
		{Role: "system", Content: RouterSystem},
		{Role: "user", Content: question},
	}, llm.Options{JSONMode: true, Temperature: 0, MaxTokens: 200, Small: true})
	if err == nil {
		if obj, perr := parseJSONObject(raw); perr == nil {
			route := jsonStr(obj, "route")
			if validRoutes[route] {
				reason := jsonStr(obj, "reason")
				if len([]rune(reason)) > 100 { // reason 截断 100 字
					reason = string([]rune(reason)[:100])
				}
				return RouteResult{route, reason, true}
			}
		}
	} else {
		log.Printf("[routing] classic 分类调用失败，降级启发式：%v", err)
	}
	return HeuristicRoute(question)
}
