// Package business 实现 mock 校内业务系统：场馆预约与请假审批。
//
// 真实学校里这是独立的业务后端；本项目内用同进程模块模拟，agent 只能通过
// agent 包工具层访问它，模块边界与生产架构一致。业务规则与语料保持一致
// （data/corpus/0010-leave.md）：请假 1—3 天辅导员批、3 天以上 7 天以内学院批、
// 超过 7 天教务处批。所有 SQL 均为静态字面量 + 参数绑定。
package business

import (
	"database/sql"
	"fmt"
	"regexp"
	"strconv"
	"strings"
	"time"

	"gewu/internal/dates"

	_ "modernc.org/sqlite" // 纯 Go SQLite 驱动，免 CGO
)

// Slots 全部可预约时段。
var Slots = []string{"08:00-10:00", "10:00-12:00", "14:00-16:00", "16:00-18:00", "19:00-21:00"}

var slotsSet = func() map[string]bool {
	m := map[string]bool{}
	for _, s := range Slots {
		m[s] = true
	}
	return m
}()

// Venue 场馆。
type Venue struct {
	VenueID  string `json:"venue_id"`
	Name     string `json:"name"`
	Kind     string `json:"kind"`
	Capacity int    `json:"capacity"`
}

// BookingView 本人有效预约视图。
type BookingView struct {
	BookingID string `json:"booking_id"`
	Venue     string `json:"venue"`
	Date      string `json:"date"`
	Slot      string `json:"slot"`
	Purpose   string `json:"purpose"`
}

// BookingFull 调试视图（含 user）。
type BookingFull struct {
	BookingID string `json:"booking_id"`
	Venue     string `json:"venue"`
	Date      string `json:"date"`
	Slot      string `json:"slot"`
	User      string `json:"user"`
}

// TicketView 请假单视图。
type TicketView struct {
	Ticket    string `json:"ticket"`
	User      string `json:"user"`
	LeaveType string `json:"leave_type"`
	Start     string `json:"start"`
	End       string `json:"end"`
	Days      int    `json:"days"`
	Approver  string `json:"approver"`
	Status    string `json:"status"` // 调试视图用；pending 列表不含
}

// Result 业务操作结果：ok/error/field/message/alternatives/receipt 的统一形态，
// 与工具层（agent）约定的字段一一对应（PARITY §8）。
type Result struct {
	OK           bool
	Err          string // "" | invalid | quota | conflict | not_found | permission
	Field        string // 字段级失败（恢复流程据此重新追问）
	Message      string
	Receipt      string   // VE-XXXX / LV-XXXX
	Alternatives []string // 冲突时可选时段
	Days         int      // submit_leave 成功时的天数
	Approver     string   // submit_leave 成功时的审批层级
}

func okMsg(message string) Result { return Result{OK: true, Message: message} }

func fail(errCode, field, message string) Result {
	return Result{Err: errCode, Field: field, Message: message}
}

// ApproverOf 按请假天数映射审批层级：≤3 辅导员；≤7 学院；>7 教务处。
func ApproverOf(days int) string {
	switch {
	case days <= 3:
		return "辅导员"
	case days <= 7:
		return "学院"
	default:
		return "教务处"
	}
}

// Business 业务系统（SQLite 持久化）。
type Business struct {
	db *db
}

// Open 打开（或创建）业务库并播种场馆。
func Open(path string) (*Business, error) {
	handle, err := openDB(path)
	if err != nil {
		return nil, err
	}
	b := &Business{db: handle}
	if err := b.seed(); err != nil {
		handle.Close()
		return nil, err
	}
	return b, nil
}

// Close 关闭底层连接。
func (b *Business) Close() error { return b.db.Close() }

func (b *Business) seed() error {
	var n int
	if err := b.db.queryRow("SELECT COUNT(*) FROM venues").Scan(&n); err != nil {
		return err
	}
	if n > 0 {
		return nil
	}
	seed := []struct {
		id, name, kind string
		cap            int
	}{
		{"venue-badminton", "羽毛球馆", "体育场馆", 2},
		{"venue-basketball", "篮球场", "体育场馆", 1},
		{"venue-room301", "研讨间301", "图书馆研讨间", 1},
		{"venue-room302", "研讨间302", "图书馆研讨间", 1},
	}
	tx, err := b.db.begin()
	if err != nil {
		return err
	}
	defer tx.rollback()
	for _, v := range seed {
		if _, err := tx.exec("INSERT INTO venues (id, name, kind, capacity) VALUES (?, ?, ?, ?)", v.id, v.name, v.kind, v.cap); err != nil {
			return err
		}
	}
	return tx.commit()
}

// ---------- 场馆 ----------

// ListVenues 全部场馆（按 id 升序）。
func (b *Business) ListVenues() ([]Venue, error) {
	rows, err := b.db.query("SELECT id, name, kind, capacity FROM venues ORDER BY id")
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var out []Venue
	for rows.Next() {
		var v Venue
		if err := rows.Scan(&v.VenueID, &v.Name, &v.Kind, &v.Capacity); err != nil {
			return nil, err
		}
		out = append(out, v)
	}
	return out, rows.Err()
}

// VenueByName 按名称子串匹配场馆（agent 侧解析用户口语用）。
func (b *Business) VenueByName(text string) (Venue, bool, error) {
	venues, err := b.ListVenues()
	if err != nil {
		return Venue{}, false, err
	}
	for _, v := range venues {
		if strings.Contains(text, v.Name) {
			return v, true, nil
		}
	}
	return Venue{}, false, nil
}

// Remaining 场馆某日各时段余量；场馆不存在返回空 map。
func (b *Business) Remaining(venueID, date string) (map[string]int, error) {
	var capacity int
	err := b.db.queryRow("SELECT capacity FROM venues WHERE id = ?", venueID).Scan(&capacity)
	if err == sql.ErrNoRows {
		return map[string]int{}, nil
	}
	if err != nil {
		return nil, err
	}
	out := map[string]int{}
	for _, s := range Slots {
		out[s] = capacity
	}
	rows, err := b.db.query(
		"SELECT slot, COUNT(*) FROM bookings WHERE venue_id = ? AND date = ? AND status = '有效' GROUP BY slot",
		venueID, date,
	)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	for rows.Next() {
		var slot string
		var used int
		if err := rows.Scan(&slot, &used); err != nil {
			return nil, err
		}
		if left := capacity - used; left > 0 {
			out[slot] = left
		} else {
			out[slot] = 0
		}
	}
	return out, rows.Err()
}

// BookVenue 预约场馆（校验顺序与返回值见 PARITY §8.1）。
func (b *Business) BookVenue(venueID, date, slot, purpose, user string) Result {
	var name string
	err := b.db.queryRow("SELECT name FROM venues WHERE id = ?", venueID).Scan(&name)
	if err == sql.ErrNoRows {
		return fail("invalid", "", "场馆不存在")
	}
	if err != nil {
		return fail("internal", "", err.Error())
	}
	if !slotsSet[slot] {
		return fail("invalid", "slot", "时段不合法")
	}
	if date < dates.TodayISO() {
		return fail("invalid", "date", "不能预约过去的日期")
	}
	var perDay int
	if err := b.db.queryRow(
		"SELECT COUNT(*) FROM bookings WHERE user = ? AND date = ? AND status = '有效'", user, date,
	).Scan(&perDay); err != nil {
		return fail("internal", "", err.Error())
	}
	if perDay >= 2 {
		return fail("quota", "", "每人每天最多预约 2 个时段")
	}
	rem, err := b.Remaining(venueID, date)
	if err != nil {
		return fail("internal", "", err.Error())
	}
	if rem[slot] <= 0 {
		var alts []string
		for _, s := range Slots { // 固定时段序，输出稳定
			if rem[s] > 0 {
				alts = append(alts, s)
			}
		}
		return Result{
			Err: "conflict", Field: "slot",
			Message:      fmt.Sprintf("%s %s 的 %s 已约满", name, date, slot),
			Alternatives: alts,
		}
	}
	tx, err := b.db.begin()
	if err != nil {
		return fail("internal", "", err.Error())
	}
	defer tx.rollback()
	res, err := tx.exec(
		"INSERT INTO bookings (venue_id, date, slot, purpose, user, created_at) VALUES (?, ?, ?, ?, ?, ?)",
		venueID, date, slot, purpose, user, nowCNISO(),
	)
	if err != nil {
		return fail("internal", "", err.Error())
	}
	id, err := res.LastInsertId()
	if err != nil {
		return fail("internal", "", err.Error())
	}
	if err := tx.commit(); err != nil {
		return fail("internal", "", err.Error())
	}
	return Result{OK: true, Receipt: receiptID("VE", id), Message: fmt.Sprintf("已预约 %s %s %s", name, date, slot)}
}

// CancelBooking 取消预约（仅本人、仅有效状态）。
func (b *Business) CancelBooking(bookingID, user string) Result {
	var id int64
	var owner, status string
	err := b.db.queryRow("SELECT id, user, status FROM bookings WHERE id = ?", numPart(bookingID)).
		Scan(&id, &owner, &status)
	if err == sql.ErrNoRows || (err == nil && status != "有效") {
		return fail("not_found", "", "预约记录不存在或已取消")
	}
	if err != nil {
		return fail("internal", "", err.Error())
	}
	if owner != user {
		return fail("permission", "", "只能取消本人的预约")
	}
	tx, err := b.db.begin()
	if err != nil {
		return fail("internal", "", err.Error())
	}
	defer tx.rollback()
	if _, err := tx.exec("UPDATE bookings SET status = '已取消' WHERE id = ?", id); err != nil {
		return fail("internal", "", err.Error())
	}
	if err := tx.commit(); err != nil {
		return fail("internal", "", err.Error())
	}
	return okMsg(fmt.Sprintf("预约 %s 已取消", bookingID))
}

// MyBookings 本人当前有效预约（按 date, slot 排序）。
func (b *Business) MyBookings(user string) ([]BookingView, error) {
	rows, err := b.db.query(
		"SELECT b.id, v.name, b.date, b.slot, b.purpose FROM bookings b JOIN venues v ON v.id = b.venue_id "+
			"WHERE b.user = ? AND b.status = '有效' ORDER BY b.date, b.slot", user,
	)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var out []BookingView
	for rows.Next() {
		var v BookingView
		var id int64
		if err := rows.Scan(&id, &v.Venue, &v.Date, &v.Slot, &v.Purpose); err != nil {
			return nil, err
		}
		v.BookingID = receiptID("VE", id)
		out = append(out, v)
	}
	return out, rows.Err()
}

// ---------- 请假 ----------

// LeaveDays 计算请假天数（含首尾）；end<start 或日期非法返回 -1。
func LeaveDays(start, end string) int {
	s, err1 := time.ParseInLocation("2006-01-02", start, dates.CNtz)
	e, err2 := time.ParseInLocation("2006-01-02", end, dates.CNtz)
	if err1 != nil || err2 != nil {
		return -1
	}
	days := int(e.Sub(s).Hours()/24) + 1
	if e.Before(s) {
		return -1
	}
	return days
}

// SubmitLeave 提交请假申请。
func (b *Business) SubmitLeave(user, leaveType, start, end, reason string) Result {
	days := LeaveDays(start, end)
	if days < 1 {
		return fail("invalid", "end_date", "结束日期不能早于开始日期")
	}
	if start < dates.TodayISO() {
		return fail("invalid", "start_date", "开始日期不能是过去")
	}
	tx, err := b.db.begin()
	if err != nil {
		return fail("internal", "", err.Error())
	}
	defer tx.rollback()
	res, err := tx.exec(
		"INSERT INTO leave_tickets (user, leave_type, start_date, end_date, days, reason, approver_level, created_at) "+
			"VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
		user, leaveType, start, end, days, reason, ApproverOf(days), nowCNISO(),
	)
	if err != nil {
		return fail("internal", "", err.Error())
	}
	id, err := res.LastInsertId()
	if err != nil {
		return fail("internal", "", err.Error())
	}
	if err := tx.commit(); err != nil {
		return fail("internal", "", err.Error())
	}
	return Result{
		OK: true, Receipt: receiptID("LV", id), Days: days, Approver: ApproverOf(days),
		Message: fmt.Sprintf("请假申请已提交（%d 天），按学校规定将由%s审批", days, ApproverOf(days)),
	}
}

// LeaveStatus 按单号查询本人请假单。
func (b *Business) LeaveStatus(ticketID, user string) Result {
	var id int64
	var owner, leaveType, start, end, approver, status string
	var days int
	err := b.db.queryRow(
		"SELECT id, user, leave_type, start_date, end_date, days, approver_level, status FROM leave_tickets WHERE id = ?",
		numPart(ticketID),
	).Scan(&id, &owner, &leaveType, &start, &end, &days, &approver, &status)
	if err == sql.ErrNoRows {
		return fail("not_found", "", "请假单不存在")
	}
	if err != nil {
		return fail("internal", "", err.Error())
	}
	if owner != user {
		return fail("permission", "", "只能查询本人的请假单")
	}
	return Result{
		OK: true, Days: days, Approver: approver, Receipt: receiptID("LV", id),
		Message: fmt.Sprintf("%s %s %s~%s（%d 天，%s审批，%s）", receiptID("LV", id), leaveType, start, end, days, approver, status),
	}
}

// PendingLeaves 全部待审批请假单（按 id 升序）。
func (b *Business) PendingLeaves() ([]TicketView, error) {
	rows, err := b.db.query(
		"SELECT id, user, leave_type, start_date, end_date, days, approver_level FROM leave_tickets WHERE status = '待审批' ORDER BY id",
	)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var out []TicketView
	for rows.Next() {
		var t TicketView
		var id int64
		if err := rows.Scan(&id, &t.User, &t.LeaveType, &t.Start, &t.End, &t.Days, &t.Approver); err != nil {
			return nil, err
		}
		t.Ticket = receiptID("LV", id)
		out = append(out, t)
	}
	return out, rows.Err()
}

// ApproveLeave 批准请假单。
func (b *Business) ApproveLeave(ticketID string) Result {
	var id int64
	var status string
	err := b.db.queryRow("SELECT id, status FROM leave_tickets WHERE id = ?", numPart(ticketID)).Scan(&id, &status)
	if err == sql.ErrNoRows {
		return fail("not_found", "", "请假单不存在")
	}
	if err != nil {
		return fail("internal", "", err.Error())
	}
	if status != "待审批" {
		return fail("invalid", "", "该请假单已处理")
	}
	tx, err := b.db.begin()
	if err != nil {
		return fail("internal", "", err.Error())
	}
	defer tx.rollback()
	if _, err := tx.exec("UPDATE leave_tickets SET status = '已通过' WHERE id = ?", id); err != nil {
		return fail("internal", "", err.Error())
	}
	if err := tx.commit(); err != nil {
		return fail("internal", "", err.Error())
	}
	return okMsg(fmt.Sprintf("请假单 %s 已通过", ticketID))
}

// ---------- 调试 / 评测 ----------

// AllBookings 全部有效预约（按 id 升序，评测断言用）。
func (b *Business) AllBookings() ([]BookingFull, error) {
	rows, err := b.db.query(
		"SELECT b.id, v.name, b.date, b.slot, b.user FROM bookings b JOIN venues v ON v.id = b.venue_id " +
			"WHERE b.status = '有效' ORDER BY b.id",
	)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var out []BookingFull
	for rows.Next() {
		var v BookingFull
		var id int64
		if err := rows.Scan(&id, &v.Venue, &v.Date, &v.Slot, &v.User); err != nil {
			return nil, err
		}
		v.BookingID = receiptID("VE", id)
		out = append(out, v)
	}
	return out, rows.Err()
}

// AllTickets 全部请假单（按 id 升序，评测断言用）。
func (b *Business) AllTickets() ([]TicketView, error) {
	rows, err := b.db.query(
		"SELECT id, user, leave_type, start_date, end_date, days, approver_level, status FROM leave_tickets ORDER BY id",
	)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var out []TicketView
	for rows.Next() {
		var t TicketView
		var id int64
		if err := rows.Scan(&id, &t.User, &t.LeaveType, &t.Start, &t.End, &t.Days, &t.Approver, &t.Status); err != nil {
			return nil, err
		}
		t.Ticket = receiptID("LV", id)
		out = append(out, t)
	}
	return out, rows.Err()
}

// Reset 清空运行数据（评测与演示用，场馆表保留）。
func (b *Business) Reset() error {
	tx, err := b.db.begin()
	if err != nil {
		return err
	}
	defer tx.rollback()
	if _, err := tx.exec("DELETE FROM bookings"); err != nil {
		return err
	}
	if _, err := tx.exec("DELETE FROM leave_tickets"); err != nil {
		return err
	}
	return tx.commit()
}

// receiptID 生成 VE-0001 / LV-0002 形态的凭证号（4 位零填充）。
func receiptID(prefix string, id int64) string {
	return fmt.Sprintf("%s-%04d", prefix, id)
}

var digitsRe = regexp.MustCompile(`[0-9]+`)

// numPart 从单号文本提取数字：拼接全部数字段（"VE-0003"→3；无数字→-1 查不到）。
func numPart(idText string) int64 {
	var b strings.Builder
	for _, d := range digitsRe.FindAllString(idText, -1) {
		b.WriteString(d)
	}
	if b.Len() == 0 {
		return -1
	}
	n, err := strconv.ParseInt(b.String(), 10, 64)
	if err != nil {
		return -1
	}
	return n
}

// nowCNISO 中国时区当前时间 ISO（秒精度），created_at 专用。
func nowCNISO() string {
	return dates.NowCN().Format("2006-01-02T15:04:05-07:00")
}
