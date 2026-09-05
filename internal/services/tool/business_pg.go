package tool

import (
	"database/sql"
	"fmt"
	"regexp"
	"strconv"
	"strings"
	"time"

	_ "github.com/jackc/pgx/v5/stdlib"

	"gewu/internal/business"
	"gewu/internal/dates"
)

// pgBusiness PG 业务库：全部方法与冻结 internal/business 逐字对齐
// （PARITY §8：校验顺序、错误文案、单号 VE-{id:04d}/LV-{id:04d}、
// created_at 中国时区秒精度、种子场馆、reset 语义）。
// 差异仅存储层：SQLite(?)→PG($n)、AUTOINCREMENT→BIGSERIAL（DELETE 均不复位，
// 序列语义一致，见 ADR-0008）。
type pgBusiness struct {
	db *sql.DB
}

// openPGBusiness 打开（或创建）业务库并播种场馆。
func openPGBusiness(dsn string) (*pgBusiness, error) {
	db, err := sql.Open("pgx", dsn)
	if err != nil {
		return nil, err
	}
	if err := db.Ping(); err != nil {
		db.Close()
		return nil, fmt.Errorf("连接 PG 失败: %w", err)
	}
	if _, err := db.Exec(`CREATE TABLE IF NOT EXISTS venues (
		id TEXT PRIMARY KEY,
		name TEXT NOT NULL,
		kind TEXT NOT NULL,
		capacity INTEGER NOT NULL
	)`); err != nil {
		db.Close()
		return nil, fmt.Errorf("建 venues 表失败: %w", err)
	}
	if _, err := db.Exec(`CREATE TABLE IF NOT EXISTS bookings (
		id BIGSERIAL PRIMARY KEY,
		venue_id TEXT NOT NULL,
		date TEXT NOT NULL,
		slot TEXT NOT NULL,
		purpose TEXT NOT NULL DEFAULT '',
		user_name TEXT NOT NULL,
		status TEXT NOT NULL DEFAULT '有效',
		created_at TEXT NOT NULL
	)`); err != nil {
		db.Close()
		return nil, fmt.Errorf("建 bookings 表失败: %w", err)
	}
	if _, err := db.Exec(`CREATE TABLE IF NOT EXISTS leave_tickets (
		id BIGSERIAL PRIMARY KEY,
		user_name TEXT NOT NULL,
		leave_type TEXT NOT NULL,
		start_date TEXT NOT NULL,
		end_date TEXT NOT NULL,
		days INTEGER NOT NULL,
		reason TEXT NOT NULL,
		approver_level TEXT NOT NULL,
		status TEXT NOT NULL DEFAULT '待审批',
		created_at TEXT NOT NULL
	)`); err != nil {
		db.Close()
		return nil, fmt.Errorf("建 leave_tickets 表失败: %w", err)
	}
	b := &pgBusiness{db: db}
	if err := b.seed(); err != nil {
		db.Close()
		return nil, err
	}
	return b, nil
}

// Close 关闭连接。
func (b *pgBusiness) Close() error { return b.db.Close() }

func (b *pgBusiness) seed() error {
	var n int
	if err := b.db.QueryRow(`SELECT COUNT(*) FROM venues`).Scan(&n); err != nil {
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
	tx, err := b.db.Begin()
	if err != nil {
		return err
	}
	defer func() { _ = tx.Rollback() }()
	for _, v := range seed {
		if _, err := tx.Exec(`INSERT INTO venues (id, name, kind, capacity) VALUES ($1, $2, $3, $4)`,
			v.id, v.name, v.kind, v.cap); err != nil {
			return err
		}
	}
	return tx.Commit()
}

// ---------- 场馆 ----------

// ListVenues 全部场馆（按 id 升序）。
func (b *pgBusiness) ListVenues() ([]business.Venue, error) {
	rows, err := b.db.Query(`SELECT id, name, kind, capacity FROM venues ORDER BY id`)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var out []business.Venue
	for rows.Next() {
		var v business.Venue
		if err := rows.Scan(&v.VenueID, &v.Name, &v.Kind, &v.Capacity); err != nil {
			return nil, err
		}
		out = append(out, v)
	}
	return out, rows.Err()
}

// VenueByName 按名称子串匹配场馆。
func (b *pgBusiness) VenueByName(text string) (business.Venue, bool, error) {
	venues, err := b.ListVenues()
	if err != nil {
		return business.Venue{}, false, err
	}
	for _, v := range venues {
		if strings.Contains(text, v.Name) {
			return v, true, nil
		}
	}
	return business.Venue{}, false, nil
}

// Remaining 场馆某日各时段余量；场馆不存在返回空 map。
func (b *pgBusiness) Remaining(venueID, date string) (map[string]int, error) {
	var capacity int
	err := b.db.QueryRow(`SELECT capacity FROM venues WHERE id = $1`, venueID).Scan(&capacity)
	if err == sql.ErrNoRows {
		return map[string]int{}, nil
	}
	if err != nil {
		return nil, err
	}
	out := map[string]int{}
	for _, s := range business.Slots {
		out[s] = capacity
	}
	rows, err := b.db.Query(
		`SELECT slot, COUNT(*) FROM bookings WHERE venue_id = $1 AND date = $2 AND status = '有效' GROUP BY slot`,
		venueID, date)
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

// BookVenue 预约场馆（校验顺序与返回值见 PARITY §8.1，逐字）。
func (b *pgBusiness) BookVenue(venueID, date, slot, purpose, user string) business.Result {
	var name string
	err := b.db.QueryRow(`SELECT name FROM venues WHERE id = $1`, venueID).Scan(&name)
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
	if err := b.db.QueryRow(
		`SELECT COUNT(*) FROM bookings WHERE user_name = $1 AND date = $2 AND status = '有效'`, user, date,
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
		for _, s := range business.Slots { // 固定时段序，输出稳定
			if rem[s] > 0 {
				alts = append(alts, s)
			}
		}
		return business.Result{
			Err: "conflict", Field: "slot",
			Message:      fmt.Sprintf("%s %s 的 %s 已约满", name, date, slot),
			Alternatives: alts,
		}
	}
	var id int64
	err = b.db.QueryRow(`INSERT INTO bookings (venue_id, date, slot, purpose, user_name, created_at)
		VALUES ($1, $2, $3, $4, $5, $6) RETURNING id`,
		venueID, date, slot, purpose, user, nowCNISO()).Scan(&id)
	if err != nil {
		return fail("internal", "", err.Error())
	}
	return business.Result{OK: true, Receipt: receiptID("VE", id), Message: fmt.Sprintf("已预约 %s %s %s", name, date, slot)}
}

// CancelBooking 取消预约（仅本人、仅有效状态）。
func (b *pgBusiness) CancelBooking(bookingID, user string) business.Result {
	var id int64
	var owner, status string
	err := b.db.QueryRow(`SELECT id, user_name, status FROM bookings WHERE id = $1`, numPart(bookingID)).
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
	if _, err := b.db.Exec(`UPDATE bookings SET status = '已取消' WHERE id = $1`, id); err != nil {
		return fail("internal", "", err.Error())
	}
	return okMsg(fmt.Sprintf("预约 %s 已取消", bookingID))
}

// MyBookings 本人当前有效预约（按 date, slot 排序）。
func (b *pgBusiness) MyBookings(user string) ([]business.BookingView, error) {
	rows, err := b.db.Query(
		`SELECT b.id, v.name, b.date, b.slot, b.purpose FROM bookings b JOIN venues v ON v.id = b.venue_id `+
			`WHERE b.user_name = $1 AND b.status = '有效' ORDER BY b.date, b.slot`, user)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var out []business.BookingView
	for rows.Next() {
		var v business.BookingView
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

// SubmitLeave 提交请假申请（校验顺序与文案逐字）。
func (b *pgBusiness) SubmitLeave(user, leaveType, start, end, reason string) business.Result {
	days := business.LeaveDays(start, end)
	if days < 1 {
		return fail("invalid", "end_date", "结束日期不能早于开始日期")
	}
	if start < dates.TodayISO() {
		return fail("invalid", "start_date", "开始日期不能是过去")
	}
	var id int64
	err := b.db.QueryRow(`INSERT INTO leave_tickets
		(user_name, leave_type, start_date, end_date, days, reason, approver_level, created_at)
		VALUES ($1, $2, $3, $4, $5, $6, $7, $8) RETURNING id`,
		user, leaveType, start, end, days, reason, business.ApproverOf(days), nowCNISO()).Scan(&id)
	if err != nil {
		return fail("internal", "", err.Error())
	}
	return business.Result{
		OK: true, Receipt: receiptID("LV", id), Days: days, Approver: business.ApproverOf(days),
		Message: fmt.Sprintf("请假申请已提交（%d 天），按学校规定将由%s审批", days, business.ApproverOf(days)),
	}
}

// LeaveStatus 按单号查询本人请假单。
func (b *pgBusiness) LeaveStatus(ticketID, user string) business.Result {
	var id int64
	var owner, leaveType, start, end, approver, status string
	var days int
	err := b.db.QueryRow(
		`SELECT id, user_name, leave_type, start_date, end_date, days, approver_level, status
		 FROM leave_tickets WHERE id = $1`, numPart(ticketID)).
		Scan(&id, &owner, &leaveType, &start, &end, &days, &approver, &status)
	if err == sql.ErrNoRows {
		return fail("not_found", "", "请假单不存在")
	}
	if err != nil {
		return fail("internal", "", err.Error())
	}
	if owner != user {
		return fail("permission", "", "只能查询本人的请假单")
	}
	return business.Result{
		OK: true, Days: days, Approver: approver, Receipt: receiptID("LV", id),
		Message: fmt.Sprintf("%s %s %s~%s（%d 天，%s审批，%s）", receiptID("LV", id), leaveType, start, end, days, approver, status),
	}
}

// PendingLeaves 全部待审批请假单（按 id 升序）。
func (b *pgBusiness) PendingLeaves() ([]business.TicketView, error) {
	rows, err := b.db.Query(
		`SELECT id, user_name, leave_type, start_date, end_date, days, approver_level
		 FROM leave_tickets WHERE status = '待审批' ORDER BY id`)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var out []business.TicketView
	for rows.Next() {
		var t business.TicketView
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
func (b *pgBusiness) ApproveLeave(ticketID string) business.Result {
	var id int64
	var status string
	err := b.db.QueryRow(`SELECT id, status FROM leave_tickets WHERE id = $1`, numPart(ticketID)).Scan(&id, &status)
	if err == sql.ErrNoRows {
		return fail("not_found", "", "请假单不存在")
	}
	if err != nil {
		return fail("internal", "", err.Error())
	}
	if status != "待审批" {
		return fail("invalid", "", "该请假单已处理")
	}
	if _, err := b.db.Exec(`UPDATE leave_tickets SET status = '已通过' WHERE id = $1`, id); err != nil {
		return fail("internal", "", err.Error())
	}
	return okMsg(fmt.Sprintf("请假单 %s 已通过", ticketID))
}

// ---------- 调试 / 评测 ----------

// AllBookings 全部有效预约（按 id 升序，评测断言用）。
func (b *pgBusiness) AllBookings() ([]business.BookingFull, error) {
	rows, err := b.db.Query(
		`SELECT b.id, v.name, b.date, b.slot, b.user_name FROM bookings b JOIN venues v ON v.id = b.venue_id ` +
			`WHERE b.status = '有效' ORDER BY b.id`)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var out []business.BookingFull
	for rows.Next() {
		var v business.BookingFull
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
func (b *pgBusiness) AllTickets() ([]business.TicketView, error) {
	rows, err := b.db.Query(
		`SELECT id, user_name, leave_type, start_date, end_date, days, approver_level, status
		 FROM leave_tickets ORDER BY id`)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var out []business.TicketView
	for rows.Next() {
		var t business.TicketView
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
func (b *pgBusiness) Reset() error {
	if _, err := b.db.Exec(`DELETE FROM bookings`); err != nil {
		return err
	}
	_, err := b.db.Exec(`DELETE FROM leave_tickets`)
	return err
}

// ---------- 辅助（与冻结实现逐字） ----------

var slotsSet = func() map[string]bool {
	m := map[string]bool{}
	for _, s := range business.Slots {
		m[s] = true
	}
	return m
}()

func okMsg(message string) business.Result { return business.Result{OK: true, Message: message} }

func fail(errCode, field, message string) business.Result {
	return business.Result{Err: errCode, Field: field, Message: message}
}

// receiptID 生成 VE-0001 / LV-0002 形态的凭证号（4 位零填充）。
func receiptID(prefix string, id int64) string {
	return fmt.Sprintf("%s-%04d", prefix, id)
}

var digitsRe = regexp.MustCompile(`[0-9]+`)

// numPart 从单号文本提取数字：拼接全部数字段（"VE-0003"→3；无数字→-1 查不到）。
func numPart(idText string) int64 {
	var sb strings.Builder
	for _, d := range digitsRe.FindAllString(idText, -1) {
		sb.WriteString(d)
	}
	if sb.Len() == 0 {
		return -1
	}
	n, err := strconv.ParseInt(sb.String(), 10, 64)
	if err != nil {
		return -1
	}
	return n
}

// nowCNISO 中国时区当前时间 ISO（秒精度），created_at 专用。
func nowCNISO() string {
	return dates.NowCN().Truncate(time.Second).Format("2006-01-02T15:04:05-07:00")
}
