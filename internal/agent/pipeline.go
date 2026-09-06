package agent

import (
	"context"
	"fmt"
	"strings"
	"time"

	"gewu/internal/business"
	"gewu/internal/config"
	"gewu/internal/llm"
	"gewu/internal/rag"
)

// LLMer 编排层对 LLM 访问层的最小依赖（rag.LLMer 超集：多流式与原生工具调用）。
// 单体注入 *llm.Client 原样工作；单测注入 mock 实现，不发真实网络请求。
type LLMer interface {
	HasKey() bool
	Chat(ctx context.Context, messages []llm.Message, o llm.Options) (string, error)
	ChatStream(ctx context.Context, messages []llm.Message, o llm.Options, onDelta func(string) error) error
	ChatWithTools(ctx context.Context, messages []llm.Message, o llm.Options, tools []llm.ToolDef) (*llm.Completion, error)
	Embed(ctx context.Context, texts []string) ([][]float64, error)
}

// Deps 聚合编排层依赖，进程内单例（由 cmd/server 装配）。
// 业务系统只能经 Tools（工具层）访问——权限矩阵的单一出口。
type Deps struct {
	Settings  *config.Settings
	LLM       LLMer // nil 或未配 key = 调用会失败（启动时已强制有 key，此兜底为测试/健壮性保留）
	Retriever *rag.Retriever
	Business  *business.Business
	Sessions  SessionStore // 内存（默认/单测）或 SQLite（SESSION_STORE=sqlite，跨重启续办）
	Tools     map[string]Tool
	Memory    *MemoryStore // 长期记忆（nil = 不启用；单测可省）
}

// NewDeps 装配编排层。lc 为 *llm.Client（生产）或测试 mock（满足 LLMer）。
func NewDeps(s *config.Settings, lc LLMer, r *rag.Retriever, b *business.Business, mem *MemoryStore) *Deps {
	d := &Deps{
		Settings:  s,
		LLM:       lc,
		Retriever: r,
		Business:  b,
		Sessions:  NewSessionStore(),
		Memory:    mem,
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
	// 捕获本轮回答文本，供会话结束后异步固化长期记忆（不阻塞回答路径）。
	var answerSB strings.Builder
	wrapped := func(ev any) error {
		if a, ok := ev.(answerDeltaEvent); ok {
			answerSB.WriteString(a.Text)
		}
		return emit(ev)
	}
	err := d.runChatInner(ctx, wrapped, question, mode, sessionID, role, user, t0)
	if err != nil {
		// 异常兜底：error 事件 + done（与 Python 的 except 行为一致）。
		// emit 失败（客户端已断开）在此静默忽略。
		_ = emit(errorEvt(err.Error()))
		_ = emit(doneEvt(elapsedMS(t0)))
	}
	// 本轮对办理会话的全部修改（槽位/阶段/完成清除）落库：SQLite 后端据此
	// 跨重启续办；内存版为 no-op。失败只告警，不影响已发出的回答。
	if err := d.Sessions.Sync(); err != nil {
		logf(ctx, "会话状态落库失败（不影响本轮回答）：%v", err)
	}
	d.consolidateAsync(ctx, user, sessionID, question, answerSB.String())
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

	// 2) 上下文补全（多轮指代消解）：补全后的问题贯通路由与检索两个环节；
	//    原始问题保留给记忆存档（RunChat 的 consolidate 用原话）与用户可见层。
	q := question
	if resolved, ok := d.ResolveQuery(ctx, question, user, sessionID); ok {
		q = resolved
	}

	// 3) 路由：产出路由决策包（cascade 级联 / classic 旧单次分类 / 用户指定模式）
	dec := d.decideRoute(ctx, q, mode)
	if q != question {
		dec.Reason += "；已结合会话上下文补全指代"
	}
	if err := emit(routeDecisionEvt(dec)); err != nil {
		return err
	}

	// 4) 分发。ReAct 引擎（P6 阶段5）两种入口：
	//    mode=react 显式指定；REACT_MODE=on 时"目标明确但路径不定"的办理问题自动转自主循环。
	if mode == "react" || (d.Settings.ReactMode == "on" && dec.Route == "transaction" && reactPlanRe.MatchString(q)) {
		if err := d.RunReAct(ctx, emit, q, role, user, sessionID, dec.Toolset); err != nil {
			return err
		}
		return emit(doneEvt(elapsedMS(t0)))
	}

	switch dec.Route {
	case "refusal":
		if err := emit(answerEvt(RefusalAnswer)); err != nil {
			return err
		}
		if err := emit(citationsEvt(nil)); err != nil {
			return err
		}
	case "factual":
		if err := d.AnswerDirect(ctx, emit, q, d.Retriever.K, user, sessionID); err != nil {
			return err
		}
	case "research":
		if err := d.RunResearch(ctx, emit, q, 5, user, sessionID); err != nil {
			return err
		}
	case "hybrid":
		if err := emit(statusEvt("先回答你的政策问题…")); err != nil {
			return err
		}
		if err := d.AnswerDirect(ctx, emit, q, d.Retriever.K, user, sessionID); err != nil {
			return err
		}
		if err := emit(statusEvt("接下来为你办理业务…")); err != nil {
			return err
		}
		if err := d.StartFlow(ctx, emit, q, role, user, sessionID); err != nil {
			return err
		}
	case "transaction":
		if err := d.StartFlow(ctx, emit, q, role, user, sessionID); err != nil {
			return err
		}
	case "agent":
		// agent-first：ReAct 引擎自主组合工具（写操作外挂确认流）。
		if err := d.RunReAct(ctx, emit, q, role, user, sessionID, dec.Toolset); err != nil {
			return err
		}
	default:
		if err := emit(errorEvt(fmt.Sprintf("未知路由：%s", dec.Route))); err != nil {
			return err
		}
	}

	// 5) done
	if err := emit(doneEvt(elapsedMS(t0))); err != nil {
		return err
	}
	return nil
}

// decideRoute 按 ROUTER_MODE 产出路由决策包。
// cascade（默认）：L0/L1/L2 五分类级联；classic：旧单次分类；
// agent-first：三选一执行策略（refusal/direct/agent，见 triage.go）。
func (d *Deps) decideRoute(ctx context.Context, question, mode string) RouteDecision {
	if mode == "direct" || mode == "research" {
		dec := RouteDecision{Route: mode, Layer: "user-specified", Reason: "用户指定 " + mode, PreRAG: true}
		dec.fillPolicy()
		return dec
	}
	if mode == "react" {
		// 显式 ReAct 也要先拿决策包约束工具集。
		return d.routeDecision(ctx, question)
	}
	if d.Settings.RouterMode == "agent-first" {
		return d.TriageRoute(ctx, question)
	}
	if d.Settings.RouterMode == "classic" {
		r := d.RouteQuestion(ctx, question)
		return decisionFromClassic(r)
	}
	return d.CascadeRoute(ctx, question)
}

// routeDecision classic/cascade 之外需要决策包但 mode 已定的入口复用。
func (d *Deps) routeDecision(ctx context.Context, question string) RouteDecision {
	if d.Settings.RouterMode == "classic" {
		return decisionFromClassic(d.RouteQuestion(ctx, question))
	}
	return d.CascadeRoute(ctx, question)
}

// decisionFromClassic 旧 RouteResult → 决策包（classic 模式的适配层）。
func decisionFromClassic(r RouteResult) RouteDecision {
	dec := RouteDecision{Route: r.Route, Layer: "classic", Reason: r.Reason, ByLLM: r.ByLLM}
	dec.fillPolicy()
	return dec
}

// flowLabel 工具 → 流程中文名（续轮 route 事件的 reason 用）。
func flowLabel(tool string) string {
	if f, ok := flowDefs[tool]; ok {
		return f.Label
	}
	return tool
}
