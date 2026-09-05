package tool

import (
	"context"

	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/metadata"
	"google.golang.org/grpc/status"

	toolv1 "gewu/pkg/gen/gewu/tool/v1"

	"gewu/internal/business"

	"go.uber.org/zap"
)

// Server 工具服务：CallTool 单一出口（权限矩阵 + user 服务端注入）。
type Server struct {
	toolv1.UnimplementedToolServiceServer
	log *zap.Logger
	biz bizStore
}

// NewServer 构造工具服务（PG 业务库）。
func NewServer(cfg Config, log *zap.Logger) (*Server, error) {
	if cfg.PostgresDSN == "" {
		return nil, status.Error(codes.FailedPrecondition, "tool 服务需要 POSTGRES_DSN（业务库存储）")
	}
	biz, err := openPGBusiness(cfg.PostgresDSN)
	if err != nil {
		return nil, err
	}
	return &Server{log: log, biz: biz}, nil
}

// NewServerWithStore 注入业务库构造（测试用冻结 SQLite business）。
func NewServerWithStore(store bizStore, log *zap.Logger) *Server {
	return &Server{log: log, biz: store}
}

// Close 释放资源（PG 存储才有连接需要关；冻结 SQLite 由调用方管理）。
func (s *Server) Close() {
	if c, ok := s.biz.(interface{ Close() error }); ok {
		_ = c.Close()
	}
}

// userFromMD 从 metadata 取服务端注入的身份（x-user；ADR-0006：
// 模型与客户端不可传，由 gateway/orchestrator 从 role 派生）。
func userFromMD(ctx context.Context) string {
	if md, ok := metadata.FromIncomingContext(ctx); ok {
		if vals := md.Get("x-user"); len(vals) > 0 && vals[0] != "" {
			return vals[0]
		}
	}
	return ""
}

func toProtoResult(r business.Result) *toolv1.CallToolResponse {
	return &toolv1.CallToolResponse{
		Ok:           r.OK,
		Error:        r.Err,
		Field:        r.Field,
		Message:      r.Message,
		Receipt:      r.Receipt,
		Alternatives: r.Alternatives,
		Days:         int32(r.Days),
		Approver:     r.Approver,
	}
}

// CallTool 工具执行单一出口。
func (s *Server) CallTool(ctx context.Context, req *toolv1.CallToolRequest) (*toolv1.CallToolResponse, error) {
	user := userFromMD(ctx)
	if user == "" {
		return nil, status.Error(codes.InvalidArgument, "缺少身份上下文（x-user）")
	}
	args := req.GetArgs()
	if args == nil {
		args = map[string]string{}
	}
	return toProtoResult(callTool(s.biz, req.GetTool(), args, req.GetRole(), user)), nil
}

// ToolDescriptions 该角色可见工具清单（给 LLM 选工具用）。
func (s *Server) ToolDescriptions(ctx context.Context, req *toolv1.ToolDescriptionsRequest) (*toolv1.ToolDescriptionsResponse, error) {
	return &toolv1.ToolDescriptionsResponse{Descriptions: toolDescriptions(req.GetRole())}, nil
}

// ListVenues 全部场馆。
func (s *Server) ListVenues(ctx context.Context, req *toolv1.ListVenuesRequest) (*toolv1.ListVenuesResponse, error) {
	venues, err := s.biz.ListVenues()
	if err != nil {
		return nil, internalStatus(err)
	}
	out := make([]*toolv1.Venue, 0, len(venues))
	for _, v := range venues {
		out = append(out, &toolv1.Venue{VenueId: v.VenueID, Name: v.Name, Kind: v.Kind, Capacity: int32(v.Capacity)})
	}
	return &toolv1.ListVenuesResponse{Venues: out}, nil
}

// VenueByName 名称子串匹配（编排侧槽位解析用）。
func (s *Server) VenueByName(ctx context.Context, req *toolv1.VenueByNameRequest) (*toolv1.VenueByNameResponse, error) {
	v, found, err := s.biz.VenueByName(req.GetText())
	if err != nil {
		return nil, internalStatus(err)
	}
	resp := &toolv1.VenueByNameResponse{Found: found}
	if found {
		resp.Venue = &toolv1.Venue{VenueId: v.VenueID, Name: v.Name, Kind: v.Kind, Capacity: int32(v.Capacity)}
	}
	return resp, nil
}

// Reset 清空 bookings 与 leave_tickets（场馆保留）。
func (s *Server) Reset(ctx context.Context, req *toolv1.ResetRequest) (*toolv1.ResetResponse, error) {
	if err := s.biz.Reset(); err != nil {
		return nil, internalStatus(err)
	}
	return &toolv1.ResetResponse{}, nil
}

// Overview 当前预约与请假单（评测断言依赖）。
func (s *Server) Overview(ctx context.Context, req *toolv1.OverviewRequest) (*toolv1.OverviewResponse, error) {
	bookings, err := s.biz.AllBookings()
	if err != nil {
		return nil, internalStatus(err)
	}
	tickets, err := s.biz.AllTickets()
	if err != nil {
		return nil, internalStatus(err)
	}
	resp := &toolv1.OverviewResponse{
		Bookings: make([]*toolv1.BookingView, 0, len(bookings)),
		Tickets:  make([]*toolv1.TicketView, 0, len(tickets)),
	}
	for _, b := range bookings {
		resp.Bookings = append(resp.Bookings, &toolv1.BookingView{
			BookingId: b.BookingID, Venue: b.Venue, Date: b.Date, Slot: b.Slot, User: b.User,
		})
	}
	for _, t := range tickets {
		resp.Tickets = append(resp.Tickets, &toolv1.TicketView{
			Ticket: t.Ticket, User: t.User, LeaveType: t.LeaveType, Start: t.Start, End: t.End,
			Days: int32(t.Days), Approver: t.Approver, Status: t.Status,
		})
	}
	return resp, nil
}

func internalStatus(err error) error {
	return status.Error(codes.Internal, "tool 业务库错误: "+err.Error())
}
