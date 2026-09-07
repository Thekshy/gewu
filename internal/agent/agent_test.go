package agent

import (
	"context"
	"path/filepath"
	"strings"
	"testing"

	"gewu/internal/business"
	"gewu/internal/config"
	"gewu/internal/dates"
	"gewu/internal/rag"
)

// testDeps 构造零 key（离线确定性链路）的编排依赖。
func testDeps(t *testing.T) *Deps {
	t.Helper()
	store, err := rag.Open(filepath.Join(t.TempDir(), "index.db"))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = store.Close() })
	biz, err := business.Open(filepath.Join(t.TempDir(), "biz.db"))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = biz.Close() })
	s := config.Default()
	d := NewDeps(s, nil, rag.NewRetriever(store, s.RetrievalK, nil), biz, nil)
	resetAll(t, d)
	return d
}

func resetAll(t *testing.T, d *Deps) {
	t.Helper()
	if err := d.Business.Reset(); err != nil {
		t.Fatal(err)
	}
	d.Sessions = NewSessionStore()
	d.Tools = toolsFor(d)
}

// ask 跑一轮对话，收集全部事件。
func ask(t *testing.T, d *Deps, sid, text string, role string) []any {
	t.Helper()
	var events []any
	d.RunChat(context.Background(), func(ev any) error {
		events = append(events, ev)
		return nil
	}, text, "auto", sid, role, "eval-user")
	return events
}

func firstEvent(events []any, etype string) any {
	for _, ev := range events {
		if typeNameOf(ev) == etype {
			return ev
		}
	}
	return nil
}

// typeNameOf 各事件结构体的类型名（测试辅助断言用）。
func typeNameOf(ev any) string {
	switch ev.(type) {
	case routeEvent:
		return "route"
	case statusEvent:
		return "status"
	case stepEvent:
		return "step"
	case answerDeltaEvent:
		return "answer_delta"
	case citationsEvent:
		return "citations"
	case slotQuestionEvent:
		return "slot_question"
	case pendingActionEvent:
		return "pending_action"
	case actionResultEvent:
		return "action_result"
	case errorEvent:
		return "error"
	case doneEvent:
		return "done"
	}
	return "unknown"
}

func eventsOf(events []any, etype string) []any {
	var out []any
	for _, ev := range events {
		if typeNameOf(ev) == etype {
			out = append(out, ev)
		}
	}
	return out
}

func answerText(events []any) string {
	var sb strings.Builder
	for _, ev := range eventsOf(events, "answer_delta") {
		sb.WriteString(ev.(answerDeltaEvent).Text)
	}
	return sb.String()
}

var tomorrowISO = func() string { return dates.Today().AddDate(0, 0, 1).Format("2006-01-02") }

// ---------- 路由启发式（对应 Python test_router.py） ----------

func TestToolsPermissionMatrix(t *testing.T) {
	d := testDeps(t)
	if r := d.CallTool("pending_leaves", map[string]string{}, "student", "demo-student"); r.OK || r.Err != "permission" {
		t.Errorf("学生查待审批应拦截: %+v", r)
	}
	// 辅导员可见：先造一张单
	if r := d.Business.SubmitLeave("demo-student", "事假", "2099-01-01", "2099-01-02", "家事"); !r.OK {
		t.Fatal(r)
	}
	r := d.CallTool("pending_leaves", map[string]string{}, "counselor", "demo-counselor")
	if !r.OK || !strings.Contains(r.Message, "LV-") {
		t.Errorf("辅导员应可查: %+v", r)
	}
	if r := d.CallTool("drop_tables", map[string]string{}, "student", "u"); r.OK || r.Err != "unknown_tool" {
		t.Errorf("未知工具: %+v", r)
	}
	student := d.ToolDescriptions("student")
	counselor := d.ToolDescriptions("counselor")
	if strings.Contains(student, "pending_leaves") || !strings.Contains(counselor, "pending_leaves") {
		t.Error("工具清单应按角色过滤")
	}
	if r := d.CallTool("book_venue", map[string]string{"venue": "venue-room301"}, "student", "u"); r.Err != "missing_arg" || r.Message != "缺少参数：date" {
		t.Errorf("缺参数应 missing_arg: %+v", r)
	}
}

// ---------- 知行执行层端到端（离线，对应 Python test_transaction.py） ----------

func TestBookVenueHappyPathOneShot(t *testing.T) {
	d := testDeps(t)
	events := ask(t, d, "t1", "帮我预约明天晚上的羽毛球馆打班级比赛", "student")
	route := firstEvent(events, "route")
	if route == nil || route.(routeEvent).Route != "transaction" {
		t.Fatalf("route = %+v", route)
	}
	pending := firstEvent(events, "pending_action")
	if pending == nil || pending.(pendingActionEvent).Tool != "book_venue" {
		t.Fatalf("pending = %+v", pending)
	}
	if q := firstEvent(events, "slot_question"); q != nil {
		t.Errorf("信息一次给全不应追问: %+v", q)
	}

	events = ask(t, d, "t1", "确认", "student")
	result := firstEvent(events, "action_result").(actionResultEvent)
	if !result.Success || result.Receipt == nil || !strings.HasPrefix(*result.Receipt, "VE-") {
		t.Fatalf("result = %+v", result)
	}
	bookings, _ := d.Business.AllBookings()
	if len(bookings) != 1 || bookings[0].Venue != "羽毛球馆" || bookings[0].Date != tomorrowISO() || bookings[0].Slot != "19:00-21:00" {
		t.Errorf("bookings = %+v", bookings)
	}
}

func TestBookVenueGuidedClarification(t *testing.T) {
	d := testDeps(t)
	sid := "t2"
	events := ask(t, d, sid, "帮我预约研讨间301", "student")
	q := firstEvent(events, "slot_question").(slotQuestionEvent)
	if q.Slot != "date" {
		t.Fatalf("第一问应是 date: %+v", q)
	}
	events = ask(t, d, sid, "明天下午", "student")
	q = firstEvent(events, "slot_question").(slotQuestionEvent)
	if q.Slot != "slot" { // 下午有两个时段，需指明
		t.Fatalf("第二问应是 slot: %+v", q)
	}
	events = ask(t, d, sid, "14:00-16:00", "student")
	if firstEvent(events, "pending_action") == nil {
		t.Fatal("应进入确认")
	}
	events = ask(t, d, sid, "确认", "student")
	if r := firstEvent(events, "action_result").(actionResultEvent); !r.Success {
		t.Fatalf("执行失败: %+v", r)
	}
}

func TestConflictRecovery(t *testing.T) {
	d := testDeps(t)
	sid := "t3"
	dayAfter := dates.Today().AddDate(0, 0, 2).Format("2006-01-02")
	ask(t, d, sid, "帮我预约后天上午10点到12点的研讨间302自习", "student")
	ask(t, d, sid, "确认", "student")

	ask(t, d, sid, "再帮我预约后天上午10点到12点的研讨间302，和同学讨论", "student")
	events := ask(t, d, sid, "确认", "student")
	failed := firstEvent(events, "action_result").(actionResultEvent)
	if failed.Success {
		t.Fatal("冲突执行应失败")
	}
	q := firstEvent(events, "slot_question").(slotQuestionEvent)
	if q.Slot != "slot" || !strings.Contains(q.Question, "14:00-16:00") {
		t.Fatalf("冲突应重新追问时段并给可选项: %+v", q)
	}

	ask(t, d, sid, "那就 14:00 到 16:00 吧", "student")
	events = ask(t, d, sid, "确认", "student")
	if r := firstEvent(events, "action_result").(actionResultEvent); !r.Success {
		t.Fatalf("换时段后应成功: %+v", r)
	}

	bookings, _ := d.Business.AllBookings()
	slots := map[string]bool{}
	for _, b := range bookings {
		if b.Date == dayAfter {
			slots[b.Slot] = true
		}
	}
	if len(slots) != 2 || !slots["10:00-12:00"] || !slots["14:00-16:00"] {
		t.Errorf("slots = %v", slots)
	}
}

func TestLeaveDaysPhraseAndApprover(t *testing.T) {
	d := testDeps(t)
	sid := "t4"
	events := ask(t, d, sid, "帮我请下周一到下周二的事假", "student")
	q := firstEvent(events, "slot_question").(slotQuestionEvent)
	if q.Slot != "reason" {
		t.Fatalf("起止与类型齐后应只追问事由: %+v", q)
	}
	events = ask(t, d, sid, "家中有事", "student")
	pending := firstEvent(events, "pending_action").(pendingActionEvent)
	if v, ok := pending.Args.vals["共"]; !ok || v != "2 天" {
		t.Errorf("确认摘要应含 2 天: %+v", pending.Args.vals)
	}
	events = ask(t, d, sid, "确认", "student")
	if r := firstEvent(events, "action_result").(actionResultEvent); !r.Success {
		t.Fatalf("result = %+v", r)
	}
	tickets, _ := d.Business.AllTickets()
	last := tickets[len(tickets)-1]
	if last.Days != 2 || last.Approver != "辅导员" {
		t.Errorf("ticket = %+v", last)
	}
}

func TestLeaveOneDayPhrase(t *testing.T) {
	d := testDeps(t)
	sid := "t5"
	events := ask(t, d, sid, "帮我提交明天一天的病假申请", "student")
	q := firstEvent(events, "slot_question").(slotQuestionEvent)
	if q.Slot != "reason" {
		t.Fatalf("明天+一天 → 起止都齐, 只缺事由: %+v", q)
	}
	ask(t, d, sid, "发烧需要休息", "student")
	events = ask(t, d, sid, "确认", "student")
	if r := firstEvent(events, "action_result").(actionResultEvent); !r.Success {
		t.Fatalf("result = %+v", r)
	}
	tickets, _ := d.Business.AllTickets()
	if tickets[len(tickets)-1].Days != 1 {
		t.Errorf("days = %d, want 1", tickets[len(tickets)-1].Days)
	}
}

func TestPermissionDeniedForStudent(t *testing.T) {
	d := testDeps(t)
	events := ask(t, d, "t6", "帮我看下现在有哪些待审批的请假", "student")
	r := firstEvent(events, "action_result").(actionResultEvent)
	if r.Success || !strings.Contains(r.Message, "无权") {
		t.Fatalf("学生应被拒: %+v", r)
	}
}

func TestTopicSwitchAbandonsFlow(t *testing.T) {
	d := testDeps(t)
	sid := "t7"
	ask(t, d, sid, "帮我预约研讨间301", "student") // 进入追问日期阶段
	events := ask(t, d, sid, "图书馆几点开门", "student")
	if r := firstEvent(events, "route").(routeEvent); r.Route != "factual" {
		t.Fatalf("切话题应走正常路由: %+v", r)
	}
	if firstEvent(events, "action_result") != nil {
		t.Error("不应有业务执行")
	}
	if d.Sessions.Get(sid) != nil {
		t.Error("流程应已放弃")
	}
}

func TestCancelDuringConfirm(t *testing.T) {
	d := testDeps(t)
	sid := "t8"
	ask(t, d, sid, "帮我预约明天晚上的篮球场训练", "student")
	events := ask(t, d, sid, "算了不约了", "student")
	if !strings.Contains(answerText(events), "已取消") {
		t.Fatalf("answer = %q", answerText(events))
	}
	if d.Sessions.Get(sid) != nil {
		t.Error("会话应已清空")
	}
}

func TestReadToolDirectAnswer(t *testing.T) {
	d := testDeps(t)
	events := ask(t, d, "t9", "现在有哪些场馆可以预约", "student")
	r := firstEvent(events, "action_result").(actionResultEvent)
	if !r.Success || !strings.Contains(r.Message, "羽毛球馆") {
		t.Fatalf("result = %+v", r)
	}
}

// ---------- 续轮意图 ----------

func TestClassifyReplyHeuristic(t *testing.T) {
	d := testDeps(t)
	sess := &TxSession{Phase: PhaseConfirm, Tool: "book_venue", Slots: map[string]string{"venue": "venue-badminton"}}
	cases := map[string]string{
		"确认":      "continue",
		"算了不办了":   "cancel",
		"图书馆几点开门": "new_topic",
		"羽毛球馆":    "continue",
		"明天下午三点":  "continue",
	}
	for text, want := range cases {
		if got := d.ClassifyReply(context.Background(), text, sess); got != want {
			t.Errorf("ClassifyReply(%q) = %s, want %s", text, got, want)
		}
	}
}

// ---------- 工具识别与时段解析 ----------

func TestDetectTool(t *testing.T) {
	cases := map[string]string{
		"帮我预约明天晚上的羽毛球馆":   "book_venue",
		"取消预约 VE-0001":    "cancel_booking",
		"现在有哪些场馆可以预约":     "query_venues",
		"帮我请下周三的假":        "",             // 无「请假/事假/请.*天.*假」字面，启发式不识别（在线时由 LLM 兜底）
		"帮我请三天假":          "submit_leave", // 命中 请.*天.*假
		"查一下 LV-0002 的进度": "leave_status",
		"帮我看下现在有哪些待审批的请假": "pending_leaves",
		"批准 LV-0003":      "approve_leave",
		"我的预约有哪些":         "my_bookings",
		"图书馆几点开门":         "",
	}
	for q, want := range cases {
		if got := DetectTool(q); got != want {
			t.Errorf("DetectTool(%q) = %q, want %q", q, got, want)
		}
	}
}

func TestParseSlotVariants(t *testing.T) {
	d := testDeps(t)
	cases := map[string]string{
		"14:00-16:00":     "14:00-16:00",
		"晚上":              "19:00-21:00",
		"中午":              "14:00-16:00",
		"10:00":           "10:00-12:00",
		"16:00-18:00 有空吗": "16:00-18:00",
		"14点":             "14:00-16:00",
	}
	for in, want := range cases {
		if got := d.parseSlot(in); got != want {
			t.Errorf("parseSlot(%q) = %q, want %q", in, got, want)
		}
	}
	// 多选时段词与小时映射不到的点位均应要求用户明示（与 Python 一致）
	for _, in := range []string{"下午", "下午3点", "上午十点"} {
		if got := d.parseSlot(in); got != "" {
			t.Errorf("parseSlot(%q) = %q, want 空", in, got)
		}
	}
}

// TestUserSpecifiedDirectRoutesToFactual 用户指定 mode=direct 归一为 factual 直答
// （P9 拆包时 Route 直接取 "direct" 落进未知路由分支的回归守护）。
func TestUserSpecifiedDirectRoutesToFactual(t *testing.T) {
	d := testDeps(t)
	if dec := d.decideRoute(context.Background(), "图书馆几点开门", "direct"); dec.Route != "factual" {
		t.Fatalf("mode=direct 应归一为 factual, got %s", dec.Route)
	}
	if dec := d.decideRoute(context.Background(), "转专业政策", "research"); dec.Route != "research" {
		t.Fatalf("mode=research 应保持 research, got %s", dec.Route)
	}
}
