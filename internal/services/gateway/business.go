package gateway

import (
	"net/http"

	"github.com/gin-gonic/gin"

	toolv1 "gewu/pkg/gen/gewu/tool/v1"
)

// ---------- /api/business/*（PARITY §2.5，评测断言依赖） ----------

// businessReset 清空 mock 业务数据（POST → {"status":"ok"}）。
func (s *Server) businessReset(c *gin.Context) {
	if _, err := s.tool.Reset(c.Request.Context(), &toolv1.ResetRequest{}); err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"detail": "tool 不可达"})
		return
	}
	c.JSON(http.StatusOK, gin.H{"status": "ok"})
}

// businessOverview 当前预约与请假单（GET → {"bookings":[…],"tickets":[…]}）。
func (s *Server) businessOverview(c *gin.Context) {
	resp, err := s.tool.Overview(c.Request.Context(), &toolv1.OverviewRequest{})
	if err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"detail": "tool 不可达"})
		return
	}
	type bookingJSON struct {
		BookingID string `json:"booking_id"`
		Venue     string `json:"venue"`
		Date      string `json:"date"`
		Slot      string `json:"slot"`
		User      string `json:"user"`
	}
	type ticketJSON struct {
		Ticket    string `json:"ticket"`
		User      string `json:"user"`
		LeaveType string `json:"leave_type"`
		Start     string `json:"start"`
		End       string `json:"end"`
		Days      int32  `json:"days"`
		Approver  string `json:"approver"`
		Status    string `json:"status"`
	}
	bookings := make([]bookingJSON, 0, len(resp.GetBookings()))
	for _, b := range resp.GetBookings() {
		bookings = append(bookings, bookingJSON{
			BookingID: b.GetBookingId(), Venue: b.GetVenue(), Date: b.GetDate(),
			Slot: b.GetSlot(), User: b.GetUser(),
		})
	}
	tickets := make([]ticketJSON, 0, len(resp.GetTickets()))
	for _, t := range resp.GetTickets() {
		tickets = append(tickets, ticketJSON{
			Ticket: t.GetTicket(), User: t.GetUser(), LeaveType: t.GetLeaveType(),
			Start: t.GetStart(), End: t.GetEnd(), Days: t.GetDays(),
			Approver: t.GetApprover(), Status: t.GetStatus(),
		})
	}
	c.JSON(http.StatusOK, gin.H{"bookings": bookings, "tickets": tickets})
}
