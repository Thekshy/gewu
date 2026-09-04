package conversation

import (
	"context"
	"os"
	"testing"
	"time"
)

// 会话语义套件（PARITY §12.3）：内存与 PG 两个实现必须通过同一套测试——
// 行为对齐靠同一套件保证。

// agedStore 可把指定会话的时间戳拨回 31 分钟前（TTL 测试用）。
type agedStore interface {
	Store
	ageSession(ctx context.Context, sessionID string) error
}

// memoryStore 的可注入时钟包装。
type clockedMemory struct {
	*MemoryStore
	now time.Time
}

func (c *clockedMemory) AgeSession(ctx context.Context, sessionID string) error {
	c.now = c.now.Add(31 * time.Minute)
	return nil
}

// MemoryStore.ageSession：clockedMemory 通过替换 clock 生效。
func (c *clockedMemory) ageSession(ctx context.Context, sessionID string) error {
	return c.AgeSession(ctx, sessionID)
}

func TestMemoryStoreSemantics(t *testing.T) {
	m := &clockedMemory{MemoryStore: NewMemoryStore(), now: time.Now()}
	m.MemoryStore.clock = func() time.Time { return m.now }
	runSemantics(t, m)
}

func TestPGStoreSemantics(t *testing.T) {
	dsn := os.Getenv("TEST_PG_DSN")
	if dsn == "" {
		t.Skip("TEST_PG_DSN 未设置，跳过 PG 语义套件（compose: postgres://gewu:gewu@localhost:5432/gewu?sslmode=disable）")
	}
	pg, err := OpenPG(dsn)
	if err != nil {
		t.Fatalf("连接 PG 失败：%v", err)
	}
	defer pg.Close()
	runSemantics(t, &pgAged{pg})
}

// TestPGStoreRestartRetention P2 验收：服务重启（重开存储）后会话保留。
func TestPGStoreRestartRetention(t *testing.T) {
	dsn := os.Getenv("TEST_PG_DSN")
	if dsn == "" {
		t.Skip("TEST_PG_DSN 未设置，跳过（需 PG）")
	}
	ctx := context.Background()
	pg1, err := OpenPG(dsn)
	if err != nil {
		t.Fatal(err)
	}
	_ = pg1.Clear(ctx, "restart-1")
	sess, err := pg1.Ensure(ctx, "restart-1", "student", "demo-student")
	if err != nil {
		t.Fatal(err)
	}
	sess.Phase, sess.Tool, sess.LastAsked = "collect", "book_venue", "date"
	sess.Slots["venue"] = "venue-badminton"
	if err := pg1.Save(ctx, sess); err != nil {
		t.Fatal(err)
	}
	if err := pg1.Close(); err != nil { // 模拟服务重启
		t.Fatal(err)
	}

	pg2, err := OpenPG(dsn)
	if err != nil {
		t.Fatal(err)
	}
	defer pg2.Close()
	got, err := pg2.Get(ctx, "restart-1")
	if err != nil {
		t.Fatal(err)
	}
	if got == nil || got.Phase != "collect" || got.Tool != "book_venue" ||
		got.LastAsked != "date" || got.Slots["venue"] != "venue-badminton" {
		t.Fatalf("重启后会话应保留：%+v", got)
	}
	_ = pg2.Clear(ctx, "restart-1")
}

// pgAged 直接改 updated_at 实现时间拨移。
type pgAged struct{ *PGStore }

func (p *pgAged) ageSession(ctx context.Context, sessionID string) error {
	_, err := p.db.ExecContext(ctx,
		`UPDATE tx_sessions SET updated_at = now() - interval '31 minutes' WHERE session_id = $1`, sessionID)
	return err
}

func runSemantics(t *testing.T, s agedStore) {
	t.Helper()
	ctx := context.Background()

	// Get 不存在 → nil
	if sess, err := s.Get(ctx, "s1"); err != nil || sess != nil {
		t.Fatalf("不存在会话应返回 nil：%v %v", sess, err)
	}

	// Ensure 建会话（idle）并刷新 role/user
	s1, err := s.Ensure(ctx, "s1", "student", "demo-student")
	if err != nil {
		t.Fatal(err)
	}
	if s1.Phase != "idle" || s1.Role != "student" || s1.User != "demo-student" {
		t.Fatalf("Ensure 语义不匹配：%+v", s1)
	}
	s1b, _ := s.Ensure(ctx, "s1", "counselor", "demo-counselor")
	if s1b.Role != "counselor" || s1b.User != "demo-counselor" {
		t.Fatalf("Ensure 应刷新 role/user：%+v", s1b)
	}

	// Save 全量保存；Get 返回副本（外部改动不影响存储）
	s1b.Phase = "collect"
	s1b.Tool = "book_venue"
	s1b.Slots["venue"] = "venue-badminton"
	s1b.LastAsked = "date"
	if err := s.Save(ctx, s1b); err != nil {
		t.Fatal(err)
	}
	got, _ := s.Get(ctx, "s1")
	if got.Phase != "collect" || got.Tool != "book_venue" || got.Slots["venue"] != "venue-badminton" || got.LastAsked != "date" {
		t.Fatalf("Save/Get 语义不匹配：%+v", got)
	}
	got.Slots["venue"] = "tampered"
	again, _ := s.Get(ctx, "s1")
	if again.Slots["venue"] != "venue-badminton" {
		t.Fatal("Get 必须返回副本（slots 隔离）")
	}

	// Clear 移除
	if err := s.Clear(ctx, "s1"); err != nil {
		t.Fatal(err)
	}
	if sess, _ := s.Get(ctx, "s1"); sess != nil {
		t.Fatal("Clear 后 Get 应为 nil")
	}

	// TTL：31 分钟前活动过的会话视同不存在；Ensure 重建为 idle
	s2, _ := s.Ensure(ctx, "s2", "student", "demo-student")
	s2.Phase = "confirm"
	_ = s.Save(ctx, s2)
	if err := s.ageSession(ctx, "s2"); err != nil {
		t.Fatal(err)
	}
	if sess, _ := s.Get(ctx, "s2"); sess != nil {
		t.Fatal("过期会话 Get 应为 nil（TTL 30 分钟惰性清理）")
	}
	s2b, _ := s.Ensure(ctx, "s2", "student", "demo-student")
	if s2b.Phase != "idle" {
		t.Fatalf("过期后 Ensure 应重建 idle 会话：%+v", s2b)
	}
	_ = s.Clear(ctx, "s2")
}
