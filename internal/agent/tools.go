package agent

import (
	"fmt"
	"strings"

	"gewu/internal/business"
	"gewu/internal/dates"
)

// Tool agent 可调用的业务动作，权限矩阵在此执行。
// 业务系统（business 包）不认识角色——权限判定统一收敛在工具层，
// 这是 agent 安全边界的单一出口：越权调用在进入业务系统之前被拒绝。
type Tool struct {
	Name        string
	Label       string
	Description string
	Roles       []string // 可执行角色
	ReadOnly    bool
	Fn          func(args map[string]string, user string) business.Result
}

// 工具实现的辅助：取必填参数，缺失时返回 missing_arg（与 Python KeyError 分支一致）。
func need(args map[string]string, key string) (string, *business.Result) {
	v, ok := args[key]
	if !ok {
		r := business.Result{Err: "missing_arg", Message: "缺少参数：" + key}
		return "", &r
	}
	return v, nil
}

func fmtVenues(d *Deps, args map[string]string, user string) business.Result {
	date := args["date"]
	if date == "" {
		date = dates.TodayISO()
	}
	venues, err := d.Business.ListVenues()
	if err != nil {
		return business.Result{Err: "internal", Message: err.Error()}
	}
	var lines []string
	for _, v := range venues {
		rem, err := d.Business.Remaining(v.VenueID, date)
		if err != nil {
			return business.Result{Err: "internal", Message: err.Error()}
		}
		var open []string
		for _, s := range business.Slots {
			if rem[s] > 0 {
				open = append(open, s)
			}
		}
		openText := strings.Join(open, "、")
		if openText == "" {
			openText = "（今日已约满）"
		}
		lines = append(lines, fmt.Sprintf("- %s（%s，每时段 %d 组）：%s", v.Name, v.Kind, v.Capacity, openText))
	}
	return business.Result{OK: true, Message: fmt.Sprintf("%s 可预约场馆：\n%s", date, strings.Join(lines, "\n"))}
}

func bookTool(d *Deps) func(map[string]string, string) business.Result {
	return func(args map[string]string, user string) business.Result {
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
		return d.Business.BookVenue(venue, date, slot, args["purpose"], user)
	}
}

func cancelTool(d *Deps) func(map[string]string, string) business.Result {
	return func(args map[string]string, user string) business.Result {
		id, bad := need(args, "booking_id")
		if bad != nil {
			return *bad
		}
		return d.Business.CancelBooking(id, user)
	}
}

func leaveTool(d *Deps) func(map[string]string, string) business.Result {
	return func(args map[string]string, user string) business.Result {
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
		return d.Business.SubmitLeave(user, lt, start, end, reason)
	}
}

func leaveStatusTool(d *Deps) func(map[string]string, string) business.Result {
	return func(args map[string]string, user string) business.Result {
		id, bad := need(args, "ticket_id")
		if bad != nil {
			return *bad
		}
		return d.Business.LeaveStatus(id, user)
	}
}

func myBookingsTool(d *Deps) func(map[string]string, string) business.Result {
	return func(args map[string]string, user string) business.Result {
		items, err := d.Business.MyBookings(user)
		if err != nil {
			return business.Result{Err: "internal", Message: err.Error()}
		}
		if len(items) == 0 {
			return business.Result{OK: true, Message: "你目前没有有效预约。"}
		}
		lines := make([]string, 0, len(items))
		for _, b := range items {
			lines = append(lines, fmt.Sprintf("- %s：%s %s %s", b.BookingID, b.Venue, b.Date, b.Slot))
		}
		return business.Result{OK: true, Message: "你的有效预约：\n" + strings.Join(lines, "\n")}
	}
}

func pendingTool(d *Deps) func(map[string]string, string) business.Result {
	return func(args map[string]string, user string) business.Result {
		items, err := d.Business.PendingLeaves()
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
	}
}

func approveTool(d *Deps) func(map[string]string, string) business.Result {
	return func(args map[string]string, user string) business.Result {
		id, bad := need(args, "ticket_id")
		if bad != nil {
			return *bad
		}
		return d.Business.ApproveLeave(id)
	}
}

// toolOrder 工具表遍历顺序（与 Python TOOLS dict 插入序一致，影响给 LLM 的清单顺序）。
var toolOrder = []string{
	"query_venues", "my_bookings", "leave_status", "pending_leaves",
	"book_venue", "cancel_booking", "submit_leave", "approve_leave",
}

// toolsFor 构造工具表（权限矩阵，PARITY §10）。
func toolsFor(d *Deps) map[string]Tool {
	list := []Tool{
		{Name: "query_venues", Label: "查询场馆", Description: "查询某天可预约的场馆与余量",
			Roles: []string{"student", "counselor"}, ReadOnly: true, Fn: func(args map[string]string, user string) business.Result {
				return fmtVenues(d, args, user)
			}},
		{Name: "my_bookings", Label: "我的预约", Description: "查询本人当前有效预约",
			Roles: []string{"student", "counselor"}, ReadOnly: true, Fn: myBookingsTool(d)},
		{Name: "leave_status", Label: "请假单查询", Description: "按请假单号查询审批状态",
			Roles: []string{"student", "counselor"}, ReadOnly: true, Fn: leaveStatusTool(d)},
		{Name: "pending_leaves", Label: "待审批请假", Description: "查看所有待审批请假申请",
			Roles: []string{"counselor"}, ReadOnly: true, Fn: pendingTool(d)},
		{Name: "book_venue", Label: "预约场馆", Description: "预约场馆的某个时段（写操作，需确认）",
			Roles: []string{"student", "counselor"}, ReadOnly: false, Fn: bookTool(d)},
		{Name: "cancel_booking", Label: "取消预约", Description: "取消本人的预约（写操作，需确认）",
			Roles: []string{"student", "counselor"}, ReadOnly: false, Fn: cancelTool(d)},
		{Name: "submit_leave", Label: "请假申请", Description: "提交请假申请（写操作，需确认）",
			Roles: []string{"student", "counselor"}, ReadOnly: false, Fn: leaveTool(d)},
		{Name: "approve_leave", Label: "批准请假", Description: "批准一张请假单（写操作，需确认，仅辅导员）",
			Roles: []string{"counselor"}, ReadOnly: false, Fn: approveTool(d)},
	}
	out := make(map[string]Tool, len(list))
	for _, t := range list {
		out[t.Name] = t
	}
	return out
}

// roleLabel 角色中文名（越权提示文案用）。
func roleLabel(role string) string {
	if role == "student" {
		return "学生"
	}
	return "辅导员"
}

// ToolDescriptions 生成给 LLM 的工具清单（只含该角色可见的工具）。
func (d *Deps) ToolDescriptions(role string) string {
	var lines []string
	for _, name := range toolOrder {
		t := d.Tools[name]
		if hasRole(t.Roles, role) {
			lines = append(lines, fmt.Sprintf("- %s：%s", t.Name, t.Description))
		}
	}
	return strings.Join(lines, "\n")
}

func hasRole(roles []string, role string) bool {
	for _, r := range roles {
		if r == role {
			return true
		}
	}
	return false
}

// CallTool 工具层单一出口：未知工具 / 越权 / 缺参数在进入业务系统前拦截。
func (d *Deps) CallTool(name string, args map[string]string, role, user string) business.Result {
	t, ok := d.Tools[name]
	if !ok {
		return business.Result{Err: "unknown_tool", Message: "未知工具：" + name}
	}
	if !hasRole(t.Roles, role) {
		return business.Result{Err: "permission",
			Message: fmt.Sprintf("当前身份（%s）无权执行「%s」", roleLabel(role), t.Label)}
	}
	return t.Fn(args, user)
}
