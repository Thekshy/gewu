package business

import (
	"path/filepath"
	"testing"
	"time"

	"gewu/internal/dates"
)

func mkBiz(t *testing.T) *Business {
	t.Helper()
	b, err := Open(filepath.Join(t.TempDir(), "biz.db"))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = b.Close() })
	return b
}

func futureISO(days int) string {
	return dates.Today().AddDate(0, 0, days).Format("2006-01-02")
}

func TestBookConflictReturnsAlternatives(t *testing.T) {
	b := mkBiz(t)
	future := futureISO(1)
	if r := b.BookVenue("venue-room301", future, "10:00-12:00", "自习", "alice"); !r.OK {
		t.Fatalf("首次预约应成功: %+v", r)
	}
	clash := b.BookVenue("venue-room301", future, "10:00-12:00", "讨论", "bob")
	if clash.OK || clash.Err != "conflict" {
		t.Fatalf("冲突应返回 conflict: %+v", clash)
	}
	found := false
	for _, s := range clash.Alternatives {
		if s == "14:00-16:00" {
			found = true
		}
	}
	if !found {
		t.Errorf("alternatives 应含 14:00-16:00: %v", clash.Alternatives)
	}
}

func TestCapacityAllowsMultipleUntilFull(t *testing.T) {
	b := mkBiz(t)
	future := futureISO(2)
	if r := b.BookVenue("venue-badminton", future, "08:00-10:00", "a", "u1"); !r.OK {
		t.Fatal(r)
	}
	if r := b.BookVenue("venue-badminton", future, "08:00-10:00", "b", "u2"); !r.OK {
		t.Fatal(r)
	}
	third := b.BookVenue("venue-badminton", future, "08:00-10:00", "c", "u3")
	if third.OK || third.Err != "conflict" {
		t.Fatalf("容量 2 打满后应 conflict: %+v", third)
	}
}

func TestDailyQuotaPerUser(t *testing.T) {
	b := mkBiz(t)
	future := futureISO(3)
	if r := b.BookVenue("venue-badminton", future, "08:00-10:00", "", "u1"); !r.OK {
		t.Fatal(r)
	}
	if r := b.BookVenue("venue-badminton", future, "10:00-12:00", "", "u1"); !r.OK {
		t.Fatal(r)
	}
	third := b.BookVenue("venue-badminton", future, "14:00-16:00", "", "u1")
	if third.OK || third.Err != "quota" {
		t.Fatalf("每人每天最多 2 个时段: %+v", third)
	}
}

func TestPastDateRejected(t *testing.T) {
	b := mkBiz(t)
	past := dates.Today().AddDate(0, 0, -1).Format("2006-01-02")
	r := b.BookVenue("venue-badminton", past, "08:00-10:00", "", "u1")
	if r.OK || r.Err != "invalid" {
		t.Fatalf("过去日期应 invalid: %+v", r)
	}
}

func TestUnknownVenueAndSlot(t *testing.T) {
	b := mkBiz(t)
	if r := b.BookVenue("venue-none", futureISO(1), "08:00-10:00", "", "u"); r.OK || r.Message != "场馆不存在" {
		t.Fatalf("场馆不存在: %+v", r)
	}
	if r := b.BookVenue("venue-room301", futureISO(1), "09:00-11:00", "", "u"); r.OK || r.Field != "slot" {
		t.Fatalf("时段不合法: %+v", r)
	}
}

func TestLeaveApproverLevels(t *testing.T) {
	b := mkBiz(t)
	start := futureISO(10)
	if r := b.SubmitLeave("u1", "事假", start, futureISO(11), "家事"); r.Approver != "辅导员" {
		t.Errorf("2 天应辅导员: %+v", r)
	}
	if r := b.SubmitLeave("u2", "事假", start, futureISO(14), "家事"); r.Approver != "学院" {
		t.Errorf("5 天应学院: %+v", r)
	}
	if r := b.SubmitLeave("u3", "事假", start, futureISO(19), "家事"); r.Approver != "教务处" {
		t.Errorf("10 天应教务处: %+v", r)
	}
}

func TestLeaveEndBeforeStartRejected(t *testing.T) {
	b := mkBiz(t)
	r := b.SubmitLeave("u", "事假", futureISO(5), futureISO(4), "x")
	if r.OK || r.Field != "end_date" {
		t.Fatalf("end<start 应 invalid: %+v", r)
	}
}

func TestCancelOnlyByOwner(t *testing.T) {
	b := mkBiz(t)
	booked := b.BookVenue("venue-room301", futureISO(1), "08:00-10:00", "", "alice")
	denied := b.CancelBooking(booked.Receipt, "bob")
	if denied.OK || denied.Err != "permission" {
		t.Fatalf("非本人应 permission: %+v", denied)
	}
	if r := b.CancelBooking(booked.Receipt, "alice"); !r.OK {
		t.Fatal(r)
	}
	if r := b.CancelBooking(booked.Receipt, "alice"); r.OK || r.Err != "not_found" {
		t.Fatalf("已取消应 not_found: %+v", r)
	}
}

func TestLeaveStatusPermissionAndPending(t *testing.T) {
	b := mkBiz(t)
	sub := b.SubmitLeave("alice", "事假", futureISO(1), futureISO(2), "家事")
	if !sub.OK {
		t.Fatal(sub)
	}
	if r := b.LeaveStatus(sub.Receipt, "bob"); r.OK || r.Err != "permission" {
		t.Fatalf("非本人查询应 permission: %+v", r)
	}
	if r := b.LeaveStatus(sub.Receipt, "alice"); !r.OK {
		t.Fatalf("本人查询: %+v", r)
	}
	pending, err := b.PendingLeaves()
	if err != nil || len(pending) != 1 || pending[0].Approver != "辅导员" {
		t.Fatalf("pending = %v, %v", pending, err)
	}
	if r := b.ApproveLeave(sub.Receipt); !r.OK {
		t.Fatal(r)
	}
	if r := b.ApproveLeave(sub.Receipt); r.OK || r.Message != "该请假单已处理" {
		t.Fatalf("重复批准应 invalid: %+v", r)
	}
}

func TestResetKeepsVenues(t *testing.T) {
	b := mkBiz(t)
	if r := b.BookVenue("venue-room301", futureISO(1), "08:00-10:00", "", "u"); !r.OK {
		t.Fatal(r)
	}
	if err := b.Reset(); err != nil {
		t.Fatal(err)
	}
	bookings, _ := b.AllBookings()
	tickets, _ := b.AllTickets()
	if len(bookings) != 0 || len(tickets) != 0 {
		t.Fatalf("reset 后应清空: %v %v", bookings, tickets)
	}
	venues, _ := b.ListVenues()
	if len(venues) != 4 {
		t.Fatalf("场馆应保留: %v", venues)
	}
}

func TestNumPart(t *testing.T) {
	cases := map[string]int64{"VE-0003": 3, "LV-0012": 12, "abc": -1, "VE-0-1": 1}
	for in, want := range cases {
		if got := numPart(in); got != want {
			t.Errorf("numPart(%q) = %d, want %d", in, got, want)
		}
	}
}

func TestLeaveDays(t *testing.T) {
	if LeaveDays("2026-09-01", "2026-09-01") != 1 {
		t.Error("同日应为 1 天")
	}
	if LeaveDays("2026-09-01", "2026-09-10") != 10 {
		t.Error("跨 10 天")
	}
	if LeaveDays("2026-09-02", "2026-09-01") != -1 {
		t.Error("倒序应为 -1")
	}
	if LeaveDays("bad", "2026-09-01") != -1 {
		t.Error("非法日期应为 -1")
	}
}

func TestCreatedAtCNTimezone(t *testing.T) {
	got := nowCNISO()
	// 形态校验：ISO 秒精度 + +08:00 偏移
	if len(got) != 25 || got[19] != '+' || got[20:25] != "08:00" {
		t.Errorf("created_at 形态 = %q", got)
	}
	if _, err := time.Parse("2006-01-02T15:04:05-07:00", got); err != nil {
		t.Errorf("created_at 不可解析: %v", err)
	}
}
