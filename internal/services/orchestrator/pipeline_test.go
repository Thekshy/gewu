package orchestrator

import (
	"context"
	"net"
	"path/filepath"
	"strings"
	"testing"
	"time"

	conversationv1 "gewu/pkg/gen/gewu/conversation/v1"
	generatev1 "gewu/pkg/gen/gewu/generate/v1"
	orchestratorv1 "gewu/pkg/gen/gewu/orchestrator/v1"
	ragv1 "gewu/pkg/gen/gewu/rag/v1"

	"gewu/internal/agent"
	"gewu/internal/business"
	"gewu/internal/dates"
	"gewu/internal/services/conversation"

	"google.golang.org/grpc"
	"google.golang.org/grpc/credentials/insecure"

	"go.uber.org/zap"
)

// ---------- 测试装配 ----------

// fakeGen 可编程的 generate 服务桩（P1 遗留结构，沿用）。
type fakeGen struct {
	generatev1.UnimplementedGenerateServiceServer
	hasKey    bool
	ensureErr error
	chatOut   string
	chatErr   error
	deltas    []string
}

func (f *fakeGen) EnsureBudget(ctx context.Context, req *generatev1.EnsureBudgetRequest) (*generatev1.EnsureBudgetResponse, error) {
	if f.ensureErr != nil {
		return nil, f.ensureErr
	}
	return &generatev1.EnsureBudgetResponse{HasKey: f.hasKey}, nil
}

func (f *fakeGen) Chat(ctx context.Context, req *generatev1.ChatRequest) (*generatev1.ChatResponse, error) {
	if f.chatErr != nil {
		return nil, f.chatErr
	}
	return &generatev1.ChatResponse{Content: f.chatOut}, nil
}

func (f *fakeGen) ChatStream(req *generatev1.ChatStreamRequest, stream generatev1.GenerateService_ChatStreamServer) error {
	for _, d := range f.deltas {
		if err := stream.Send(&generatev1.ChatStreamResponse{Text: d}); err != nil {
			return err
		}
	}
	return nil
}

// dialFakeGen 起一个 fake generate gRPC 服务并返回客户端。
func dialFakeGen(t *testing.T, fake *fakeGen) generatev1.GenerateServiceClient {
	t.Helper()
	lis, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	srv := grpc.NewServer()
	generatev1.RegisterGenerateServiceServer(srv, fake)
	go func() { _ = srv.Serve(lis) }()
	t.Cleanup(srv.Stop)
	cc, err := grpc.NewClient(lis.Addr().String(), grpc.WithTransportCredentials(insecure.NewCredentials()))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = cc.Close() })
	return generatev1.NewGenerateServiceClient(cc)
}

// fakeRag 可编程的检索服务桩（缺省空命中）。
type fakeRag struct {
	ragv1.UnimplementedRagServiceServer
	hits []*ragv1.Hit
}

func (f *fakeRag) Search(ctx context.Context, req *ragv1.SearchRequest) (*ragv1.SearchResponse, error) {
	return &ragv1.SearchResponse{Hits: f.hits}, nil
}

// dialFakeRag 起一个 fake rag gRPC 服务并返回客户端。
func dialFakeRag(t *testing.T, fake *fakeRag) ragv1.RagServiceClient {
	t.Helper()
	lis, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	srv := grpc.NewServer()
	ragv1.RegisterRagServiceServer(srv, fake)
	go func() { _ = srv.Serve(lis) }()
	t.Cleanup(srv.Stop)
	cc, err := grpc.NewClient(lis.Addr().String(), grpc.WithTransportCredentials(insecure.NewCredentials()))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = cc.Close() })
	return ragv1.NewRagServiceClient(cc)
}

// dialRealConversation 起一个真实 conversation gRPC 服务（内存存储）并返回
// 编排侧 gRPC 会话存取——跨进程集成路径。
func dialRealConversation(t *testing.T) sessionStore {
	t.Helper()
	lis, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	srv := grpc.NewServer()
	conversationv1.RegisterConversationServiceServer(srv,
		conversation.NewServer(conversation.NewMemoryStore(), zap.NewNop()))
	go func() { _ = srv.Serve(lis) }()
	t.Cleanup(srv.Stop)
	cc, err := grpc.NewClient(lis.Addr().String(), grpc.WithTransportCredentials(insecure.NewCredentials()))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = cc.Close() })
	return newGrpcSessions(conversationv1.NewConversationServiceClient(cc))
}

// testServer 构造零 key（离线确定性链路）的编排服务。
func testServer(t *testing.T) *Server {
	t.Helper()
	biz, err := business.Open(filepath.Join(t.TempDir(), "biz.db"))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = biz.Close() })
	cs, err := openConfigStore("", "", zap.NewNop())
	if err != nil {
		t.Fatal(err)
	}
	return newServerWithDeps(LoadConfig(), dialFakeGen(t, &fakeGen{hasKey: false}),
		newMemorySessions(), biz, cs, zap.NewNop())
}

// ask 跑一轮对话，收集全部事件。
func ask(t *testing.T, s *Server, sid, text string, role string) []*orchestratorv1.ChatResponse {
	t.Helper()
	ctx := context.Background()
	ensure, err := s.generate.EnsureBudget(ctx, &generatev1.EnsureBudgetRequest{})
	if err != nil {
		t.Fatalf("EnsureBudget: %v", err)
	}
	var events []*orchestratorv1.ChatResponse
	err = s.runChat(ctx, func(ev *orchestratorv1.ChatResponse) error {
		events = append(events, ev)
		return nil
	}, &orchestratorv1.ChatRequest{Question: text, Mode: "auto", SessionId: sid, Role: role},
		ensure.HasKey, time.Now())
	if err != nil {
		t.Fatalf("runChat: %v", err)
	}
	return events
}

// ---------- 事件辅助 ----------

func eventKind(ev *orchestratorv1.ChatResponse) string {
	switch ev.GetKind().(type) {
	case *orchestratorv1.ChatResponse_Route:
		return "route"
	case *orchestratorv1.ChatResponse_Status:
		return "status"
	case *orchestratorv1.ChatResponse_Step:
		return "step"
	case *orchestratorv1.ChatResponse_AnswerDelta:
		return "answer_delta"
	case *orchestratorv1.ChatResponse_Citations:
		return "citations"
	case *orchestratorv1.ChatResponse_SlotQuestion:
		return "slot_question"
	case *orchestratorv1.ChatResponse_PendingAction:
		return "pending_action"
	case *orchestratorv1.ChatResponse_ActionResult:
		return "action_result"
	case *orchestratorv1.ChatResponse_Error:
		return "error"
	case *orchestratorv1.ChatResponse_Done:
		return "done"
	}
	return "?"
}

func firstOf(events []*orchestratorv1.ChatResponse, kind string) *orchestratorv1.ChatResponse {
	for _, ev := range events {
		if eventKind(ev) == kind {
			return ev
		}
	}
	return nil
}

func eventsOf(events []*orchestratorv1.ChatResponse, kind string) []*orchestratorv1.ChatResponse {
	var out []*orchestratorv1.ChatResponse
	for _, ev := range events {
		if eventKind(ev) == kind {
			out = append(out, ev)
		}
	}
	return out
}

func answerText(events []*orchestratorv1.ChatResponse) string {
	var sb strings.Builder
	for _, ev := range eventsOf(events, "answer_delta") {
		sb.WriteString(ev.GetAnswerDelta().GetText())
	}
	return sb.String()
}

var tomorrowISO = func() string { return dates.Today().AddDate(0, 0, 1).Format("2006-01-02") }

// ---------- 工具层权限（对应冻结 agent_test.go） ----------

func TestToolsPermissionMatrix(t *testing.T) {
	s := testServer(t)
	if r := s.callTool("pending_leaves", map[string]string{}, "student", "demo-student"); r.OK || r.Err != "permission" {
		t.Errorf("学生查待审批应拦截: %+v", r)
	}
	if r := s.business.SubmitLeave("demo-student", "事假", "2099-01-01", "2099-01-02", "家事"); !r.OK {
		t.Fatal(r)
	}
	r := s.callTool("pending_leaves", map[string]string{}, "counselor", "demo-counselor")
	if !r.OK || !strings.Contains(r.Message, "LV-") {
		t.Errorf("辅导员应可查: %+v", r)
	}
	if r := s.callTool("drop_tables", map[string]string{}, "student", "u"); r.OK || r.Err != "unknown_tool" {
		t.Errorf("未知工具: %+v", r)
	}
	student := s.toolDescriptions("student")
	counselor := s.toolDescriptions("counselor")
	if strings.Contains(student, "pending_leaves") || !strings.Contains(counselor, "pending_leaves") {
		t.Error("工具清单应按角色过滤")
	}
	if r := s.callTool("book_venue", map[string]string{"venue": "venue-room301"}, "student", "u"); r.Err != "missing_arg" || r.Message != "缺少参数：date" {
		t.Errorf("缺参数应 missing_arg: %+v", r)
	}
}

// ---------- 知行执行层端到端（离线确定性：collect/confirm/修改/取消/冲突恢复） ----------

func TestBookVenueHappyPathOneShot(t *testing.T) {
	s := testServer(t)
	events := ask(t, s, "t1", "帮我预约明天晚上的羽毛球馆打班级比赛", "student")
	if route := firstOf(events, "route"); route == nil || route.GetRoute().GetRoute() != "transaction" {
		t.Fatalf("route = %+v", firstOf(events, "route"))
	}
	pending := firstOf(events, "pending_action")
	if pending == nil || pending.GetPendingAction().GetTool() != "book_venue" {
		t.Fatalf("pending = %+v", pending)
	}
	if q := firstOf(events, "slot_question"); q != nil {
		t.Errorf("信息一次给全不应追问: %+v", q)
	}

	events = ask(t, s, "t1", "确认", "student")
	result := firstOf(events, "action_result").GetActionResult()
	if !result.GetSuccess() || !strings.HasPrefix(result.GetReceipt(), "VE-") {
		t.Fatalf("result = %+v", result)
	}
	bookings, _ := s.business.AllBookings()
	if len(bookings) != 1 || bookings[0].Venue != "羽毛球馆" || bookings[0].Date != tomorrowISO() || bookings[0].Slot != "19:00-21:00" {
		t.Errorf("bookings = %+v", bookings)
	}
}

func TestBookVenueGuidedClarification(t *testing.T) {
	s := testServer(t)
	sid := "t2"
	events := ask(t, s, sid, "帮我预约研讨间301", "student")
	if q := firstOf(events, "slot_question").GetSlotQuestion(); q.GetSlot() != "date" {
		t.Fatalf("第一问应是 date: %+v", q)
	}
	events = ask(t, s, sid, "明天下午", "student")
	if q := firstOf(events, "slot_question").GetSlotQuestion(); q.GetSlot() != "slot" { // 下午有两个时段，需指明
		t.Fatalf("第二问应是 slot: %+v", q)
	}
	events = ask(t, s, sid, "14:00-16:00", "student")
	if firstOf(events, "pending_action") == nil {
		t.Fatal("应进入确认")
	}
	events = ask(t, s, sid, "确认", "student")
	if r := firstOf(events, "action_result").GetActionResult(); !r.GetSuccess() {
		t.Fatalf("执行失败: %+v", r)
	}
}

func TestConflictRecovery(t *testing.T) {
	s := testServer(t)
	sid := "t3"
	dayAfter := dates.Today().AddDate(0, 0, 2).Format("2006-01-02")
	ask(t, s, sid, "帮我预约后天上午10点到12点的研讨间302自习", "student")
	ask(t, s, sid, "确认", "student")

	ask(t, s, sid, "再帮我预约后天上午10点到12点的研讨间302，和同学讨论", "student")
	events := ask(t, s, sid, "确认", "student")
	if failed := firstOf(events, "action_result").GetActionResult(); failed.GetSuccess() {
		t.Fatal("冲突执行应失败")
	}
	q := firstOf(events, "slot_question").GetSlotQuestion()
	if q.GetSlot() != "slot" || !strings.Contains(q.GetQuestion(), "14:00-16:00") {
		t.Fatalf("冲突应重新追问时段并给可选项: %+v", q)
	}

	ask(t, s, sid, "那就 14:00 到 16:00 吧", "student")
	events = ask(t, s, sid, "确认", "student")
	if r := firstOf(events, "action_result").GetActionResult(); !r.GetSuccess() {
		t.Fatalf("换时段后应成功: %+v", r)
	}

	bookings, _ := s.business.AllBookings()
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
	s := testServer(t)
	sid := "t4"
	events := ask(t, s, sid, "帮我请下周一到下周二的事假", "student")
	if q := firstOf(events, "slot_question").GetSlotQuestion(); q.GetSlot() != "reason" {
		t.Fatalf("起止与类型齐后应只追问事由: %+v", q)
	}
	events = ask(t, s, sid, "家中有事", "student")
	pending := firstOf(events, "pending_action").GetPendingAction()
	var daysVal string
	for _, kv := range pending.GetArgs() {
		if kv.GetKey() == "共" {
			daysVal = kv.GetValue()
		}
	}
	if daysVal != "2 天" {
		t.Errorf("确认摘要应含 2 天: %v", pending.GetArgs())
	}
	events = ask(t, s, sid, "确认", "student")
	if r := firstOf(events, "action_result").GetActionResult(); !r.GetSuccess() {
		t.Fatalf("result = %+v", r)
	}
	tickets, _ := s.business.AllTickets()
	last := tickets[len(tickets)-1]
	if last.Days != 2 || last.Approver != "辅导员" {
		t.Errorf("ticket = %+v", last)
	}
}

func TestLeaveOneDayPhrase(t *testing.T) {
	s := testServer(t)
	sid := "t5"
	events := ask(t, s, sid, "帮我提交明天一天的病假申请", "student")
	if q := firstOf(events, "slot_question").GetSlotQuestion(); q.GetSlot() != "reason" {
		t.Fatalf("明天+一天 → 起止都齐, 只缺事由: %+v", q)
	}
	ask(t, s, sid, "发烧需要休息", "student")
	events = ask(t, s, sid, "确认", "student")
	if r := firstOf(events, "action_result").GetActionResult(); !r.GetSuccess() {
		t.Fatalf("result = %+v", r)
	}
	tickets, _ := s.business.AllTickets()
	if tickets[len(tickets)-1].Days != 1 {
		t.Errorf("days = %d, want 1", tickets[len(tickets)-1].Days)
	}
}

func TestPermissionDeniedForStudent(t *testing.T) {
	s := testServer(t)
	events := ask(t, s, "t6", "帮我看下现在有哪些待审批的请假", "student")
	r := firstOf(events, "action_result").GetActionResult()
	if r.GetSuccess() || !strings.Contains(r.GetMessage(), "无权") {
		t.Fatalf("学生应被拒: %+v", r)
	}
}

func TestTopicSwitchAbandonsFlow(t *testing.T) {
	s := testServer(t)
	sid := "t7"
	ask(t, s, sid, "帮我预约研讨间301", "student") // 进入追问日期阶段
	events := ask(t, s, sid, "图书馆几点开门", "student")
	if r := firstOf(events, "route").GetRoute(); r.GetRoute() != "factual" {
		t.Fatalf("切话题应走正常路由: %+v", r)
	}
	if firstOf(events, "action_result") != nil {
		t.Error("不应有业务执行")
	}
	if sess, _ := s.sessions.Get(context.Background(), sid); sess != nil {
		t.Error("流程应已放弃")
	}
}

func TestCancelDuringConfirm(t *testing.T) {
	s := testServer(t)
	sid := "t8"
	ask(t, s, sid, "帮我预约明天晚上的篮球场训练", "student")
	events := ask(t, s, sid, "算了不约了", "student")
	if !strings.Contains(answerText(events), "已取消") {
		t.Fatalf("answer = %q", answerText(events))
	}
	if sess, _ := s.sessions.Get(context.Background(), sid); sess != nil {
		t.Error("会话应已清空")
	}
}

func TestReadToolDirectAnswer(t *testing.T) {
	s := testServer(t)
	events := ask(t, s, "t9", "现在有哪些场馆可以预约", "student")
	r := firstOf(events, "action_result").GetActionResult()
	if !r.GetSuccess() || !strings.Contains(r.GetMessage(), "羽毛球馆") {
		t.Fatalf("result = %+v", r)
	}
}

// ---------- 续轮意图 / 工具识别 / 时段解析 ----------

func TestClassifyReplyHeuristic(t *testing.T) {
	s := testServer(t)
	sess := &TxSession{Phase: PhaseConfirm, Tool: "book_venue", Slots: map[string]string{"venue": "venue-badminton"}}
	cases := map[string]string{
		"确认":      "continue",
		"算了不办了":   "cancel",
		"图书馆几点开门": "new_topic",
		"羽毛球馆":    "continue",
		"明天下午三点":  "continue",
	}
	for text, want := range cases {
		if got := s.classifyReply(context.Background(), text, sess, false); got != want {
			t.Errorf("classifyReply(%q) = %s, want %s", text, got, want)
		}
	}
}

func TestDetectTool(t *testing.T) {
	cases := map[string]string{
		"帮我预约明天晚上的羽毛球馆":   "book_venue",
		"取消预约 VE-0001":    "cancel_booking",
		"现在有哪些场馆可以预约":     "query_venues",
		"帮我请下周三的假":        "", // 无字面命中，启发式不识别（在线时由 LLM 兜底）
		"帮我请三天假":          "submit_leave",
		"查一下 LV-0002 的进度": "leave_status",
		"帮我看下现在有哪些待审批的请假": "pending_leaves",
		"批准 LV-0003":      "approve_leave",
		"我的预约有哪些":         "my_bookings",
		"图书馆几点开门":         "",
	}
	for q, want := range cases {
		if got := detectTool(q); got != want {
			t.Errorf("detectTool(%q) = %q, want %q", q, got, want)
		}
	}
}

func TestParseSlotVariants(t *testing.T) {
	s := testServer(t)
	cases := map[string]string{
		"14:00-16:00":     "14:00-16:00",
		"晚上":              "19:00-21:00",
		"中午":              "14:00-16:00",
		"10:00":           "10:00-12:00",
		"16:00-18:00 有空吗": "16:00-18:00",
		"14点":             "14:00-16:00",
	}
	for in, want := range cases {
		if got := s.parseSlot(in); got != want {
			t.Errorf("parseSlot(%q) = %q, want %q", in, got, want)
		}
	}
	for _, in := range []string{"下午", "下午3点", "上午十点"} {
		if got := s.parseSlot(in); got != "" {
			t.Errorf("parseSlot(%q) = %q, want 空", in, got)
		}
	}
}

// ---------- P2 验收：配置默认行 + 跨进程会话 ----------

// TestAgentConfigDefaultMatchesPrompts 默认行与冻结 prompts.go 逐字一致（P2 验收）。
func TestAgentConfigDefaultMatchesPrompts(t *testing.T) {
	cs, err := openConfigStore("", "", zap.NewNop())
	if err != nil {
		t.Fatal(err)
	}
	cfg, isDefault, err := cs.Get(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	want := AgentConfig{
		RouterSystem:   agent.RouterSystem,
		SlotExtractSys: agent.SlotExtractSystem,
		PlannerSystem:  agent.PlannerSystem,
		AnswerSystem:   agent.AnswerSystem,
	}
	if !isDefault || cfg != want {
		t.Fatalf("默认配置与 prompts.go 不一致：isDefault=%v\n got: %+v\nwant: %+v", isDefault, cfg, want)
	}
	// Set 空字段沿用；改一个字段后 is_default 翻转
	updated, err := cs.Set(context.Background(), AgentConfig{AnswerSystem: "测试提示词"})
	if err != nil {
		t.Fatal(err)
	}
	if updated.RouterSystem != want.RouterSystem || updated.AnswerSystem != "测试提示词" {
		t.Fatalf("Set 语义不匹配: %+v", updated)
	}
	if _, isDefault, _ := cs.Get(context.Background()); isDefault {
		t.Fatal("修改后不应再是默认行")
	}
}

// TestCrossProcessSessionFlow 跨进程集成：编排 ↔ conversation（gRPC）多轮办理。
func TestCrossProcessSessionFlow(t *testing.T) {
	biz, err := business.Open(filepath.Join(t.TempDir(), "biz.db"))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = biz.Close() })
	cs, _ := openConfigStore("", "", zap.NewNop())
	s := newServerWithDeps(LoadConfig(), dialFakeGen(t, &fakeGen{hasKey: false}),
		dialRealConversation(t), biz, cs, zap.NewNop())

	sid := "cross-1"
	events := ask(t, s, sid, "帮我预约研讨间301", "student")
	if q := firstOf(events, "slot_question").GetSlotQuestion(); q.GetSlot() != "date" {
		t.Fatalf("应经 conversation 追问 date: %+v", q)
	}
	events = ask(t, s, sid, "明天下午", "student")
	if q := firstOf(events, "slot_question").GetSlotQuestion(); q.GetSlot() != "slot" {
		t.Fatalf("会话状态应经 conversation 跨轮保持: %+v", q)
	}
	ask(t, s, sid, "14:00-16:00", "student")
	events = ask(t, s, sid, "确认", "student")
	if r := firstOf(events, "action_result").GetActionResult(); !r.GetSuccess() {
		t.Fatalf("跨进程办理应成功: %+v", r)
	}
	if sess, _ := s.sessions.Get(context.Background(), sid); sess != nil {
		t.Error("办理完成后会话应清除")
	}
}

// ---------- P3：直答/深研检索链路 ----------

// ask2 带 err 返回的 ask 变体。
func ask2(t *testing.T, s *Server, text string) ([]*orchestratorv1.ChatResponse, error) {
	t.Helper()
	return ask(t, s, "p3-"+t.Name(), text, "student"), nil
}

func TestDirectZeroKeyWithHitsDemoText(t *testing.T) {
	biz, _ := business.Open(filepath.Join(t.TempDir(), "biz.db"))
	cs, _ := openConfigStore("", "", zap.NewNop())
	s := newServerWithDeps(LoadConfig(), dialFakeGen(t, &fakeGen{hasKey: false}),
		newMemorySessions(), biz, cs, zap.NewNop())
	s.rag = dialFakeRag(t, &fakeRag{hits: []*ragv1.Hit{
		{ChunkId: 1, DocId: "0007-library", Seq: 0, Text: "图书馆开放时间为 7:00-22:00。", Title: "图书馆服务指南", Source: "钱塘大学"},
	}})
	events, err := ask2(t, s, "图书馆几点开门")
	if err != nil {
		t.Fatal(err)
	}
	answer := answerText(events)
	if !strings.Contains(answer, agent.DemoModeNote) ||
		!strings.Contains(answer, "[1] 《图书馆服务指南》：图书馆开放时间为 7:00-22:00。…") {
		t.Fatalf("零 key 直答应为检索节选演示文案：%q", answer)
	}
	cites := firstOf(events, "citations").GetCitations().GetItems()
	if len(cites) != 1 || cites[0].GetDocId() != "0007-library" {
		t.Fatalf("引用不匹配：%v", cites)
	}
}

func TestDirectStreamWithHits(t *testing.T) {
	biz, _ := business.Open(filepath.Join(t.TempDir(), "biz.db"))
	cs, _ := openConfigStore("", "", zap.NewNop())
	s := newServerWithDeps(LoadConfig(), dialFakeGen(t, &fakeGen{
		hasKey: true, deltas: []string{"第一", "第二"},
	}), newMemorySessions(), biz, cs, zap.NewNop())
	s.rag = dialFakeRag(t, &fakeRag{hits: []*ragv1.Hit{
		{ChunkId: 1, DocId: "0007-library", Seq: 0, Text: "图书馆开放时间为 7:00-22:00。", Title: "图书馆服务指南", Source: "钱塘大学"},
	}})
	events, err := ask2(t, s, "图书馆几点开门")
	if err != nil {
		t.Fatal(err)
	}
	kinds := make([]string, 0, len(events))
	for _, ev := range events {
		kinds = append(kinds, eventKind(ev))
	}
	want := []string{"route", "answer_delta", "answer_delta", "citations", "done"}
	if len(kinds) != len(want) {
		t.Fatalf("事件序不匹配：%v", kinds)
	}
	for i := range want {
		if kinds[i] != want[i] {
			t.Fatalf("事件序不匹配：%v", kinds)
		}
	}
	if answerText(events) != "第一第二" {
		t.Fatalf("流式增量不匹配：%q", answerText(events))
	}
}

func TestResearchZeroKeyStepsAndDemo(t *testing.T) {
	biz, _ := business.Open(filepath.Join(t.TempDir(), "biz.db"))
	cs, _ := openConfigStore("", "", zap.NewNop())
	s := newServerWithDeps(LoadConfig(), dialFakeGen(t, &fakeGen{hasKey: false}),
		newMemorySessions(), biz, cs, zap.NewNop())
	s.rag = dialFakeRag(t, &fakeRag{hits: []*ragv1.Hit{
		{ChunkId: 1, DocId: "0001-transfer", Seq: 0, Text: "转专业绩点要求 2.0 以上。", Title: "转专业管理办法", Source: "钱塘大学"},
	}})
	events, err := ask2(t, s, "转专业绩点要求以及申请流程分别是什么")
	if err != nil {
		t.Fatal(err)
	}
	// 零 key：plan=[原问题] → 事件序：
	// route → status(拆解) → step → status(证据数) → answer(演示) → citations → done
	var kinds []string
	for _, ev := range events {
		kinds = append(kinds, eventKind(ev))
	}
	if len(events) != 7 || kinds[0] != "route" || kinds[1] != "status" || kinds[2] != "step" {
		t.Fatalf("深研事件序不匹配：%v", kinds)
	}
	step := events[2].GetStep()
	if step.GetIndex() != 1 || step.GetSources() == nil {
		t.Fatalf("step 不匹配：%v", step)
	}
	answer := answerText(events)
	if !strings.Contains(answer, "围绕 1 个子问题共检索到 1 条相关段落") {
		t.Fatalf("深研演示文案不匹配：%q", answer)
	}
}
