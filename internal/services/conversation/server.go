package conversation

import (
	"context"

	conversationv1 "gewu/pkg/gen/gewu/conversation/v1"

	"go.uber.org/zap"
)

// Server 会话服务 gRPC 实现：proto ↔ 领域模型的薄层，语义全部在 Store。
type Server struct {
	conversationv1.UnimplementedConversationServiceServer
	log   *zap.Logger
	store Store
}

// NewServer 构造会话服务。
func NewServer(store Store, log *zap.Logger) *Server { return &Server{store: store, log: log} }

// GetSession 取会话（nil = 不存在或已过期）。
func (s *Server) GetSession(ctx context.Context, req *conversationv1.GetSessionRequest) (*conversationv1.GetSessionResponse, error) {
	sess, err := s.store.Get(ctx, req.GetSessionId())
	if err != nil {
		return nil, internalErr(err)
	}
	if sess == nil {
		return &conversationv1.GetSessionResponse{}, nil
	}
	return &conversationv1.GetSessionResponse{Session: toProto(sess)}, nil
}

// EnsureSession 取或建会话并刷新 role/user。
func (s *Server) EnsureSession(ctx context.Context, req *conversationv1.EnsureSessionRequest) (*conversationv1.EnsureSessionResponse, error) {
	sess, err := s.store.Ensure(ctx, req.GetSessionId(), req.GetRole(), req.GetUser())
	if err != nil {
		return nil, internalErr(err)
	}
	return &conversationv1.EnsureSessionResponse{Session: toProto(sess)}, nil
}

// SaveSession 保存编排侧变更后的全量状态。
func (s *Server) SaveSession(ctx context.Context, req *conversationv1.SaveSessionRequest) (*conversationv1.SaveSessionResponse, error) {
	if err := s.store.Save(ctx, fromProto(req.GetSession())); err != nil {
		return nil, internalErr(err)
	}
	return &conversationv1.SaveSessionResponse{}, nil
}

// ClearSession 移除会话。
func (s *Server) ClearSession(ctx context.Context, req *conversationv1.ClearSessionRequest) (*conversationv1.ClearSessionResponse, error) {
	if err := s.store.Clear(ctx, req.GetSessionId()); err != nil {
		return nil, internalErr(err)
	}
	return &conversationv1.ClearSessionResponse{}, nil
}

// AppendMessage 审计消息落库（尽力而为：失败仅记日志，不阻断主链路）。
func (s *Server) AppendMessage(ctx context.Context, req *conversationv1.AppendMessageRequest) (*conversationv1.AppendMessageResponse, error) {
	if err := s.store.AppendMessage(ctx, Message{
		SessionID: req.GetSessionId(), Role: req.GetRole(), Content: req.GetContent(),
	}); err != nil {
		s.log.Warn("消息落库失败", zap.Error(err))
		return &conversationv1.AppendMessageResponse{}, nil
	}
	return &conversationv1.AppendMessageResponse{}, nil
}

func toProto(s *Session) *conversationv1.SessionState {
	return &conversationv1.SessionState{
		SessionId:   s.SessionID,
		Role:        s.Role,
		User:        s.User,
		Phase:       s.Phase,
		Tool:        s.Tool,
		Slots:       s.Slots,
		LastAsked:   s.LastAsked,
		UpdatedUnix: s.Updated.Unix(),
	}
}

func fromProto(p *conversationv1.SessionState) *Session {
	slots := p.GetSlots()
	if slots == nil {
		slots = map[string]string{}
	}
	return &Session{
		SessionID: p.GetSessionId(),
		Role:      p.GetRole(),
		User:      p.GetUser(),
		Phase:     p.GetPhase(),
		Tool:      p.GetTool(),
		Slots:     slots,
		LastAsked: p.GetLastAsked(),
	}
}
