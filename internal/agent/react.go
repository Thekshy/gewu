package agent

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"log"
	"sort"
	"strings"
	"sync"

	"gewu/internal/dates"
	"gewu/internal/llm"
	"gewu/internal/rag"
)

// P6 阶段5 / agent-first 改造：通用单主体 ReAct 引擎（原生 tool-calling 版）。
//
// 与 workflow（pipeline.go route→固定处理器）并存、不替代：workflow 是确定性
// 基线（评测可复现），ReAct 服务"agent 自主组合工具"的场景——模型每轮通过
// OpenAI 兼容的原生 tool_calls 协议决定调用哪个工具，无调用即终止（唯一判据）。
// 入口两种：mode=react 显式指定；ROUTER_MODE=agent-first 时 triage 分流过来。
//
// 防护四件套：唯一终止判据是模型不再产出 tool_calls；相同 (name,args) 指纹
// 第 3 次被拒并提示换路；maxTurns 上限 + 预算熔断（client 每次调用内部 Ensure）；
// 工具错误回填为 observation 不断链。写操作工具不直接执行——必填参数齐时
// 转入既有 TxSession 确认流（pending_action → 用户确认 → 执行 → 回执），
// 下一轮由 ClassifyReply/HandleReply 确定性接管。

// AgentTool ReAct 可用工具：Parameters 返回 JSON Schema（注入原生 tools 参数）。
type AgentTool interface {
	Name() string
	Description() string
	Parameters() map[string]any
	ReadOnly() bool // 写操作走确认流，不直接执行
	Run(ctx context.Context, args map[string]any) (string, error)
}

// reactMaxTurns 轮次上限（预算熔断之外的第二道闸）。
const reactMaxTurns = 8

// reactRepeatLimit 同一 (name,args) 指纹允许的执行次数；第 3 次拒绝并提示换路。
const reactRepeatLimit = 2

// observationLimit 回填给模型单条 observation 的长度上限（控制上下文膨胀）。
const observationLimit = 1500

// reactState 一次 ReAct 运行的共享状态（检索工具往里累积引用）。
type reactState struct {
	mu        sync.Mutex
	citations []Citation
}

// reactSystemPrompt 构造 ReAct system 提示词（工具清单走原生 tools 参数，
// 不再拼进文本；记忆块作为上下文注入）。
func reactSystemPrompt(memBlock string) string {
	var sb strings.Builder
	sb.WriteString(`你是「格物」的自主任务执行器，通过调用工具完成用户在校园场景的目标。规则：
1. 事实与政策一律用检索工具获取，不凭记忆编造；引用政策时标注来源文档标题；
2. 写操作工具（预约/取消/请假/审批）在必填参数齐全时发起一次调用即可，系统会转入用户确认流程；
3. 信息不足时不要猜：直接向用户提问（不调用工具，输出提问文本）；
4. 查实时业务数据（可约场馆/我的预约/待审批）用业务查询工具，不要检索政策文件；
5. 信息足够后直接给出最终中文回答（不再调用工具）；不要重复发起参数完全相同的调用。`)
	if memBlock != "" {
		sb.WriteString("\n\n已知用户信息：\n" + memBlock)
	}
	return sb.String()
}

// RunReAct 执行原生 tool-calling 的 agent 循环。toolset 为空表示不限制
// （角色权限在工具表内生效）。事件流：status(工具调用提示)* → answer_delta
// (final) → citations；写操作转确认流时：status → pending_action → answer_delta。
func (d *Deps) RunReAct(ctx context.Context, emit emitFn, question, role, user, sessionID string, toolset []string) error {
	if d.LLM == nil || !d.LLM.HasKey() {
		return errors.New("ReAct 需要 LLM_API_KEY")
	}
	state := &reactState{citations: []Citation{}}
	tools := d.reactTools(role, user, toolset, state)
	if len(tools) == 0 {
		return errors.New("ReAct 无可用工具")
	}
	toolDefs := agentToolDefs(tools)
	msgs := []llm.Message{
		{Role: "system", Content: reactSystemPrompt(d.memoryBlock(user, sessionID))},
		{Role: "user", Content: question},
	}
	seen := map[string]int{}  // (name,args) 指纹 → 已执行次数
	var observations []string // 已获得的工具结果（到顶兜底组织部分结论用）
	for turn := 0; turn < reactMaxTurns; turn++ {
		comp, err := d.LLM.ChatWithTools(ctx, msgs, llm.Options{Temperature: 0, MaxTokens: 1200}, toolDefs)
		if err != nil {
			return err
		}
		if len(comp.ToolCalls) == 0 { // 唯一终止判据：无 tool_calls 即最终回答
			final := strings.TrimSpace(comp.Content)
			if final == "" && len(observations) > 0 {
				final = reactPartialAnswer(observations)
			}
			if final == "" {
				final = "未能获取足够信息回答该问题，请换个说法或补充细节。"
			}
			if err := emit(answerEvt(final)); err != nil {
				return err
			}
			return emit(citationsEvt(state.citations))
		}
		// assistant 消息原样回填（content + tool_calls），保持协议配对完整。
		msgs = append(msgs, llm.Message{Role: "assistant", Content: comp.Content,
			ToolCalls: callsToMsg(comp.ToolCalls)})
		for _, call := range comp.ToolCalls {
			out, stop, err := d.runAgentCall(ctx, emit, tools, seen, &observations, call, role, user, sessionID)
			if err != nil {
				return err
			}
			if stop { // 写操作已转确认流，本轮到此为止
				return nil
			}
			msgs = append(msgs, llm.Message{Role: "tool", ToolCallID: call.ID, Content: out})
		}
	}
	// 到顶兜底：强制收敛一次；失败则用已累积观察组织部分结论。
	msgs = append(msgs, llm.Message{Role: "user",
		Content: "已达到最大工具调用轮次。请基于已有结果直接给出最终回答，不要再调用工具。"})
	if comp, err := d.LLM.ChatWithTools(ctx, msgs, llm.Options{Temperature: 0, MaxTokens: 1200}, toolDefs); err == nil {
		if final := strings.TrimSpace(comp.Content); final != "" {
			if err := emit(answerEvt(final)); err != nil {
				return err
			}
			return emit(citationsEvt(state.citations))
		}
	}
	if len(observations) > 0 {
		if err := emit(answerEvt(reactPartialAnswer(observations))); err != nil {
			return err
		}
		return emit(citationsEvt(state.citations))
	}
	return errors.New("ReAct 已达最大轮次仍未收敛")
}

// runAgentCall 执行单次工具调用并返回 observation。
// stop=true 表示写操作已转入用户确认流（调用方应立即结束本轮）。
func (d *Deps) runAgentCall(ctx context.Context, emit emitFn, tools map[string]AgentTool,
	seen map[string]int, observations *[]string, call llm.ToolCall,
	role, user, sessionID string) (string, bool, error) {
	tool, ok := tools[call.Name]
	if !ok {
		return "工具不存在：" + call.Name + "。可用工具：" + reactToolNames(tools), false, nil
	}
	args := parseToolArgs(call.Arguments)
	fp := reactFingerprint(call.Name, args)
	if seen[fp] >= reactRepeatLimit { // 第 3 次重复：拒绝执行，强制换路
		return "该调用已重复执行 " + fmt.Sprint(reactRepeatLimit) +
			" 次，结果不会变化。请换思路，或直接给出最终回答。", false, nil
	}
	// 写操作：必填参数齐 → 既有确认流（pending_action → 用户确认 → 执行）；
	// 参数不全 → observation 提示 agent 先向用户收集。
	if !tool.ReadOnly() {
		sess, missing, ready := d.agentConfirmSession(sessionID, role, user, call.Name, args)
		if ready {
			seen[fp]++
			log.Printf("[agent] ReAct 写操作转确认流 session=%s user=%s tool=%s", sessionID, user, call.Name)
			if err := emit(statusEvt("已整理办理信息，等待确认…")); err != nil {
				return "", false, err
			}
			if err := d.emitConfirm(emit, sess); err != nil {
				return "", false, err
			}
			return "", true, nil
		}
		return "缺少必填参数：" + strings.Join(missing, "、") +
			"。请先向用户收集这些信息（直接提问，不调用工具），信息齐全后再发起调用。", false, nil
	}
	seen[fp]++
	if err := emit(statusEvt(fmt.Sprintf("调用工具 %s…", call.Name))); err != nil {
		return "", false, err
	}
	out, runErr := tool.Run(ctx, args)
	if runErr != nil {
		out = "工具执行失败：" + runErr.Error() // 错误回填为 observation，不断链
	}
	*observations = append(*observations, call.Name+"："+truncate(out, observationLimit))
	log.Printf("[agent] ReAct session=%s user=%s tool=%s", sessionID, user, call.Name)
	return truncate(out, observationLimit), false, nil
}

// agentConfirmSession 把 agent 的写调用参数转成确认阶段的 TxSession
// （槽位经与 workflow 相同的 normalizeSlot 归一，行为一致可复用）。
// 必填缺失时不建会话，返回缺失清单交回 agent 向用户收集。
func (d *Deps) agentConfirmSession(sessionID, role, user, toolName string, args map[string]any) (*TxSession, []string, bool) {
	flow, inFlows := flowDefs[toolName]
	if !inFlows {
		return nil, nil, false // 不在确认流定义中的写工具：由调用方直接执行
	}
	slots := map[string]string{}
	for k, v := range args {
		raw := fmt.Sprintf("%v", v)
		if norm, ok := d.normalizeSlot(k, raw); ok {
			slots[k] = norm
		} else {
			slots[k] = raw // 归一失败保留原值，业务层校验兜底
		}
	}
	if missing := missingSlots(flow, slots); len(missing) > 0 {
		return nil, missing, false
	}
	sess := d.Sessions.Ensure(sessionID, role, user)
	sess.Tool, sess.Phase, sess.LastAsked = toolName, PhaseConfirm, ""
	sess.Slots = slots
	return sess, nil, true
}

// reactPartialAnswer 到顶且收敛失败时的部分结论（不留裸错误）。
func reactPartialAnswer(observations []string) string {
	var sb strings.Builder
	sb.WriteString("已达到最大工具调用轮次，暂未完全办成。已查到的信息：\n")
	for _, obs := range firstN(observations, 5) {
		sb.WriteString("- " + truncate(obs, 200) + "\n")
	}
	return sb.String()
}

// parseToolArgs 解析模型给的参数 JSON 字符串（容错：空/非法返回空 map）。
func parseToolArgs(raw string) map[string]any {
	args := map[string]any{}
	if strings.TrimSpace(raw) == "" {
		return args
	}
	_ = json.Unmarshal([]byte(raw), &args)
	return args
}

// reactFingerprint (name,args) 规范化指纹：json.Marshal 对 map 的 key 排序输出，
// 同一调用必有同一指纹（防死循环去重的确定性基础）。
func reactFingerprint(name string, args map[string]any) string {
	if args == nil {
		args = map[string]any{}
	}
	buf, err := json.Marshal(args)
	if err != nil {
		return name + "|?"
	}
	return name + "|" + string(buf)
}

func callsToMsg(calls []llm.ToolCall) []llm.ToolCallMsg {
	out := make([]llm.ToolCallMsg, 0, len(calls))
	for _, c := range calls {
		var m llm.ToolCallMsg
		m.ID, m.Type = c.ID, "function"
		m.Function.Name, m.Function.Arguments = c.Name, c.Arguments
		out = append(out, m)
	}
	return out
}

func reactToolNames(tools map[string]AgentTool) string {
	names := make([]string, 0, len(tools))
	for name := range tools {
		names = append(names, name)
	}
	sort.Strings(names)
	return strings.Join(names, "、")
}

// agentToolDefs AgentTool 表 → 原生 tools 参数。
func agentToolDefs(tools map[string]AgentTool) []llm.ToolDef {
	names := make([]string, 0, len(tools))
	for name := range tools {
		names = append(names, name)
	}
	sort.Strings(names)
	defs := make([]llm.ToolDef, 0, len(tools))
	for _, name := range names {
		t := tools[name]
		defs = append(defs, llm.ToolDef{Type: "function", Function: llm.ToolFunction{
			Name:        t.Name(),
			Description: t.Description(),
			Parameters:  t.Parameters(),
		}})
	}
	return defs
}

// reactTools 构造本角色可用的 ReAct 工具表；toolset 非空时进一步收窄（路由决策包）。
func (d *Deps) reactTools(role, user string, toolset []string, state *reactState) map[string]AgentTool {
	allowed := map[string]bool{}
	for _, name := range toolset {
		allowed[name] = true
	}
	tools := map[string]AgentTool{}
	tools["search_knowledge"] = &searchTool{d: d, state: state}
	tools["parse_date"] = dateTool{}
	for name, t := range d.Tools {
		if !hasRole(t.Roles, role) {
			continue // 角色权限矩阵照常生效
		}
		if len(toolset) > 0 && !allowed[name] {
			continue // 路由决策包的最小工具集约束
		}
		tools[name] = &bizTool{d: d, spec: t, role: role, user: user}
	}
	return tools
}

// ---------- 工具实现 ----------

// searchTool 混合检索工具：把检索命中带进 observation，并累积引用。
type searchTool struct {
	d     *Deps
	state *reactState
}

func (s *searchTool) Name() string { return "search_knowledge" }

func (s *searchTool) Description() string {
	return "混合检索校园政策知识库（转专业/保研/奖学金/图书馆/宿舍/校历等），返回带来源的条款原文"
}

func (s *searchTool) Parameters() map[string]any {
	return map[string]any{
		"type": "object",
		"properties": map[string]any{
			"query": map[string]any{"type": "string", "description": "自包含的检索问题"},
			"k":     map[string]any{"type": "integer", "description": "返回条数 1~10，默认 5"},
		},
		"required": []string{"query"},
	}
}

func (s *searchTool) ReadOnly() bool { return true }

func (s *searchTool) Run(ctx context.Context, args map[string]any) (string, error) {
	query, _ := args["query"].(string)
	if strings.TrimSpace(query) == "" {
		return "", errors.New("缺少参数 query")
	}
	k := 5
	if kf, ok := args["k"].(float64); ok && kf >= 1 && kf <= 10 {
		k = int(kf)
	}
	hits, err := s.d.Retriever.Search(ctx, query, k)
	if err != nil {
		return "", err
	}
	if len(hits) == 0 {
		return "未检索到相关资料。", nil
	}
	cites := citationsFrom(hits)
	s.state.mu.Lock()
	have := map[string]bool{}
	for _, c := range s.state.citations {
		have[c.DocID+c.Title] = true
	}
	for _, c := range cites {
		if !have[c.DocID+c.Title] {
			s.state.citations = append(s.state.citations, c)
		}
	}
	s.state.mu.Unlock()
	var sb strings.Builder
	for i, h := range hits {
		fmt.Fprintf(&sb, "[%d]《%s》（来源：%s）\n%s\n\n", i+1, h.Title, h.Source, truncate(h.Text, 600))
	}
	return strings.TrimSpace(sb.String()), nil
}

// citationsFrom 检索命中 → 引用列表（与 AnswerDirect 的编号口径一致）。
func citationsFrom(hits []rag.Hit) []Citation {
	out := make([]Citation, 0, len(hits))
	for i, h := range hits {
		out = append(out, Citation{N: i + 1, DocID: h.DocID, Title: h.Title, Source: h.Source})
	}
	return out
}

// bizTool 业务工具适配：LLM 的自由 JSON 参数收敛为 map[string]string 后
// 走 CallTool 单一出口（未知工具/越权/缺参在进入业务系统前拦截）。
type bizTool struct {
	d    *Deps
	spec Tool
	role string
	user string
}

func (b *bizTool) Name() string        { return b.spec.Name }
func (b *bizTool) ReadOnly() bool      { return b.spec.ReadOnly }
func (b *bizTool) Description() string { return b.spec.Description }

func (b *bizTool) Run(_ context.Context, args map[string]any) (string, error) {
	strArgs := make(map[string]string, len(args))
	for k, v := range args {
		switch x := v.(type) {
		case string:
			strArgs[k] = x
		default:
			strArgs[k] = fmt.Sprintf("%v", x)
		}
	}
	result := b.d.CallTool(b.spec.Name, strArgs, b.role, b.user)
	if result.Err != "" {
		return result.Message, nil // 业务失败也是有效 observation（缺参/越权），交模型决策
	}
	return result.Message, nil
}

// bizToolParams 各业务工具的参数 JSON Schema（字段名与 workflow 槽位一致，
// 便于确认流共用 normalizeSlot 归一）。
func bizToolParams(name string) map[string]any {
	str := func(desc string) map[string]any { return map[string]any{"type": "string", "description": desc} }
	obj := func(props map[string]any, required ...string) map[string]any {
		return map[string]any{"type": "object", "properties": props, "required": required}
	}
	switch name {
	case "query_venues":
		return obj(map[string]any{"date": str("查询日期 YYYY-MM-DD，缺省今天")})
	case "my_bookings", "pending_leaves":
		return obj(map[string]any{})
	case "leave_status":
		return obj(map[string]any{"ticket_id": str("请假单号，如 LV-20260901-0001")}, "ticket_id")
	case "book_venue":
		return obj(map[string]any{
			"venue":   str("场馆名称，如 羽毛球馆/篮球场/研讨间301"),
			"date":    str("预约日期 YYYY-MM-DD"),
			"slot":    str("时段，如 08:00-10:00/19:00-21:00"),
			"purpose": str("用途（可选）"),
		}, "venue", "date", "slot")
	case "cancel_booking":
		return obj(map[string]any{"booking_id": str("预约单号，如 VE-xxx")}, "booking_id")
	case "submit_leave":
		return obj(map[string]any{
			"leave_type": str("假别：事假/病假/销假"),
			"start_date": str("开始日期 YYYY-MM-DD"),
			"end_date":   str("结束日期 YYYY-MM-DD"),
			"reason":     str("请假事由"),
		}, "leave_type", "start_date", "end_date", "reason")
	case "approve_leave":
		return obj(map[string]any{"ticket_id": str("请假单号")}, "ticket_id")
	default:
		return obj(map[string]any{})
	}
}

func (b *bizTool) Parameters() map[string]any { return bizToolParams(b.spec.Name) }

// dateTool 确定性日期解析工具（复用 internal/dates，零 LLM 成本）。
type dateTool struct{}

func (dateTool) Name() string { return "parse_date" }

func (dateTool) Description() string {
	return `把"明天/下周三/9月2日"等中文日期表述解析为 YYYY-MM-DD`
}

func (dateTool) Parameters() map[string]any {
	return map[string]any{
		"type": "object",
		"properties": map[string]any{
			"text": map[string]any{"type": "string", "description": "日期表述原文"},
		},
		"required": []string{"text"},
	}
}

func (dateTool) ReadOnly() bool { return true }

func (dateTool) Run(_ context.Context, args map[string]any) (string, error) {
	text, _ := args["text"].(string)
	if strings.TrimSpace(text) == "" {
		return "", errors.New("缺少参数 text")
	}
	if t, ok := dates.Parse(text, nil); ok {
		return text + " → " + t.Format("2006-01-02"), nil
	}
	return "无法识别日期表述：" + text, nil
}
