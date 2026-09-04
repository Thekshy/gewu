package orchestrator

// 知行执行层（拷贝改造自冻结 internal/agent/transaction.go，PARITY §9 逐字）：
// 工具识别 → 槽位收集 → 确认 → 执行 → 冲突/失败恢复。
// 与单体的差异仅两处（行为等价）：
// 1) 会话经 conversation 服务（每轮取回→变更→回写，轮间持久）；
// 2) LLM 调用经 generate 服务。
// 业务系统暂以冻结 internal/business 同进程过渡（P4 迁 tool 服务）。

import (
	"context"
	"encoding/json"
	"fmt"
	"regexp"
	"strconv"
	"strings"
	"time"

	generatev1 "gewu/pkg/gen/gewu/generate/v1"
	orchestratorv1 "gewu/pkg/gen/gewu/orchestrator/v1"

	"gewu/internal/business"
	"gewu/internal/dates"

	"go.uber.org/zap"
)

// Phase 常量。
const (
	PhaseIdle    = "idle"
	PhaseCollect = "collect"
	PhaseConfirm = "confirm"
)

// emitFn 事件发射器：返回错误时中止（下游断开）。
type emitFn func(*orchestratorv1.ChatResponse) error

// toolPatterns 工具识别（离线启发式，按序首个命中；顺序是契约）。
var toolPatterns = []struct {
	name string
	re   *regexp.Regexp
}{
	{"cancel_booking", regexp.MustCompile(`取消预约|退订`)},
	{"approve_leave", regexp.MustCompile(`批准|通过.*(请假|申请)`)},
	{"pending_leaves", regexp.MustCompile(`待审批|审批.*(请假|申请)|谁.*请了假`)},
	{"leave_status", regexp.MustCompile(`请假.*(单号|进度|状态|批了没)|LV-\d+`)},
	{"my_bookings", regexp.MustCompile(`我的预约|我预约了|我订了`)},
	{"query_venues", regexp.MustCompile(`(有|哪些|什么|能).*(场馆|场地|研讨间)|场馆.*(有|能|可)`)},
	{"submit_leave", regexp.MustCompile(`请假|事假|病假|销假|休.*假|请.*天.*假`)},
	{"book_venue", regexp.MustCompile(`预约|预订|订.*(馆|场|间)`)},
}

// readTools 读工具集（直接执行，不进确认流）。
var readTools = map[string]bool{
	"query_venues": true, "my_bookings": true, "leave_status": true, "pending_leaves": true,
}

var cnNum = map[string]int{"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}

var daysPhraseRe = regexp.MustCompile(`([一二三四五六七八九]|[0-9]+)\s*天`)

// phraseDays 「请一天假 / 请三天假」的天数。
func phraseDays(text string) (int, bool) {
	m := daysPhraseRe.FindStringSubmatch(text)
	if m == nil {
		return 0, false
	}
	if n, ok := cnNum[m[1]]; ok {
		return n, true
	}
	n, err := strconv.Atoi(m[1])
	if err != nil {
		return 0, false
	}
	return n, true
}

// detectTool 启发式工具识别（确定性强，LLM 只兜底口语化表述）。
func detectTool(question string) string {
	for _, tp := range toolPatterns {
		if tp.re.MatchString(question) {
			return tp.name
		}
	}
	return ""
}

// ---------- 槽位解析 ----------

// periodMap 时段口语映射（多选词不唯一时不命中，须指明具体时段）。
var periodMap = map[string][]string{
	"上午": {"08:00-10:00", "10:00-12:00"},
	"中午": {"14:00-16:00"},
	"下午": {"14:00-16:00", "16:00-18:00"},
	"晚上": {"19:00-21:00"},
	"傍晚": {"16:00-18:00", "19:00-21:00"},
}

var hourSlot = map[int]string{
	8: "08:00-10:00", 10: "10:00-12:00", 14: "14:00-16:00", 16: "16:00-18:00", 19: "19:00-21:00",
}

var hourTextRe = regexp.MustCompile(`(\d{1,2})\s*[点:：时]\s*(\d{2})?`)

// parseSlot 时段解析（PARITY §9.3.1）。
func (s *Server) parseSlot(text string) string {
	for _, slot := range business.Slots {
		if strings.Contains(text, slot) {
			return slot
		}
		if len(slot) >= 5 && strings.Contains(text, slot[:5]) {
			return slot
		}
	}
	if m := hourTextRe.FindStringSubmatch(text); m != nil {
		h, _ := strconv.Atoi(m[1])
		if sl, ok := hourSlot[h%12]; ok && h%12 != 0 {
			return sl
		}
		if sl, ok := hourSlot[h]; ok {
			return sl
		}
	}
	for word, slots := range periodMap {
		if strings.Contains(text, word) && len(slots) == 1 {
			return slots[0]
		}
	}
	return ""
}

// parseVenue 场馆名 → venue_id（名称子串匹配）。
func (s *Server) parseVenue(text string) string {
	v, ok, err := s.business.VenueByName(text)
	if err != nil || !ok {
		return ""
	}
	return v.VenueID
}

// normVenueID venue_id → 场馆名（确认摘要展示用）。
func (s *Server) normVenueID(venueID string) string {
	venues, err := s.business.ListVenues()
	if err != nil {
		return venueID
	}
	for _, v := range venues {
		if v.VenueID == venueID {
			return v.Name
		}
	}
	return venueID
}

func parseLeaveType(text string) string {
	for _, t := range []string{"事假", "病假", "其他"} {
		if strings.Contains(text, t) {
			return t
		}
	}
	return ""
}

// rawText 非空原文（自由文本槽位解析实现）。
func rawText(text string) (string, bool) {
	t := strings.TrimSpace(text)
	if t == "" {
		return "", false
	}
	return t, true
}

// rawTextFn 非空原文（自由文本槽位）。
func rawTextFn(d *Server, t string) (string, bool) { return rawText(t) }

var bookingIDRe = regexp.MustCompile(`VE-\d+`)
var ticketIDRe = regexp.MustCompile(`LV-\d+`)

func firstMatch(re *regexp.Regexp, text string) (string, bool) {
	m := re.FindString(text)
	if m == "" {
		return "", false
	}
	return m, true
}

// slotMeta 槽位元数据：label/ask 文案逐字保留（PARITY §9.3 表格）。
type slotMeta struct {
	Label string
	Ask   string
	Parse func(d *Server, text string) (string, bool)
}

// slotOrder SLOT_META 的遍历顺序（Python dict 插入序，confirm 修改检测依赖此序）。
var slotOrder = []string{
	"venue", "date", "slot", "purpose", "leave_type", "start_date", "end_date", "reason", "booking_id", "ticket_id",
}

var slotMetaTable = map[string]slotMeta{
	"venue": {Label: "场馆", Ask: "想预约哪个场馆？可选：羽毛球馆、篮球场、研讨间301、研讨间302", Parse: func(d *Server, t string) (string, bool) {
		v := d.parseVenue(t)
		return v, v != ""
	}},
	"date": {Label: "日期", Ask: "预约哪一天？（如：明天、周三、9月2日）", Parse: parseDateSlotFn},
	"slot": {Label: "时段", Ask: "预约哪个时段？可选：08:00-10:00 / 10:00-12:00 / 14:00-16:00 / 16:00-18:00 / 19:00-21:00（也可回复上午/下午/晚上）", Parse: func(d *Server, t string) (string, bool) {
		s := d.parseSlot(t)
		return s, s != ""
	}},
	"purpose": {Label: "用途", Ask: "预约用途是什么？（如：班级活动、训练）", Parse: rawTextFn},
	"leave_type": {Label: "类型", Ask: "请假类型是？（事假 / 病假 / 其他）", Parse: func(d *Server, t string) (string, bool) {
		s := parseLeaveType(t)
		return s, s != ""
	}},
	"start_date": {Label: "开始日期", Ask: "从哪一天开始请假？（如：明天、下周一）", Parse: parseDateSlotFn},
	"end_date":   {Label: "结束日期", Ask: "请到哪一天？（含当天，如：下周二）", Parse: parseDateSlotFn},
	"reason":     {Label: "事由", Ask: "请简要说明请假事由", Parse: rawTextFn},
	"booking_id": {Label: "预约单号", Ask: "要取消的预约单号是？（形如 VE-0001，可先查「我的预约」）", Parse: func(d *Server, t string) (string, bool) {
		return firstMatch(bookingIDRe, t)
	}},
	"ticket_id": {Label: "请假单号", Ask: "请假单号是？（形如 LV-0001）", Parse: func(d *Server, t string) (string, bool) {
		return firstMatch(ticketIDRe, t)
	}},
}

// parseDateSlotFn 日期表述 → ISO（确定性解析）。
func parseDateSlotFn(d *Server, t string) (string, bool) {
	iso := dates.ParseISO(t, nil)
	return iso, iso != ""
}

// flowDef 办理流程定义。
type flowDef struct {
	Label    string
	Required []string
	Optional []string
}

// flowOrder FLOW_DEFS 的遍历顺序（Python dict 插入序）。
var flowOrder = []string{"book_venue", "submit_leave", "cancel_booking", "approve_leave", "leave_status"}

var flowDefs = map[string]flowDef{
	"book_venue":     {Label: "预约场馆", Required: []string{"venue", "date", "slot"}, Optional: []string{"purpose"}},
	"submit_leave":   {Label: "请假申请", Required: []string{"leave_type", "start_date", "end_date", "reason"}},
	"cancel_booking": {Label: "取消预约", Required: []string{"booking_id"}},
	"approve_leave":  {Label: "批准请假", Required: []string{"ticket_id"}},
	"leave_status":   {Label: "请假单查询", Required: []string{"ticket_id"}},
}

// flowLabel 工具 → 流程中文名（续轮 route 事件的 reason 用）。
func flowLabel(tool string) string {
	if f, ok := flowDefs[tool]; ok {
		return f.Label
	}
	return tool
}

// ---------- 会话持久化（跨进程差异点） ----------

// clearSession 清会话并标记本地副本（后续 persist 跳过，避免复活）。
func (s *Server) clearSession(ctx context.Context, sess *TxSession) {
	sess.Cleared = true
	_ = s.sessions.Clear(ctx, sess.SessionID)
}

// persistSession 轮末回写（cleared 副本跳过）。
func (s *Server) persistSession(ctx context.Context, sess *TxSession) {
	if sess == nil || sess.Cleared {
		return
	}
	if err := s.sessions.Save(ctx, sess); err != nil {
		s.log.Warn("会话回写失败", zap.Error(err))
	}
}

// ---------- 流程入口 ----------

// startFlow 路由判定为 transaction 后的入口。
func (s *Server) startFlow(ctx context.Context, emit emitFn, question, role, user, sessionID string, hasKey bool) error {
	// 启发式优先（确定性强），LLM 只兜底启发式没识别出口语化表述的情况
	tool := detectTool(question)
	if tool == "" && hasKey {
		tool = s.llmExtractTool(ctx, question, role)
	}
	if _, known := s.tools[tool]; !known {
		return s.fallbackKnowledge(ctx, emit, question, hasKey)
	}

	if _, inFlows := flowDefs[tool]; readTools[tool] || !inFlows {
		args := map[string]string{}
		if tool == "query_venues" {
			if iso, ok := parseDateSlotFn(s, question); ok {
				args["date"] = iso
			}
		}
		result := s.callTool(tool, args, role, user)
		if err := emit(actionResultEvent(tool, result.OK, result.Message, result.Receipt)); err != nil {
			return err
		}
		if result.OK {
			return emit(answerEvent(result.Message))
		}
		return emit(answerEvent(fmt.Sprintf("办理未完成：%s。", orMessage(result))))
	}

	sess, err := s.sessions.Ensure(ctx, sessionID, role, user)
	if err != nil {
		return err
	}
	sess.Tool, sess.Phase, sess.LastAsked = tool, PhaseCollect, ""
	sess.Slots = map[string]string{}
	sess.Cleared = false
	defer s.persistSession(ctx, sess)
	return s.advance(ctx, emit, sess, question, hasKey)
}

func orMessage(r business.Result) string {
	if r.Message != "" {
		return r.Message
	}
	return "未知错误"
}

// llmExtractTool LLM 选工具（启发式未识别且有 key 时）。
func (s *Server) llmExtractTool(ctx context.Context, question, role string) string {
	raw, err := s.generate.Chat(ctx, &generatev1.ChatRequest{
		Messages: []*generatev1.Message{
			{Role: "system", Content: fmt.Sprintf(
				"根据用户消息选择最匹配的工具，只输出工具名 JSON：{\"tool\": \"...\"}。可选工具：\n%s", s.toolDescriptions(role))},
			{Role: "user", Content: question},
		},
		Options: &generatev1.Options{JsonMode: true, Temperature: 0, MaxTokens: 100, Small: true},
	})
	if err != nil {
		s.log.Warn("工具识别 LLM 调用失败", zap.Error(err))
		return ""
	}
	obj, err := parseJSONObject(raw.GetContent())
	if err != nil {
		return ""
	}
	name := jsonStr(obj, "tool")
	if _, ok := s.tools[name]; ok {
		return name
	}
	return ""
}

// fallbackKnowledge 工具未识别 → 转知识库检索（P3 前为空库语义 → NO_DATA）。
func (s *Server) fallbackKnowledge(ctx context.Context, emit emitFn, question string, hasKey bool) error {
	if err := emit(answerEvent("这个问题我理解为你想咨询校园信息，为你转知识库检索：")); err != nil {
		return err
	}
	return s.answerDirectBare(ctx, emit, question, hasKey)
}

// advance collect 阶段：吸收新信息 → 齐了进确认，缺则追问。
func (s *Server) advance(ctx context.Context, emit emitFn, sess *TxSession, userText string, hasKey bool) error {
	flow := flowDefs[sess.Tool]

	if hasKey {
		extracted := s.llmExtractSlots(ctx, sess.Tool, userText, sess.Slots)
		for _, slot := range slotOrder {
			value, ok := extracted[slot]
			if !ok {
				continue
			}
			if _, collected := sess.Slots[slot]; collected {
				continue
			}
			if norm, ok := s.normalizeSlot(slot, value); ok {
				sess.Slots[slot] = norm
			}
		}
	} else {
		// 离线：针对上一轮追问的字段解析；首轮则对全文做结构化字段的机会性抽取
		if sess.LastAsked != "" {
			if meta, ok := slotMetaTable[sess.LastAsked]; ok {
				if v, ok := meta.Parse(s, userText); ok {
					sess.Slots[sess.LastAsked] = v
				}
			}
		} else {
			s.opportunisticFill(sess, userText)
		}
	}

	s.applyDaysPhrase(sess, userText)

	missing := missingSlots(flow, sess.Slots)
	if len(missing) > 0 {
		next := missing[0]
		sess.LastAsked = next
		ask := slotMetaTable[next].Ask
		if err := emit(slotQuestionEvent(next, ask)); err != nil {
			return err
		}
		return emit(answerEvent(ask))
	}

	sess.Phase = PhaseConfirm
	sess.LastAsked = ""
	return s.emitConfirm(emit, sess)
}

// missingSlots 按流程必填顺序找缺失槽位。
func missingSlots(flow flowDef, slots map[string]string) []string {
	var missing []string
	for _, sl := range flow.Required {
		if _, ok := slots[sl]; !ok {
			missing = append(missing, sl)
		}
	}
	return missing
}

// opportunisticFill 离线首轮：从原句里直接抽取结构化字段（场馆/日期/时段/类型）。
// 自由文本字段（purpose/reason/单号）不猜测，留给追问。
func (s *Server) opportunisticFill(sess *TxSession, text string) {
	flow := flowDefs[sess.Tool]
	slots := append(append([]string{}, flow.Required...), flow.Optional...)
	for _, slot := range slots {
		if _, ok := sess.Slots[slot]; ok {
			continue
		}
		switch slot {
		case "purpose", "reason", "booking_id", "ticket_id":
			continue
		case "date":
			if sess.Tool == "book_venue" {
				if iso, ok := parseDateSlotFn(s, text); ok {
					sess.Slots["date"] = iso
				}
			}
		case "start_date", "end_date":
			if sess.Tool == "submit_leave" {
				datesList := dates.ParseAll(text, nil)
				if slot == "start_date" && len(datesList) > 0 {
					sess.Slots["start_date"] = datesList[0].Format("2006-01-02")
				}
				if slot == "end_date" && len(datesList) >= 2 {
					sess.Slots["end_date"] = datesList[len(datesList)-1].Format("2006-01-02")
				}
			}
		default:
			if meta, ok := slotMetaTable[slot]; ok {
				if v, ok := meta.Parse(s, text); ok {
					sess.Slots[slot] = v
				}
			}
		}
	}
}

// applyDaysPhrase 「请一天假 / 请三天假」：给了开始日期时直接换算结束日期。
func (s *Server) applyDaysPhrase(sess *TxSession, text string) {
	if sess.Tool != "submit_leave" {
		return
	}
	start, ok := sess.Slots["start_date"]
	if !ok {
		return
	}
	if _, has := sess.Slots["end_date"]; has {
		return
	}
	n, ok := phraseDays(text)
	if !ok || n <= 0 {
		return
	}
	if sd, err := time.ParseInLocation("2006-01-02", start, dates.CNtz); err == nil {
		sess.Slots["end_date"] = sd.AddDate(0, 0, n-1).Format("2006-01-02")
	}
}

// llmExtractSlots LLM 槽位抽取（失败退化为空 map，由确定性路径兜底）。
func (s *Server) llmExtractSlots(ctx context.Context, tool, text string, collected map[string]string) map[string]string {
	flow := flowDefs[tool]
	fields := newOrderedArgs()
	for _, sl := range append(append([]string{}, flow.Required...), flow.Optional...) {
		fields.set(sl, slotMetaTable[sl].Label)
	}
	collectedJSON, _ := json.Marshal(collected)
	fieldsJSON, _ := json.Marshal(fields)
	userMsg := fmt.Sprintf("今天是 %s。工具：%s（%s）\n字段定义：%s\n已收集：%s\n用户消息：%s",
		dates.TodayISO(), tool, flow.Label, string(fieldsJSON), string(collectedJSON), text)
	raw, err := s.generate.Chat(ctx, &generatev1.ChatRequest{
		Messages: []*generatev1.Message{
			{Role: "system", Content: s.cfgStore.slotExtractPrompt()},
			{Role: "user", Content: userMsg},
		},
		Options: &generatev1.Options{JsonMode: true, Temperature: 0, MaxTokens: 300, Small: true},
	})
	if err != nil {
		s.log.Warn("槽位抽取 LLM 调用失败，退化为启发式", zap.Error(err))
		return map[string]string{}
	}
	obj, err := parseJSONObject(raw.GetContent())
	if err != nil {
		return map[string]string{}
	}
	out := map[string]string{}
	for k, v := range jsonStrMap(obj, "slots") {
		if str, ok := v.(string); ok {
			out[k] = str
		}
	}
	return out
}

// normalizeSlot LLM 抽出的原始值过确定性解析器归一（日期换算、场馆名→ID 等）。
func (s *Server) normalizeSlot(slot, value string) (string, bool) {
	switch slot {
	case "purpose", "reason", "booking_id", "ticket_id":
		return rawText(value)
	}
	meta, ok := slotMetaTable[slot]
	if !ok {
		return "", false
	}
	return meta.Parse(s, value)
}

// emitConfirm 确认摘要（PARITY §9.3.3）。
func (s *Server) emitConfirm(emit emitFn, sess *TxSession) error {
	flow := flowDefs[sess.Tool]
	args := newOrderedArgs()
	for _, sl := range append(append([]string{}, flow.Required...), flow.Optional...) {
		if v, ok := sess.Slots[sl]; ok {
			args.set(slotMetaTable[sl].Label, v)
		}
	}
	note := ""
	if sess.Tool == "submit_leave" {
		days := business.LeaveDays(sess.Slots["start_date"], sess.Slots["end_date"])
		if days >= 1 {
			level := business.ApproverOf(days)
			args.set("共", fmt.Sprintf("%d 天", days))
			args.set("审批", level+"（按学校规定）")
			if sess.Slots["leave_type"] == "病假" && days > 3 {
				note = "病假超过 3 天建议附医院证明。"
			}
		}
	}
	if sess.Tool == "book_venue" {
		args.set("场馆", s.normVenueID(sess.Slots["venue"]))
	}
	var parts []string
	for _, k := range args.keys {
		parts = append(parts, fmt.Sprintf("%s：%s", k, args.vals[k]))
	}
	summary := strings.Join(parts, "；")
	if err := emit(pendingActionEvent(sess.Tool, flow.Label, args.protoArgs())); err != nil {
		return err
	}
	text := strings.TrimSpace(fmt.Sprintf("请确认%s信息——%s。%s回复「确认」提交，或直接告诉我需要修改的地方。",
		flow.Label, summary, note))
	return emit(answerEvent(text))
}

// confirmModifyRe / cancelWordRe confirm 阶段的确认/取消词。
var (
	confirmModifyRe = regexp.MustCompile(`确认|确定|好的|可以|提交|是的|对`)
	cancelWordRe    = regexp.MustCompile(`取消|算了|不办|不要`)
)

// handleReply collect/confirm 阶段收到用户回复后的处理（由管线在续轮调用）。
func (s *Server) handleReply(ctx context.Context, emit emitFn, sess *TxSession, userText string, hasKey bool) error {
	if sess.Phase == PhaseCollect {
		return s.advance(ctx, emit, sess, userText, hasKey)
	}

	// confirm 阶段：先尝试理解为「修改」，再判确认/取消
	flow := flowDefs[sess.Tool]
	modified := false
	for _, slot := range slotOrder {
		if slot == "purpose" || slot == "reason" {
			continue
		}
		_, inRequired := containsStr(flow.Required, slot)
		_, collected := sess.Slots[slot]
		if !inRequired && !collected {
			continue
		}
		meta := slotMetaTable[slot]
		value, ok := meta.Parse(s, userText)
		if ok && value != sess.Slots[slot] {
			sess.Slots[slot] = value
			modified = true
		}
	}
	if modified {
		sess.Phase = PhaseConfirm
		if err := emit(statusEvent("已更新，请重新确认：")); err != nil {
			return err
		}
		return s.emitConfirm(emit, sess)
	}

	if confirmModifyRe.MatchString(userText) {
		return s.execute(ctx, emit, sess)
	}
	if cancelWordRe.MatchString(userText) {
		s.clearSession(ctx, sess)
		return emit(answerEvent("好的，已取消本次办理。有别的事随时找我。"))
	}
	return emit(answerEvent("没太听懂——请回复「确认」提交，或「取消」放弃，也可以直接告诉我需要修改的日期、时段等信息。"))
}

func containsStr(list []string, item string) (int, bool) {
	for i, v := range list {
		if v == item {
			return i, true
		}
	}
	return -1, false
}

// execute 确认后的执行与失败恢复（PARITY §9.3.5）。
func (s *Server) execute(ctx context.Context, emit emitFn, sess *TxSession) error {
	result := s.callTool(sess.Tool, sess.Slots, sess.Role, sess.User)

	if result.OK {
		s.clearSession(ctx, sess)
		if err := emit(actionResultEvent(sess.Tool, true, result.Message, result.Receipt)); err != nil {
			return err
		}
		receipt := ""
		if result.Receipt != "" {
			receipt = fmt.Sprintf("（凭证号：%s）", result.Receipt)
		}
		return emit(answerEvent(fmt.Sprintf("办理成功：%s%s", result.Message, receipt)))
	}

	// 失败恢复：字段级问题重新追问该字段，其余失败结束流程并说明
	if result.Field != "" {
		if meta, ok := slotMetaTable[result.Field]; ok {
			sess.Phase = PhaseCollect
			sess.LastAsked = result.Field
			delete(sess.Slots, result.Field)
			question := meta.Ask
			if len(result.Alternatives) > 0 {
				question = strings.TrimSpace(question + "可选时段：" + strings.Join(result.Alternatives, "、"))
			}
			if err := emit(actionResultEvent(sess.Tool, false, result.Message, "")); err != nil {
				return err
			}
			if err := emit(slotQuestionEvent(result.Field, question)); err != nil {
				return err
			}
			msg := result.Message
			if msg == "" {
				msg = "执行失败"
			}
			return emit(answerEvent(fmt.Sprintf("%s。%s", msg, question)))
		}
	}

	s.clearSession(ctx, sess)
	if err := emit(actionResultEvent(sess.Tool, false, result.Message, "")); err != nil {
		return err
	}
	return emit(answerEvent(fmt.Sprintf("办理未完成：%s。如需继续请重新发起。", orMessage(result))))
}

// ---------- 续轮意图判定 ----------

var (
	replyCancelRe  = regexp.MustCompile(`取消|算了|不办了|不要了`)
	replyConfirmRe = regexp.MustCompile(`确认|确定|好的|可以|提交`)
	replyTopicRe   = regexp.MustCompile(`什么|怎么|为什么|几点|哪|谁|吗`)
)

// classifyReply 判断用户回复是继续流程、取消流程、还是切换新话题（PARITY §9.5）。
func (s *Server) classifyReply(ctx context.Context, userText string, sess *TxSession, hasKey bool) string {
	if hasKey {
		phaseText := "补充信息"
		if sess.Phase == PhaseConfirm {
			phaseText = "确认"
		}
		slotsJSON, _ := json.Marshal(sess.Slots)
		raw, err := s.generate.Chat(ctx, &generatev1.ChatRequest{
			Messages: []*generatev1.Message{
				{Role: "system", Content: fmt.Sprintf(
					"用户正在办理业务，系统处于「%s」阶段，已收集：%s。"+
						"判断用户这条消息是：continue（提供信息/确认/修改，继续流程）、"+
						"cancel（明确取消本次办理）、new_topic（转移话题问别的事）。"+
						"只输出 JSON：{\"intent\": \"continue|cancel|new_topic\"}", phaseText, string(slotsJSON))},
				{Role: "user", Content: userText},
			},
			Options: &generatev1.Options{JsonMode: true, Temperature: 0, MaxTokens: 60, Small: true},
		})
		if err == nil {
			if obj, perr := parseJSONObject(raw.GetContent()); perr == nil {
				switch jsonStr(obj, "intent") {
				case "continue", "cancel", "new_topic":
					return jsonStr(obj, "intent")
				}
			}
		} else {
			s.log.Warn("续轮意图 LLM 调用失败，退化为启发式", zap.Error(err))
		}
	}

	if replyCancelRe.MatchString(userText) {
		return "cancel"
	}
	if replyConfirmRe.MatchString(userText) {
		return "continue"
	}
	if replyTopicRe.MatchString(userText) {
		return "new_topic"
	}
	if sess.LastAsked != "" {
		if meta, ok := slotMetaTable[sess.LastAsked]; ok {
			if _, ok := meta.Parse(s, userText); ok {
				return "continue"
			}
		}
	}
	for _, slot := range slotOrder {
		if slot == "purpose" || slot == "reason" {
			continue
		}
		if _, collected := sess.Slots[slot]; !collected {
			continue
		}
		if meta, ok := slotMetaTable[slot]; ok {
			if _, ok := meta.Parse(s, userText); ok {
				return "continue"
			}
		}
	}
	if len([]rune(userText)) <= 12 && !isQuestionMark(userText) {
		return "continue" // 短句大概率是在回答追问
	}
	return "new_topic"
}

// isQuestionMark 含中英文问号。
func isQuestionMark(text string) bool {
	return strings.Contains(text, "？") || strings.Contains(text, "?")
}
