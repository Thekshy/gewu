package orchestrator

import (
	"context"
	"time"

	generatev1 "gewu/pkg/gen/gewu/generate/v1"
	orchestratorv1 "gewu/pkg/gen/gewu/orchestrator/v1"

	"gewu/internal/agent"

	"go.uber.org/zap"
)

// runChat 编排管线（PARITY §4 结构逐字；各分支按阶段接入）。
//
// P1 裸管线：续轮意图（P2）与工具调度（P4）未接；refusal 走固定话术；
// 其余路由直连 generate 流式作答（无检索——P3 接 rag 后换成
// AnswerDirect/RunResearch 的真实链路，届时删除本注释）。
// t0 在编排入口（决策 B），与冻结单体 RunChat 口径一致。
func (s *Server) runChat(ctx context.Context, send func(*orchestratorv1.ChatResponse) error, req *orchestratorv1.ChatRequest, hasKey bool, t0 time.Time) error {
	question, mode := req.Question, req.Mode

	// 1) 办理流程进行中（collect/confirm）→ P2 接 conversation 后补
	// 2) 路由
	var result agent.RouteResult
	if mode == "direct" || mode == "research" {
		result = agent.RouteResult{Route: mode, Reason: "用户指定 " + mode, ByLLM: false}
	} else {
		result = s.routeQuestion(ctx, question, hasKey)
	}
	if err := send(routeEvent(result.Route, result.Reason, result.ByLLM)); err != nil {
		return err
	}

	// 3) 分发
	switch result.Route {
	case "refusal":
		if err := send(answerEvent(agent.RefusalAnswer)); err != nil {
			return err
		}
		if err := send(citationsEvent(nil)); err != nil {
			return err
		}
	default:
		// P1 裸直答：零 key = 等价「空知识库的单体」→ NO_DATA；有 key = 直接流式
		if !hasKey {
			if err := send(answerEvent(agent.NoDataAnswer)); err != nil {
				return err
			}
			if err := send(citationsEvent(nil)); err != nil {
				return err
			}
			break
		}
		streamReq := &generatev1.ChatStreamRequest{
			Messages: []*generatev1.Message{
				{Role: "system", Content: agent.AnswerSystem},
				{Role: "user", Content: question},
			},
		}
		gs, err := s.generate.ChatStream(ctx, streamReq)
		if err != nil {
			return err
		}
		for {
			delta, err := gs.Recv()
			if err != nil {
				if err == errEOF {
					break
				}
				return err
			}
			if delta.Text != "" {
				if err := send(answerEvent(delta.Text)); err != nil {
					return err
				}
			}
		}
		if err := send(citationsEvent(nil)); err != nil {
			return err
		}
	}

	// 4) done
	return send(doneEvent(elapsedMS(t0)))
}

// routeQuestion 五分类路由：有 key 走 LLM（json/temp 0/max_tokens 200/small），
// 无 key 或调用/解析失败降级启发式（internal/agent 冻结实现，行为同源）。
func (s *Server) routeQuestion(ctx context.Context, question string, hasKey bool) agent.RouteResult {
	if !hasKey {
		return agent.HeuristicRoute(question)
	}
	raw, err := s.generate.Chat(ctx, &generatev1.ChatRequest{
		Messages: []*generatev1.Message{
			{Role: "system", Content: agent.RouterSystem},
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
