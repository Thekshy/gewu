package tool

import (
	"fmt"
	"strings"

	"gewu/internal/business"
	"gewu/internal/dates"
)

// 工具注册中心：权限矩阵（PARITY §10 逐字）+ 各工具的执行与消息格式化
// （格式化从冻结 internal/agent/tools.go 迁移至此——工具的完整出口）。

// bizStore 业务库接口：pgBusiness（PG）与冻结 internal/business（SQLite，
// 测试/对照用）方法集完全一致——工具逻辑与存储解耦。
type bizStore interface {
	ListVenues() ([]business.Venue, error)
	VenueByName(text string) (business.Venue, bool, error)
	Remaining(venueID, date string) (map[string]int, error)
	BookVenue(venueID, date, slot, purpose, user string) business.Result
	CancelBooking(bookingID, user string) business.Result
	MyBookings(user string) ([]business.BookingView, error)
	SubmitLeave(user, leaveType, start, end, reason string) business.Result
	LeaveStatus(ticketID, user string) business.Result
	PendingLeaves() ([]business.TicketView, error)
	ApproveLeave(ticketID string) business.Result
	AllBookings() ([]business.BookingFull, error)
	AllTickets() ([]business.TicketView, error)
	Reset() error
}

// Tool 注册条目。
type Tool struct {
	Name        string
	Label       string
	Description string
	Roles       []string // 可执行角色
	ReadOnly    bool
	Fn          func(b bizStore, args map[string]string, user string) business.Result
}

// toolOrder 工具表遍历顺序（与 Python TOOLS dict 插入序一致，影响给 LLM 的清单顺序）。
var toolOrder = []string{
	"query_venues", "my_bookings", "leave_status", "pending_leaves",
	"book_venue", "cancel_booking", "submit_leave", "approve_leave",
}

var tools = map[string]Tool{
	"query_venues": {Name: "query_venues", Label: "查询场馆", Description: "查询某天可预约的场馆与余量",
		Roles: []string{"student", "counselor"}, ReadOnly: true,
		Fn: func(b bizStore, args map[string]string, user string) business.Result {
			date := args["date"]
			if date == "" {
				date = dates.TodayISO()
			}
			venues, err := b.ListVenues()
			if err != nil {
				return business.Result{Err: "internal", Message: err.Error()}
			}
			var lines []string
			for _, v := range venues {
				rem, err := b.Remaining(v.VenueID, date)
				if err != nil {
					return business.Result{Err: "internal", Message: err.Error()}
				}
				var open []string
				for _, sl := range business.Slots {
					if rem[sl] > 0 {
						open = append(open, sl)
					}
				}
				openText := strings.Join(open, "、")
				if openText == "" {
					openText = "（今日已约满）"
				}
				lines = append(lines, fmt.Sprintf("- %s（%s，每时段 %d 组）：%s", v.Name, v.Kind, v.Capacity, openText))
			}
			return business.Result{OK: true, Message: fmt.Sprintf("%s 可预约场馆：\n%s", date, strings.Join(lines, "\n"))}
		}},
	"my_bookings": {Name: "my_bookings", Label: "我的预约", Description: "查询本人当前有效预约",
		Roles: []string{"student", "counselor"}, ReadOnly: true,
		Fn: func(b bizStore, args map[string]string, user string) business.Result {
			items, err := b.MyBookings(user)
			if err != nil {
				return business.Result{Err: "internal", Message: err.Error()}
			}
			if len(items) == 0 {
				return business.Result{OK: true, Message: "你目前没有有效预约。"}
			}
			lines := make([]string, 0, len(items))
			for _, bk := range items {
				lines = append(lines, fmt.Sprintf("- %s：%s %s %s", bk.BookingID, bk.Venue, bk.Date, bk.Slot))
			}
			return business.Result{OK: true, Message: "你的有效预约：\n" + strings.Join(lines, "\n")}
		}},
	"leave_status": {Name: "leave_status", Label: "请假单查询", Description: "按请假单号查询审批状态",
		Roles: []string{"student", "counselor"}, ReadOnly: true,
		Fn: func(b bizStore, args map[string]string, user string) business.Result {
			return b.LeaveStatus(args["ticket_id"], user)
		}},
	"pending_leaves": {Name: "pending_leaves", Label: "待审批请假", Description: "查看所有待审批请假申请",
		Roles: []string{"counselor"}, ReadOnly: true,
		Fn: func(b bizStore, args map[string]string, user string) business.Result {
			items, err := b.PendingLeaves()
			if err != nil {
				return business.Result{Err: "internal", Message: err.Error()}
			}
			if len(items) == 0 {
				return business.Result{OK: true, Message: "当前没有待审批的请假申请。"}
			}
			lines := make([]string, 0, len(items))
			for _, t := range items {
				lines = append(lines, fmt.Sprintf("- %s：%s %s %s~%s（%d 天，%s审批）",
					t.Ticket, t.User, t.LeaveType, t.Start, t.End, t.Days, t.Approver))
			}
			return business.Result{OK: true, Message: "待审批请假申请：\n" + strings.Join(lines, "\n")}
		}},
	"book_venue": {Name: "book_venue", Label: "预约场馆", Description: "预约场馆的某个时段（写操作，需确认）",
		Roles: []string{"student", "counselor"}, ReadOnly: false,
		Fn: func(b bizStore, args map[string]string, user string) business.Result {
			venue, bad := need(args, "venue")
			if bad != nil {
				return *bad
			}
			date, bad := need(args, "date")
			if bad != nil {
				return *bad
			}
			slot, bad := need(args, "slot")
			if bad != nil {
				return *bad
			}
			return b.BookVenue(venue, date, slot, args["purpose"], user)
		}},
	"cancel_booking": {Name: "cancel_booking", Label: "取消预约", Description: "取消本人的预约（写操作，需确认）",
		Roles: []string{"student", "counselor"}, ReadOnly: false,
		Fn: func(b bizStore, args map[string]string, user string) business.Result {
			id, bad := need(args, "booking_id")
			if bad != nil {
				return *bad
			}
			return b.CancelBooking(id, user)
		}},
	"submit_leave": {Name: "submit_leave", Label: "请假申请", Description: "提交请假申请（写操作，需确认）",
		Roles: []string{"student", "counselor"}, ReadOnly: false,
		Fn: func(b bizStore, args map[string]string, user string) business.Result {
			lt, bad := need(args, "leave_type")
			if bad != nil {
				return *bad
			}
			start, bad := need(args, "start_date")
			if bad != nil {
				return *bad
			}
			end, bad := need(args, "end_date")
			if bad != nil {
				return *bad
			}
			reason, bad := need(args, "reason")
			if bad != nil {
				return *bad
			}
			return b.SubmitLeave(user, lt, start, end, reason)
		}},
	"approve_leave": {Name: "approve_leave", Label: "批准请假", Description: "批准一张请假单（写操作，需确认，仅辅导员）",
		Roles: []string{"counselor"}, ReadOnly: false,
		Fn: func(b bizStore, args map[string]string, user string) business.Result {
			id, bad := need(args, "ticket_id")
			if bad != nil {
				return *bad
			}
			return b.ApproveLeave(id)
		}},
}

// need 取必填参数，缺失时返回 missing_arg（与 Python KeyError 分支一致）。
func need(args map[string]string, key string) (string, *business.Result) {
	v, ok := args[key]
	if !ok {
		r := business.Result{Err: "missing_arg", Message: "缺少参数：" + key}
		return "", &r
	}
	return v, nil
}

// roleLabel 角色中文名（越权提示文案用）。
func roleLabel(role string) string {
	if role == "student" {
		return "学生"
	}
	return "辅导员"
}

func hasRole(roles []string, role string) bool {
	for _, r := range roles {
		if r == role {
			return true
		}
	}
	return false
}

// callTool 工具层单一出口：未知工具 / 越权 / 缺参数在进入业务系统前拦截
// （文案逐字，PARITY §10）。
func callTool(b bizStore, name string, args map[string]string, role, user string) business.Result {
	t, ok := tools[name]
	if !ok {
		return business.Result{Err: "unknown_tool", Message: "未知工具：" + name}
	}
	if !hasRole(t.Roles, role) {
		return business.Result{Err: "permission",
			Message: fmt.Sprintf("当前身份（%s）无权执行「%s」", roleLabel(role), t.Label)}
	}
	return t.Fn(b, args, user)
}

// toolDescriptions 生成给 LLM 的工具清单（只含该角色可见的工具）。
func toolDescriptions(role string) string {
	var lines []string
	for _, name := range toolOrder {
		t := tools[name]
		if hasRole(t.Roles, role) {
			lines = append(lines, fmt.Sprintf("- %s：%s", t.Name, t.Description))
		}
	}
	return strings.Join(lines, "\n")
}
