package orchestrator

import (
	"context"
	"sync"
	"time"

	conversationv1 "gewu/pkg/gen/gewu/conversation/v1"
)

// TxSession 办理会话的编排侧形态（跨进程：每轮取回→变更→回写，
// 轮内原子、轮间持久——与冻结单体的可见行为一致）。
// Cleared 为编排侧本地标记（清会话后阻止轮末回写复活），不参与序列化。
type TxSession struct {
	SessionID string
	Role      string
	User      string
	Phase     string // idle | collect | confirm
	Tool      string
	Slots     map[string]string
	LastAsked string
	Cleared   bool
}

// sessionStore 会话存取（conversation 服务经 gRPC；测试用内存实现）。
type sessionStore interface {
	Get(ctx context.Context, sessionID string) (*TxSession, error)
	Ensure(ctx context.Context, sessionID, role, user string) (*TxSession, error)
	Save(ctx context.Context, s *TxSession) error
	Clear(ctx context.Context, sessionID string) error
	AppendMessage(ctx context.Context, sessionID, role, content string) error
}

// grpcSessions conversation 服务的 gRPC 适配。
type grpcSessions struct {
	client conversationv1.ConversationServiceClient
}

func newGrpcSessions(client conversationv1.ConversationServiceClient) *grpcSessions {
	return &grpcSessions{client: client}
}

func (g *grpcSessions) Get(ctx context.Context, sessionID string) (*TxSession, error) {
	resp, err := g.client.GetSession(ctx, &conversationv1.GetSessionRequest{SessionId: sessionID})
	if err != nil {
		return nil, err
	}
	return fromSessionProto(resp.GetSession()), nil // nil session → nil
}

func (g *grpcSessions) Ensure(ctx context.Context, sessionID, role, user string) (*TxSession, error) {
	resp, err := g.client.EnsureSession(ctx, &conversationv1.EnsureSessionRequest{
		SessionId: sessionID, Role: role, User: user,
	})
	if err != nil {
		return nil, err
	}
	return fromSessionProto(resp.GetSession()), nil
}

func (g *grpcSessions) Save(ctx context.Context, s *TxSession) error {
	_, err := g.client.SaveSession(ctx, &conversationv1.SaveSessionRequest{Session: toSessionProto(s)})
	return err
}

func (g *grpcSessions) Clear(ctx context.Context, sessionID string) error {
	_, err := g.client.ClearSession(ctx, &conversationv1.ClearSessionRequest{SessionId: sessionID})
	return err
}

func (g *grpcSessions) AppendMessage(ctx context.Context, sessionID, role, content string) error {
	_, err := g.client.AppendMessage(ctx, &conversationv1.AppendMessageRequest{
		SessionId: sessionID, Role: role, Content: content,
	})
	return err
}

func fromSessionProto(p *conversationv1.SessionState) *TxSession {
	if p == nil {
		return nil
	}
	slots := p.GetSlots()
	if slots == nil {
		slots = map[string]string{}
	}
	return &TxSession{
		SessionID: p.GetSessionId(),
		Role:      p.GetRole(),
		User:      p.GetUser(),
		Phase:     p.GetPhase(),
		Tool:      p.GetTool(),
		Slots:     slots,
		LastAsked: p.GetLastAsked(),
	}
}

func toSessionProto(s *TxSession) *conversationv1.SessionState {
	return &conversationv1.SessionState{
		SessionId: s.SessionID,
		Role:      s.Role,
		User:      s.User,
		Phase:     s.Phase,
		Tool:      s.Tool,
		Slots:     s.Slots,
		LastAsked: s.LastAsked,
	}
}

// memorySessions 进程内会话（测试用；语义对齐 conversation 服务）。
type memorySessions struct {
	mu   sync.Mutex
	data map[string]*TxSession
}

func newMemorySessions() *memorySessions {
	return &memorySessions{data: map[string]*TxSession{}}
}

func (m *memorySessions) Get(ctx context.Context, sessionID string) (*TxSession, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	s := m.data[sessionID]
	if s == nil {
		return nil, nil
	}
	return copySession(s), nil
}

func (m *memorySessions) Ensure(ctx context.Context, sessionID, role, user string) (*TxSession, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	s, ok := m.data[sessionID]
	if !ok {
		s = &TxSession{SessionID: sessionID, Phase: "idle", Slots: map[string]string{}}
		m.data[sessionID] = s
	}
	s.Role, s.User = role, user
	return copySession(s), nil
}

func (m *memorySessions) Save(ctx context.Context, s *TxSession) error {
	m.mu.Lock()
	defer m.mu.Unlock()
	m.data[s.SessionID] = copySession(s)
	return nil
}

func (m *memorySessions) Clear(ctx context.Context, sessionID string) error {
	m.mu.Lock()
	defer m.mu.Unlock()
	delete(m.data, sessionID)
	return nil
}

func (m *memorySessions) AppendMessage(ctx context.Context, sessionID, role, content string) error {
	return nil
}

func copySession(s *TxSession) *TxSession {
	cp := *s
	cp.Slots = map[string]string{}
	for k, v := range s.Slots {
		cp.Slots[k] = v
	}
	return &cp
}

// sessionIdleTTL 与 conversation 侧一致的常量引用（文档一致性）。
const sessionIdleTTL = 30 * time.Minute
