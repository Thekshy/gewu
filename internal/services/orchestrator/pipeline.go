package orchestrator

// 编排管线（PARITY §4 逐字结构；各分支按阶段接入）。
// P2：会话（conversation RPC）+ transaction/hybrid 全量迁移；
// 检索尚未接入（P3）——直答/深研为空库语义（NO_DATA），工具兜底同理。

import (
	"context"
	"strings"
	"time"

	generatev1 "gewu/pkg/gen/gewu/generate/v1"
	orchestratorv1 "gewu/pkg/gen/gewu/orchestrator/v1"

	"gewu/internal/agent"

	"go.uber.org/zap"
)

// runChat 一轮完整编排。t0 在编排入口（决策 B）。
func (s *Server) runChat(ctx context.Context, send func(*orchestratorv1.ChatResponse) error, req *orchestratorv1.ChatRequest, hasKey bool, t0 time.Time) error {
	question, mode, sessionID, role := req.Question, req.Mode, req.SessionId, req.Role
	user := "demo-" + role

	// 审计：收集答案增量，轮末落库（尽力而为）
	var answer strings.Builder
	emit := func(ev *orchestratorv1.ChatResponse) error {
		if d, ok := ev.GetKind().(*orchestratorv1.ChatResponse_AnswerDelta); ok {
			answer.WriteString(d.AnswerDelta.GetText())
		}
		return send(ev)
	}
	finish := func() error {
		if a := []rune(answer.String()); len(a) > 0 {
			_ = s.sessions.AppendMessage(ctx, sessionID, "assistant", string(a[:minRunes(len(a), 4000)]))
		}
		return nil
	}
	_ = s.sessions.AppendMessage(ctx, sessionID, "user", question) // 审计尽力而为

	// 1) 办理流程进行中：优先把消息解释为对流程的回应
	if sess, err := s.sessions.Get(ctx, sessionID); err != nil {
		s.log.Warn("读取会话失败", zap.Error(err))
	} else if sess != nil && (sess.Phase == PhaseCollect || sess.Phase == PhaseConfirm) {
		switch s.classifyReply(ctx, question, sess, hasKey) {
		case "continue":
			if err := emit(routeEvent("transaction", "继续办理："+flowLabel(sess.Tool), false)); err != nil {
				return err
			}
			if err := s.handleReply(ctx, emit, sess, question, hasKey); err != nil {
				return err
			}
			s.persistSession(ctx, sess)
			if err := emit(doneEvent(elapsedMS(t0))); err != nil {
				return err
			}
			return finish()
		case "cancel":
			s.clearSession(ctx, sess)
			if err := emit(routeEvent("transaction", "用户取消办理", false)); err != nil {
				return err
			}
			if err := emit(answerEvent("好的，已取消本次办理。有别的事随时找我。")); err != nil {
				return err
			}
			if err := emit(doneEvent(elapsedMS(t0))); err != nil {
				return err
			}
			return finish()
		default:
			s.clearSession(ctx, sess) // 切换新话题：放弃流程，走正常路由
		}
	}

	// 2) 路由
	var result agent.RouteResult
	if mode == "direct" || mode == "research" {
		result = agent.RouteResult{Route: mode, Reason: "用户指定 " + mode, ByLLM: false}
	} else {
		result = s.routeQuestion(ctx, question, hasKey)
	}
	if err := emit(routeEvent(result.Route, result.Reason, result.ByLLM)); err != nil {
		return err
	}

	// 3) 分发
	switch result.Route {
	case "refusal":
		if err := emit(answerEvent(agent.RefusalAnswer)); err != nil {
			return err
		}
		if err := emit(citationsEvent(nil)); err != nil {
			return err
		}
	case "factual":
		if err := s.answerDirect(ctx, emit, question, 0, hasKey); err != nil {
			return err
		}
	case "research":
		if err := s.runResearch(ctx, emit, question, hasKey); err != nil {
			return err
		}
	case "hybrid":
		if err := emit(statusEvent("先回答你的政策问题…")); err != nil {
			return err
		}
		if err := s.answerDirect(ctx, emit, question, 0, hasKey); err != nil {
			return err
		}
		if err := emit(statusEvent("接下来为你办理业务…")); err != nil {
			return err
		}
		if err := s.startFlow(ctx, emit, question, role, user, sessionID, hasKey); err != nil {
			return err
		}
	case "transaction":
		if err := s.startFlow(ctx, emit, question, role, user, sessionID, hasKey); err != nil {
			return err
		}
	default:
		if err := emit(errorEvent("未知路由：" + result.Route)); err != nil {
			return err
		}
	}

	// 4) done
	if err := emit(doneEvent(elapsedMS(t0))); err != nil {
		return err
	}
	return finish()
}

// routeQuestion 五分类路由：有 key 走 LLM（json/temp 0/max_tokens 200/small，
// 提示词取自 agent_config——默认行与 prompts.go 逐字一致），无 key 或
// 调用/解析失败降级启发式（internal/agent 冻结实现，行为同源）。
func (s *Server) routeQuestion(ctx context.Context, question string, hasKey bool) agent.RouteResult {
	if !hasKey {
		return agent.HeuristicRoute(question)
	}
	raw, err := s.generate.Chat(ctx, &generatev1.ChatRequest{
		Messages: []*generatev1.Message{
			{Role: "system", Content: s.cfgStore.routerPrompt(ctx)},
			{Role: "user", Content: question},
		},
		Options: &generatev1.Options{JsonMode: true, Temperature: 0, MaxTokens: 200, Small: true},
	})
	if err == nil {
		if obj, perr := parseJSONObject(raw.GetContent()); perr == nil {
			route := jsonStr(obj, "route")
			if validRoutes[route] {
				reason := jsonStr(obj, "reason")
				if len([]rune(reason)) > 100 { // reason 截断 100 字
					reason = string([]rune(reason)[:100])
				}
				return agent.RouteResult{Route: route, Reason: reason, ByLLM: true}
			}
		}
	} else {
		s.log.Warn("路由器 LLM 调用失败，降级启发式路由", zap.Error(err))
	}
	return agent.HeuristicRoute(question)
}

var validRoutes = map[string]bool{
	"factual": true, "research": true, "refusal": true, "transaction": true, "hybrid": true,
}

func minRunes(a, b int) int {
	if a < b {
		return a
	}
	return b
}
