package api

import (
	"net/http"

	"github.com/gin-gonic/gin"

	"gewu/internal/business"
)

func (s *Server) businessReset(c *gin.Context) {
	if err := s.deps.Business.Reset(); err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"detail": err.Error()})
		return
	}
	c.JSON(http.StatusOK, gin.H{"status": "ok"})
}

func (s *Server) businessOverview(c *gin.Context) {
	bookings, err := s.deps.Business.AllBookings()
	if err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"detail": err.Error()})
		return
	}
	tickets, err := s.deps.Business.AllTickets()
	if err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"detail": err.Error()})
		return
	}
	if bookings == nil {
		bookings = []business.BookingFull{}
	}
	if tickets == nil {
		tickets = []business.TicketView{}
	}
	c.JSON(http.StatusOK, gin.H{"bookings": bookings, "tickets": tickets})
}
