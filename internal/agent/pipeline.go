package agent

import (
	"context"
	"fmt"
	"time"

	"gewu/internal/business"
	"gewu/internal/config"
	"gewu/internal/llm"
	"gewu/internal/rag"
)

// Deps 聚合编排层依赖，进程内单例（由 cmd/server 装配）。
// 业务系统只能经 Tools（工具层）访问——权限矩阵的单一出口。
type Deps struct {
	Settings  *config.Settings
	LLM       *llm.Client // nil 或未配 key = 零 key 演示模式
	Retriever *rag.Retriever
	Business  *business.Business
	Sessions  *SessionStore
	Tools     map[string]Tool
}

// NewDeps 装配编排层。
func NewDeps(s *config.Settings, lc *llm.Client, r *rag.Retriever, b *business.Business) *Deps {
	d := &Deps{
		Settings:  s,
		LLM:       lc,
		Retriever: r,
		Business:  b,
		Sessions:  NewSessionStore(),
	}
	d.Tools = toolsFor(d)
	return d
}

// HasKey 是否启用 LLM。
func (d *Deps) HasKey() bool { return d.LLM != nil && d.LLM.HasKey() }

// RunChat 会话编排入口（SSE 接口与评测复用同一入口，PARITY §4）。
//
// emit 逐事件回调（返回错误时立即中止——SSE 客户端断开时借 ctx 取消传播到
// 上游 LLM 流）。整体耗时通过 done 事件的 latency_ms 返回。
func (d *Deps) RunChat(ctx context.Context, emit emitFn, question, mode, sessionID, role, user string) {
	t0 := time.Now()
	if user == "" {
		user = "demo-" + role
	}
	if err := d.runChatInner(ctx, emit, question, mode, sessionID, role, user, t0); err != nil {
		// 异常兜底：error 事件 + done（与 Python 的 except 行为一致）。
		// emit 失败（客户端已断开）在此静默忽略。
		_ = emit(errorEvt(err.Error()))
		_ = emit(doneEvt(elapsedMS(t0)))
	}
}

func elapsedMS(t0 time.Time) int64 { return time.Since(t0).Milliseconds() }

func (d *Deps) runChatInner(ctx context.Context, emit emitFn, question, mode, sessionID, role, user string, t0 time.Time) error {
	// 1) 办理流程进行中：优先把消息解释为对流程的回应
	if sess := d.Sessions.Get(sessionID); sess != nil && (sess.Phase == PhaseCollect || sess.Phase == PhaseConfirm) {
		switch d.ClassifyReply(ctx, question, sess) {
		case "continue":
			if err := emit(routeEvt("transaction", "继续办理："+flowLabel(sess.Tool), false)); err != nil {
				return err
			}
			if err := d.HandleReply(ctx, emit, sess, question); err != nil {
				return err
			}
			return emit(doneEvt(elapsedMS(t0)))
		case "cancel":
			d.Sessions.Clear(sessionID)
			if err := emit(routeEvt("transaction", "用户取消办理", false)); err != nil {
				return err
			}
			if err := emit(answerEvt("好的，已取消本次办理。有别的事随时找我。")); err != nil {
				return err
			}
			return emit(doneEvt(elapsedMS(t0)))
		default:
			d.Sessions.Clear(sessionID) // 切换新话题：放弃流程，走正常路由
		}
	}

	// 2) 路由
	var result RouteResult
	if mode == "direct" || mode == "research" {
		result = RouteResult{Route: mode, Reason: "用户指定 " + mode, ByLLM: false}
	} else {
		result = d.RouteQuestion(ctx, question)
	}
	if err := emit(routeEvt(result.Route, result.Reason, result.ByLLM)); err != nil {
		return err
	}

	// 3) 分发
	switch result.Route {
	case "refusal":
		if err := emit(answerEvt(RefusalAnswer)); err != nil {
			return err
		}
		if err := emit(citationsEvt(nil)); err != nil {
			return err
		}
	case "factual":
		if err := d.AnswerDirect(ctx, emit, question, d.Retriever.K); err != nil {
			return err
		}
	case "research":
		if err := d.RunResearch(ctx, emit, question, 5); err != nil {
			return err
		}
	case "hybrid":
		if err := emit(statusEvt("先回答你的政策问题…")); err != nil {
			return err
		}
		if err := d.AnswerDirect(ctx, emit, question, d.Retriever.K); err != nil {
			return err
		}
		if err := emit(statusEvt("接下来为你办理业务…")); err != nil {
			return err
		}
		if err := d.StartFlow(ctx, emit, question, role, user, sessionID); err != nil {
			return err
		}
	case "transaction":
		if err := d.StartFlow(ctx, emit, question, role, user, sessionID); err != nil {
			return err
		}
	default:
		if err := emit(errorEvt(fmt.Sprintf("未知路由：%s", result.Route))); err != nil {
			return err
		}
	}

	// 4) done
	if err := emit(doneEvt(elapsedMS(t0))); err != nil {
		return err
	}
	return nil
}

// flowLabel 工具 → 流程中文名（续轮 route 事件的 reason 用）。
func flowLabel(tool string) string {
	if f, ok := flowDefs[tool]; ok {
		return f.Label
	}
	return tool
}
