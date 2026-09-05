package tool

import (
	"context"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"google.golang.org/grpc/metadata"

	toolv1 "gewu/pkg/gen/gewu/tool/v1"

	"gewu/internal/business"
	"gewu/internal/dates"

	"go.uber.org/zap"
)

// 工具/业务语义套件：pgBusiness（PG）与冻结 internal/business（SQLite）必须
// 通过同一套逐字断言（PARITY §8/§10）——同一套件锁两种实现。

type bizFixture struct {
	store bizStore
	kind  string
}

func openFixtures(t *testing.T) []bizFixture {
	t.Helper()
	var out []bizFixture
	// 冻结 SQLite 实现（对照基准）
	sqlBiz, err := business.Open(filepath.Join(t.TempDir(), "biz.db"))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = sqlBiz.Close() })
	out = append(out, bizFixture{sqlBiz, "sqlite"})
	// PG 实现（需 TEST_PG_DSN）
	if dsn := os.Getenv("TEST_PG_DSN"); dsn != "" {
		pg, err := openPGBusiness(dsn)
		if err != nil {
			t.Fatalf("连接 PG 失败：%v", err)
		}
		t.Cleanup(func() { _ = pg.Close() })
		if err := pg.Reset(); err != nil {
			t.Fatal(err)
		}
		out = append(out, bizFixture{pg, "pg"})
	} else {
		t.Log("TEST_PG_DSN 未设置，仅跑 SQLite 侧（compose: postgres://gewu:gewu@localhost:5432/gewu?sslmode=disable）")
	}
	return out
}

func futureISO(days int) string { return dates.Today().AddDate(0, 0, days).Format("2006-01-02") }

func TestBusinessSemantics(t *testing.T) {
	for _, fx := range openFixtures(t) {
		t.Run(fx.kind, func(t *testing.T) {
			ctx := context.Background()
			b := fx.store

			// 校验顺序（§8.1）：场馆不存在 → slot → date → quota → conflict
			if r := b.BookVenue("venue-x", futureISO(1), "08:00-10:00", "", "u"); r.Err != "invalid" || r.Message != "场馆不存在" {
				t.Fatalf("场馆不存在: %+v", r)
			}
			if r := b.BookVenue("venue-badminton", futureISO(1), "07:00-09:00", "", "u"); r.Err != "invalid" || r.Field != "slot" || r.Message != "时段不合法" {
				t.Fatalf("时段不合法: %+v", r)
			}
			if r := b.BookVenue("venue-badminton", "2000-01-01", "08:00-10:00", "", "u"); r.Err != "invalid" || r.Field != "date" || r.Message != "不能预约过去的日期" {
				t.Fatalf("过去日期: %+v", r)
			}
			// 配额与冲突
			d := futureISO(3)
			r1 := b.BookVenue("venue-basketball", d, "08:00-10:00", "", "u")
			r2 := b.BookVenue("venue-basketball", d, "10:00-12:00", "", "u")
			if !r1.OK || !r2.OK {
				t.Fatalf("前两次预约应成功: %+v %+v", r1, r2)
			}
			if !strings.HasPrefix(r1.Receipt, "VE-") || !strings.HasPrefix(r2.Receipt, "VE-") {
				t.Fatalf("单号形态: %s %s", r1.Receipt, r2.Receipt)
			}
			if r := b.BookVenue("venue-basketball", d, "14:00-16:00", "", "u"); r.Err != "quota" || r.Message != "每人每天最多预约 2 个时段" {
				t.Fatalf("配额: %+v", r)
			}
			if r := b.BookVenue("venue-basketball", d, "08:00-10:00", "", "other-user"); r.Err != "conflict" ||
				r.Field != "slot" || r.Message != "篮球场 "+d+" 的 08:00-10:00 已约满" {
				t.Fatalf("冲突: %+v", r)
			} else if len(r.Alternatives) != 3 || r.Alternatives[0] != "14:00-16:00" {
				t.Fatalf("可选项: %+v", r.Alternatives) // 08:00/10:00 已满，14:00 起有余量
			}
			_ = ctx

			// 取消：非本人 / 不存在
			if r := b.CancelBooking(r1.Receipt, "not-owner"); r.Err != "permission" || r.Message != "只能取消本人的预约" {
				t.Fatalf("非本人取消: %+v", r)
			}
			if r := b.CancelBooking("VE-9999", "u"); r.Err != "not_found" || r.Message != "预约记录不存在或已取消" {
				t.Fatalf("取消不存在: %+v", r)
			}
			if r := b.CancelBooking(r1.Receipt, "u"); !r.OK || r.Message != "预约 "+r1.Receipt+" 已取消" {
				t.Fatalf("取消: %+v", r)
			}

			// 请假：审批层级 / 校验顺序
			if r := b.SubmitLeave("u", "事假", "2099-01-05", "2099-01-04", "x"); r.Err != "invalid" ||
				r.Field != "end_date" || r.Message != "结束日期不能早于开始日期" {
				t.Fatalf("结束早于开始: %+v", r)
			}
			if r := b.SubmitLeave("u", "事假", "2000-01-01", "2000-01-02", "x"); r.Err != "invalid" ||
				r.Field != "start_date" || r.Message != "开始日期不能是过去" {
				t.Fatalf("开始是过去: %+v", r)
			}
			if r := b.SubmitLeave("u", "事假", "2099-01-01", "2099-01-02", "家事"); !r.OK ||
				r.Days != 2 || r.Approver != "辅导员" ||
				r.Message != "请假申请已提交（2 天），按学校规定将由辅导员审批" {
				t.Fatalf("请假: %+v", r)
			}
			if r := b.SubmitLeave("u", "事假", "2099-02-01", "2099-02-12", "家事"); !r.OK || r.Approver != "教务处" {
				t.Fatalf("超 7 天应教务处: %+v", r)
			}

			// leave_status：不存在/非本人/成功
			if r := b.LeaveStatus("LV-9999", "u"); r.Err != "not_found" || r.Message != "请假单不存在" {
				t.Fatalf("请假单不存在: %+v", r)
			}
			if r := b.LeaveStatus("LV-0001", "not-owner"); r.Err != "permission" || r.Message != "只能查询本人的请假单" {
				t.Fatalf("非本人查询: %+v", r)
			}
			// 第一张 LV-0001 即上面 2 天事假（两实现新库种子一致）
			if r := b.LeaveStatus("LV-0001", "u"); !r.OK || r.Days != 2 {
				t.Fatalf("查询: %+v", r)
			}

			// approve：已处理 / 成功
			if r := b.ApproveLeave("LV-9999"); r.Err != "not_found" {
				t.Fatalf("批准不存在: %+v", r)
			}
			if r := b.ApproveLeave("LV-0001"); !r.OK || r.Message != "请假单 LV-0001 已通过" {
				t.Fatalf("批准: %+v", r)
			}
			if r := b.ApproveLeave("LV-0001"); r.Err != "invalid" || r.Message != "该请假单已处理" {
				t.Fatalf("重复批准: %+v", r)
			}

			// 权限矩阵（经 callTool 单一出口）
			if r := callTool(b, "pending_leaves", map[string]string{}, "student", "demo-student"); r.OK || r.Err != "permission" ||
				!strings.Contains(r.Message, "当前身份（学生）无权执行「待审批请假」") {
				t.Fatalf("学生越权: %+v", r)
			}
			if r := callTool(b, "unknown", map[string]string{}, "student", "u"); r.Err != "unknown_tool" ||
				r.Message != "未知工具：unknown" {
				t.Fatalf("未知工具: %+v", r)
			}
			if r := callTool(b, "book_venue", map[string]string{"venue": "venue-room301"}, "student", "u"); r.Err != "missing_arg" ||
				r.Message != "缺少参数：date" {
				t.Fatalf("缺参: %+v", r)
			}

			// reset 保留场馆
			if err := b.Reset(); err != nil {
				t.Fatal(err)
			}
			venues, _ := b.ListVenues()
			if len(venues) != 4 {
				t.Fatalf("场馆应保留: %d", len(venues))
			}
			bookings, _ := b.AllBookings()
			tickets, _ := b.AllTickets()
			if len(bookings) != 0 || len(tickets) != 0 {
				t.Fatalf("reset 后应为空: %+v %+v", bookings, tickets)
			}
			// reset 后新单号继续增长（DELETE 不复位序列——两实现一致）
			if r := b.BookVenue("venue-badminton", futureISO(1), "08:00-10:00", "", "u"); !r.OK || r.Receipt == "VE-0001" {
				t.Fatalf("reset 后单号应继续增长: %+v", r)
			}
		})
	}
}

func TestToolServerGRPSShape(t *testing.T) {
	// 消息格式化与 user 注入（走 Server 层，冻结 SQLite 存储）
	sqlBiz, err := business.Open(filepath.Join(t.TempDir(), "biz.db"))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = sqlBiz.Close() })
	srv := NewServerWithStore(sqlBiz, zap.NewNop())

	// 缺 x-user → InvalidArgument
	if _, err := srv.CallTool(context.Background(), toolv1CallReq("query_venues", "student")); err == nil ||
		!strings.Contains(err.Error(), "缺少身份上下文") {
		t.Fatalf("缺身份应拒绝: %v", err)
	}
	// 带 x-user → 成功且消息格式逐字
	resp, err := srv.CallTool(withUserCtx("demo-student"), toolv1CallReq("query_venues", "student"))
	if err != nil || !resp.GetOk() {
		t.Fatalf("查询场馆: %v %+v", err, resp)
	}
	if !strings.Contains(resp.GetMessage(), "可预约场馆：\n- 羽毛球馆（体育场馆，每时段 2 组）：") {
		t.Fatalf("消息格式: %q", resp.GetMessage())
	}
	// 工具清单
	td, _ := srv.ToolDescriptions(context.Background(), toolv1DescReq("student"))
	if strings.Contains(td.GetDescriptions(), "pending_leaves") {
		t.Fatal("学生清单不应含 pending_leaves")
	}
	td, _ = srv.ToolDescriptions(context.Background(), toolv1DescReq("counselor"))
	if !strings.Contains(td.GetDescriptions(), "pending_leaves") {
		t.Fatal("辅导员清单应含 pending_leaves")
	}
}

// ---------- 测试辅助 ----------

func toolv1CallReq(toolName, role string) *toolv1.CallToolRequest {
	return &toolv1.CallToolRequest{Tool: toolName, Args: map[string]string{}, Role: role}
}

func toolv1DescReq(role string) *toolv1.ToolDescriptionsRequest {
	return &toolv1.ToolDescriptionsRequest{Role: role}
}

func withUserCtx(user string) context.Context {
	return metadata.NewIncomingContext(context.Background(), metadata.Pairs("x-user", user))
}
