package agent

import (
	"context"
	"encoding/json"
	"fmt"
	"regexp"
	"strconv"
	"strings"
	"time"

	"gewu/internal/business"
	"gewu/internal/dates"
	"gewu/internal/llm"
)

// 知行执行层：工具识别 → 槽位收集 → 确认 → 执行 → 冲突/失败恢复。
//
// 核心原则（PARITY §9）：
// - 读操作直接执行；写操作必须经过「确认摘要 → 用户确认 → 执行 → 回执」；
// - 日期换算一律走确定性解析（dates 包），LLM 只负责"找出"表述；
// - 执行失败不是终点：冲突给可选项、字段非法重新追问，恢复也是流程的一部分。

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
	// book_venue 必须是"办理动词 + 场馆类宾语共现"：裸 `预约` 会把
	// "预约心理咨询/预约挂号"一切"预约X"都误选成场馆工具（P7 修复）。
	{"book_venue", regexp.MustCompile(`(预约|预订|订).*(馆|场|间|羽毛球|篮球|游泳|乒乓|网球|健身|研讨|教室|场地)`)},
}

// nonVenueRe 负向双保险：咨询/就医类词与"预约"共现时，即使句式像
// "预约X"也不选场馆工具（跳过候选继续匹配，最终落知识库）。
var nonVenueRe = regexp.MustCompile(`心理咨询|心理辅导|心理咨询室|挂号|看医生|校医|咨询老师|辅导员`)

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

// DetectTool 启发式工具识别（确定性强，LLM 只兜底口语化表述）。
// book_venue 候选遇 nonVenueRe（咨询/就医类）时跳过——宁可落知识库也不误入办理流。
func DetectTool(question string) string {
	for _, tp := range toolPatterns {
		if !tp.re.MatchString(question) {
			continue
		}
		if tp.name == "book_venue" && nonVenueRe.MatchString(question) {
			continue
		}
		return tp.name
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
func (d *Deps) parseSlot(text string) string {
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
		if s, ok := hourSlot[h%12]; ok && h%12 != 0 {
			return s
		}
		if s, ok := hourSlot[h]; ok {
			return s
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
func (d *Deps) parseVenue(text string) string {
	v, ok, err := d.Business.VenueByName(text)
	if err != nil || !ok {
		return ""
	}
	return v.VenueID
}

// normVenueID venue_id → 场馆名（确认摘要展示用）。
func (d *Deps) normVenueID(venueID string) string {
	venues, err := d.Business.ListVenues()
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

// rawTextFn 非空原文（自由文本槽位）。
func rawTextFn(d *Deps, t string) (string, bool) { return rawText(t) }

// rawText 非空原文（自由文本槽位解析实现）。
func rawText(text string) (string, bool) {
	t := strings.TrimSpace(text)
	if t == "" {
		return "", false
	}
	return t, true
}

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
	Parse func(d *Deps, text string) (string, bool)
}

// slotOrder SLOT_META 的遍历顺序（Python dict 插入序，confirm 修改检测依赖此序）。
var slotOrder = []string{
	"venue", "date", "slot", "purpose", "leave_type", "start_date", "end_date", "reason", "booking_id", "ticket_id",
}

var slotMetaTable = map[string]slotMeta{
	"venue": {Label: "场馆", Ask: "想预约哪个场馆？可选：羽毛球馆、篮球场、研讨间301、研讨间302", Parse: func(d *Deps, t string) (string, bool) {
		v := d.parseVenue(t)
		return v, v != ""
	}},
	"date": {Label: "日期", Ask: "预约哪一天？（如：明天、周三、9月2日）", Parse: parseDateSlotFn},
	"slot": {Label: "时段", Ask: "预约哪个时段？可选：08:00-10:00 / 10:00-12:00 / 14:00-16:00 / 16:00-18:00 / 19:00-21:00（也可回复上午/下午/晚上）", Parse: func(d *Deps, t string) (string, bool) {
		s := d.parseSlot(t)
		return s, s != ""
	}},
	"purpose": {Label: "用途", Ask: "预约用途是什么？（如：班级活动、训练）", Parse: rawTextFn},
	"leave_type": {Label: "类型", Ask: "请假类型是？（事假 / 病假 / 其他）", Parse: func(d *Deps, t string) (string, bool) {
		s := parseLeaveType(t)
		return s, s != ""
	}},
	"start_date": {Label: "开始日期", Ask: "从哪一天开始请假？（如：明天、下周一）", Parse: parseDateSlotFn},
	"end_date":   {Label: "结束日期", Ask: "请到哪一天？（含当天，如：下周二）", Parse: parseDateSlotFn},
	"reason":     {Label: "事由", Ask: "请简要说明请假事由", Parse: rawTextFn},
	"booking_id": {Label: "预约单号", Ask: "要取消的预约单号是？（形如 VE-0001，可先查「我的预约」）", Parse: func(d *Deps, t string) (string, bool) {
		return firstMatch(bookingIDRe, t)
	}},
	"ticket_id": {Label: "请假单号", Ask: "请假单号是？（形如 LV-0001）", Parse: func(d *Deps, t string) (string, bool) {
		return firstMatch(ticketIDRe, t)
	}},
}

// parseDateSlotFn 日期表述 → ISO（确定性解析）。
func parseDateSlotFn(d *Deps, t string) (string, bool) {
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

// emitFn 事件发射器：返回错误时中止（客户端断开）。
type emitFn func(any) error

// StartFlow 路由判定为 transaction 后的入口（outcome 贯通给 fallbackKnowledge
// 的直答链路，供 done.reason 计算截断）。
func (d *Deps) StartFlow(ctx context.Context, emit emitFn, question, role, user, sessionID string, outcome *chatOutcome) error {
	// 启发式优先（确定性强），LLM 只兜底启发式没识别出口语化表述的情况
	tool := DetectTool(question)
	if tool == "" && d.LLM != nil && d.LLM.HasKey() {
		tool = d.llmExtractTool(ctx, question, role)
	}
	if _, known := d.Tools[tool]; !known {
		return d.fallbackKnowledge(ctx, emit, question, user, sessionID, outcome)
	}

	if _, inFlows := flowDefs[tool]; readTools[tool] || !inFlows {
		args := map[string]string{}
		if tool == "query_venues" {
			if iso, ok := parseDateSlotFn(d, question); ok {
				args["date"] = iso
			}
		}
		result := d.CallTool(tool, args, role, user)
		if err := emit(actionResultEvt(tool, result.OK, result.Message, receiptPtr(result))); err != nil {
			return err
		}
		if result.OK {
			return emit(answerEvt(result.Message))
		}
		return emit(answerEvt(fmt.Sprintf("办理未完成：%s。", orMessage(result))))
	}

	sess := d.Sessions.Ensure(sessionID, role, user)
	sess.Tool, sess.Phase, sess.LastAsked = tool, PhaseCollect, ""
	sess.Slots = map[string]string{}
	return d.advance(ctx, emit, sess, question)
}

func orMessage(r business.Result) string {
	if r.Message != "" {
		return r.Message
	}
	return "未知错误"
}

// receiptPtr 有凭证时返回指针（action_result.receipt 的 null/值语义）。
func receiptPtr(r business.Result) *string {
	if r.Receipt == "" {
		return nil
	}
	s := r.Receipt
	return &s
}

// llmExtractTool LLM 选工具（启发式未识别且有 key 时）。
func (d *Deps) llmExtractTool(ctx context.Context, question, role string) string {
	raw, err := d.LLM.Chat(ctx, []llm.Message{
		{Role: "system", Content: fmt.Sprintf(
			"根据用户消息选择最匹配的工具，只输出工具名 JSON：{\"tool\": \"...\"}。可选工具：\n%s\n\n"+
				"可选工具中没有语义匹配的工具时，必须返回 {\"tool\": \"\"}，"+
				"禁止挑选最相近的工具强行办理（例如咨询类诉求不是任何工具）。", d.ToolDescriptions(role))},
		{Role: "user", Content: question},
	}, llm.Options{JSONMode: true, Temperature: 0, MaxTokens: 100, Small: true})
	if err != nil {
		logf(ctx, "工具识别 LLM 调用失败：%v", err)
		return ""
	}
	obj, err := parseJSONObject(raw)
	if err != nil {
		return ""
	}
	name := jsonStr(obj, "tool")
	if name == "book_venue" && nonVenueRe.MatchString(question) {
		// 负向双保险对 LLM 路径同样生效：句中带咨询/就医类词时，
		// LLM 强行挑 book_venue 一律不采信，落知识库（P7-1）。
		return ""
	}
	if _, ok := d.Tools[name]; ok {
		return name
	}
	return ""
}

// fallbackKnowledge 工具未识别 → 转知识库检索。
func (d *Deps) fallbackKnowledge(ctx context.Context, emit emitFn, question, user, sessionID string, outcome *chatOutcome) error {
	if err := emit(answerEvt("这个问题我理解为你想咨询校园信息，为你转知识库检索：")); err != nil {
		return err
	}
	return d.AnswerDirect(ctx, emit, question, d.Retriever.K, user, sessionID, outcome)
}

// advance collect 阶段：吸收新信息 → 齐了进确认，缺则追问。
func (d *Deps) advance(ctx context.Context, emit emitFn, sess *TxSession, userText string) error {
	flow := flowDefs[sess.Tool]

	if d.LLM != nil && d.LLM.HasKey() {
		extracted := d.llmExtractSlots(ctx, sess.Tool, userText, sess.Slots)
		for _, slot := range slotOrder {
			value, ok := extracted[slot]
			if !ok {
				continue
			}
			if _, collected := sess.Slots[slot]; collected {
				continue
			}
			if norm, ok := d.normalizeSlot(slot, value); ok {
				sess.Slots[slot] = norm
			}
		}
	} else {
		// 离线：针对上一轮追问的字段解析；首轮则对全文做结构化字段的机会性抽取
		if sess.LastAsked != "" {
			if meta, ok := slotMetaTable[sess.LastAsked]; ok {
				if v, ok := meta.Parse(d, userText); ok {
					sess.Slots[sess.LastAsked] = v
				}
			}
		} else {
			d.opportunisticFill(sess, userText)
		}
	}

	d.applyDaysPhrase(sess, userText)

	missing := missingSlots(flow, sess.Slots)
	if len(missing) > 0 {
		next := missing[0]
		sess.LastAsked = next
		ask := slotMetaTable[next].Ask
		if err := emit(slotQuestionEvt(next, ask)); err != nil {
			return err
		}
		return emit(answerEvt(ask))
	}

	sess.Phase = PhaseConfirm
	sess.LastAsked = ""
	return d.emitConfirm(emit, sess)
}

// missingSlots 按流程必填顺序找缺失槽位。
func missingSlots(flow flowDef, slots map[string]string) []string {
	var missing []string
	for _, s := range flow.Required {
		if _, ok := slots[s]; !ok {
			missing = append(missing, s)
		}
	}
	return missing
}

// opportunisticFill 离线首轮：从原句里直接抽取结构化字段（场馆/日期/时段/类型）。
// 自由文本字段（purpose/reason/单号）不猜测，留给追问。
func (d *Deps) opportunisticFill(sess *TxSession, text string) {
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
				if iso, ok := parseDateSlotFn(d, text); ok {
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
				if v, ok := meta.Parse(d, text); ok {
					sess.Slots[slot] = v
				}
			}
		}
	}
}

// applyDaysPhrase 「请一天假 / 请三天假」：给了开始日期时直接换算结束日期。
func (d *Deps) applyDaysPhrase(sess *TxSession, text string) {
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
func (d *Deps) llmExtractSlots(ctx context.Context, tool, text string, collected map[string]string) map[string]string {
	flow := flowDefs[tool]
	fields := newOrderedArgs()
	for _, s := range append(append([]string{}, flow.Required...), flow.Optional...) {
		fields.set(s, slotMetaTable[s].Label)
	}
	collectedJSON, _ := json.Marshal(collected)
	fieldsJSON, _ := json.Marshal(fields)
	userMsg := fmt.Sprintf("今天是 %s。工具：%s（%s）\n字段定义：%s\n已收集：%s\n用户消息：%s",
		dates.TodayISO(), tool, flow.Label, string(fieldsJSON), string(collectedJSON), text)
	raw, err := d.LLM.Chat(ctx, []llm.Message{
		{Role: "system", Content: SlotExtractSystem},
		{Role: "user", Content: userMsg},
	}, llm.Options{JSONMode: true, Temperature: 0, MaxTokens: 300, Small: true})
	if err != nil {
		logf(ctx, "槽位抽取 LLM 调用失败，退化为启发式：%v", err)
		return map[string]string{}
	}
	obj, err := parseJSONObject(raw)
	if err != nil {
		return map[string]string{}
	}
	out := map[string]string{}
	for k, v := range jsonStrMap(obj, "slots") {
		if s, ok := v.(string); ok {
			out[k] = s
		}
	}
	return out
}

// normalizeSlot LLM 抽出的原始值过确定性解析器归一（日期换算、场馆名→ID 等）。
func (d *Deps) normalizeSlot(slot, value string) (string, bool) {
	switch slot {
	case "purpose", "reason", "booking_id", "ticket_id":
		return rawText(value)
	}
	meta, ok := slotMetaTable[slot]
	if !ok {
		return "", false
	}
	return meta.Parse(d, value)
}

// emitConfirm 确认摘要（PARITY §9.3.3）。
func (d *Deps) emitConfirm(emit emitFn, sess *TxSession) error {
	flow := flowDefs[sess.Tool]
	args := newOrderedArgs()
	for _, s := range append(append([]string{}, flow.Required...), flow.Optional...) {
		if v, ok := sess.Slots[s]; ok {
			args.set(slotMetaTable[s].Label, v)
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
		args.set("场馆", d.normVenueID(sess.Slots["venue"]))
	}
	var parts []string
	for _, k := range args.keys {
		parts = append(parts, fmt.Sprintf("%s：%s", k, args.vals[k]))
	}
	summary := strings.Join(parts, "；")
	if err := emit(pendingActionEvt(sess.Tool, flow.Label, args)); err != nil {
		return err
	}
	text := strings.TrimSpace(fmt.Sprintf("请确认%s信息——%s。%s回复「确认」提交，或直接告诉我需要修改的地方。",
		flow.Label, summary, note))
	return emit(answerEvt(text))
}

// confirmWordRe / cancelWordRe confirm 阶段的确认/取消词。
var (
	confirmModifyRe = regexp.MustCompile(`确认|确定|好的|可以|提交|是的|对`)
	cancelWordRe    = regexp.MustCompile(`取消|算了|不办|不要`)
)

// HandleReply collect/confirm 阶段收到用户回复后的处理（由 pipeline 在续轮调用）。
func (d *Deps) HandleReply(ctx context.Context, emit emitFn, sess *TxSession, userText string) error {
	if sess.Phase == PhaseCollect {
		return d.advance(ctx, emit, sess, userText)
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
		value, ok := meta.Parse(d, userText)
		if ok && value != sess.Slots[slot] {
			sess.Slots[slot] = value
			modified = true
		}
	}
	if modified {
		sess.Phase = PhaseConfirm
		if err := emit(statusEvt("已更新，请重新确认：")); err != nil {
			return err
		}
		return d.emitConfirm(emit, sess)
	}

	if confirmModifyRe.MatchString(userText) {
		return d.execute(emit, sess)
	}
	if cancelWordRe.MatchString(userText) {
		d.Sessions.Clear(sess.SessionID)
		return emit(answerEvt("好的，已取消本次办理。有别的事随时找我。"))
	}
	return emit(answerEvt("没太听懂——请回复「确认」提交，或「取消」放弃，也可以直接告诉我需要修改的日期、时段等信息。"))
}

func containsStr(list []string, s string) (int, bool) {
	for i, v := range list {
		if v == s {
			return i, true
		}
	}
	return -1, false
}

// execute 确认后的执行与失败恢复（PARITY §9.3.5）。
func (d *Deps) execute(emit emitFn, sess *TxSession) error {
	result := d.CallTool(sess.Tool, sess.Slots, sess.Role, sess.User)

	if result.OK {
		d.Sessions.Clear(sess.SessionID)
		if err := emit(actionResultEvt(sess.Tool, true, result.Message, receiptPtr(result))); err != nil {
			return err
		}
		receipt := ""
		if result.Receipt != "" {
			receipt = fmt.Sprintf("（凭证号：%s）", result.Receipt)
		}
		return emit(answerEvt(fmt.Sprintf("办理成功：%s%s", result.Message, receipt)))
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
			if err := emit(actionResultEvt(sess.Tool, false, result.Message, nil)); err != nil {
				return err
			}
			if err := emit(slotQuestionEvt(result.Field, question)); err != nil {
				return err
			}
			msg := result.Message
			if msg == "" {
				msg = "执行失败"
			}
			return emit(answerEvt(fmt.Sprintf("%s。%s", msg, question)))
		}
	}

	d.Sessions.Clear(sess.SessionID)
	if err := emit(actionResultEvt(sess.Tool, false, result.Message, nil)); err != nil {
		return err
	}
	return emit(answerEvt(fmt.Sprintf("办理未完成：%s。如需继续请重新发起。", orMessage(result))))
}

// ---------- 续轮意图判定 ----------

var (
	replyCancelRe  = regexp.MustCompile(`取消|算了|不办了|不要了`)
	replyConfirmRe = regexp.MustCompile(`确认|确定|好的|可以|提交`)
	replyTopicRe   = regexp.MustCompile(`什么|怎么|为什么|几点|哪|谁|吗`)
)

// ClassifyReply 判断用户回复是继续流程、取消流程、还是切换新话题（PARITY §9.5）。
func (d *Deps) ClassifyReply(ctx context.Context, userText string, sess *TxSession) string {
	if d.LLM != nil && d.LLM.HasKey() {
		phaseText := "补充信息"
		if sess.Phase == PhaseConfirm {
			phaseText = "确认"
		}
		slotsJSON, _ := json.Marshal(sess.Slots)
		raw, err := d.LLM.Chat(ctx, []llm.Message{
			{Role: "system", Content: fmt.Sprintf(
				"用户正在办理业务，系统处于「%s」阶段，已收集：%s。"+
					"判断用户这条消息是：continue（提供信息/确认/修改，继续流程）、"+
					"cancel（明确取消本次办理）、new_topic（转移话题问别的事）。"+
					"只输出 JSON：{\"intent\": \"continue|cancel|new_topic\"}", phaseText, string(slotsJSON))},
			{Role: "user", Content: userText},
		}, llm.Options{JSONMode: true, Temperature: 0, MaxTokens: 60, Small: true})
		if err == nil {
			if obj, perr := parseJSONObject(raw); perr == nil {
				switch jsonStr(obj, "intent") {
				case "continue", "cancel", "new_topic":
					return jsonStr(obj, "intent")
				}
			}
		} else {
			logf(ctx, "续轮意图 LLM 调用失败，退化为启发式：%v", err)
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
			if _, ok := meta.Parse(d, userText); ok {
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
			if _, ok := meta.Parse(d, userText); ok {
				return "continue"
			}
		}
	}
	if len([]rune(userText)) <= 12 && !isQuestionMark(userText) {
		return "continue" // 短句大概率是在回答追问
	}
	return "new_topic"
}
