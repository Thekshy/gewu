package agent

import (
	"path/filepath"
	"testing"
	"time"
)

// openTestSessions 打开临时目录下的 SQLite 会话存储（clock 可注入）。
func openTestSessions(t *testing.T, clock func() time.Time) SessionStore {
	t.Helper()
	s, err := openSessionStore(filepath.Join(t.TempDir(), "sessions.db"), clock)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = s.Close() })
	return s
}

// TestSQLiteSessionStore_ResumeAcrossReopen 跨「重启」续办：collect 阶段会话
// 关闭后重新打开，Get 取回完整状态并可继续 advance 到确认。
func TestSQLiteSessionStore_ResumeAcrossReopen(t *testing.T) {
	d := testDeps(t)
	path := filepath.Join(t.TempDir(), "sessions.db")
	store, err := openSessionStore(path, time.Now)
	if err != nil {
		t.Fatal(err)
	}
	d.Sessions = store

	sid := "resume-sqlite-1"
	// 首轮发起办理：场馆/日期可从原句机会性抽取，时段留空 → collect 追问 slot
	events := ask(t, d, sid, "帮我预约明天的羽毛球馆", "student")
	sess := d.Sessions.Get(sid)
	if sess == nil || sess.Phase != PhaseCollect {
		t.Fatalf("首轮应处于 collect 阶段，got phase=%v", sess)
	}
	if sess.Tool != "book_venue" {
		t.Fatalf("工具识别错误：got %q want book_venue", sess.Tool)
	}
	if sess.Slots["venue"] == "" || sess.Slots["date"] == "" {
		t.Fatalf("venue/date 应已收集，slots=%v", sess.Slots)
	}
	if sess.Slots["slot"] != "" {
		t.Fatalf("时段不应被收集（原句未指明具体时段），slots=%v", sess.Slots)
	}
	if hasEvent(events, "slot_question") == nil {
		t.Fatalf("首轮应发出 slot_question 追问，events=%v", events)
	}

	// 「重启」：关闭再打开，同一文件恢复
	if err := store.Close(); err != nil {
		t.Fatal(err)
	}
	reopened, err := openSessionStore(path, time.Now)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = reopened.Close() })
	d.Sessions = reopened

	sess = d.Sessions.Get(sid)
	if sess == nil {
		t.Fatal("重启后应取回办理中的会话")
	}
	if sess.Phase != PhaseCollect || sess.Tool != "book_venue" {
		t.Fatalf("重启后状态不完整：phase=%s tool=%s", sess.Phase, sess.Tool)
	}
	if sess.Slots["venue"] == "" || sess.Slots["date"] == "" || sess.Slots["slot"] != "" {
		t.Fatalf("重启后 slots 不完整：%v", sess.Slots)
	}
	if sess.LastAsked != "slot" {
		t.Fatalf("重启后 LastAsked 应为 slot（追问上下文续上），got %q", sess.LastAsked)
	}

	// 第二轮补时段：应继续 advance 到确认
	events = ask(t, d, sid, "晚上七点", "student")
	if hasEvent(events, "pending_action") == nil {
		t.Fatalf("重启后续轮应走到确认（pending_action），events=%v", events)
	}
	if sess := d.Sessions.Get(sid); sess == nil || sess.Phase != PhaseConfirm {
		t.Fatalf("重启后续轮后应处于 confirm 阶段，got %v", sess)
	}
}

// TestSQLiteSessionStore_TTLExpire TTL 过期惰性清理（clock 注入推进 31 分钟）。
func TestSQLiteSessionStore_TTLExpire(t *testing.T) {
	now := time.Date(2026, 9, 6, 12, 0, 0, 0, time.Local)
	clock := func() time.Time { return now }
	s := openTestSessions(t, clock)

	sid := "ttl-1"
	s.Ensure(sid, "student", "u1")
	if s.Get(sid) == nil {
		t.Fatal("写入后应可取回")
	}

	now = now.Add(ttl + time.Minute)
	if got := s.Get(sid); got != nil {
		t.Fatalf("过期后应返回 nil，got %v", got)
	}
}

// TestSQLiteSessionStore_SlotsJSONRoundTrip slots JSON 往返无损（特殊字符）。
func TestSQLiteSessionStore_SlotsJSONRoundTrip(t *testing.T) {
	path := filepath.Join(t.TempDir(), "sessions.db")
	s, err := openSessionStore(path, time.Now)
	if err != nil {
		t.Fatal(err)
	}
	want := map[string]string{
		"venue":  "羽毛球馆",
		"reason": "含\"引号\"与\\反斜杠",
		"note":   "多行\n文本\t制表",
		"emoji":  "🏸 中文混排 ok",
	}
	sess := s.Ensure("roundtrip-1", "student", "u1")
	sess.Tool, sess.Phase, sess.LastAsked = "book_venue", PhaseCollect, "slot"
	for k, v := range want {
		sess.Slots[k] = v
	}
	if err := s.Sync(); err != nil {
		t.Fatal(err)
	}
	if err := s.Close(); err != nil {
		t.Fatal(err)
	}

	s2, err := openSessionStore(path, time.Now)
	if err != nil {
		t.Fatal(err)
	}
	defer s2.Close()
	got := s2.Get("roundtrip-1")
	if got == nil {
		t.Fatal("重开后应取回会话")
	}
	if len(got.Slots) != len(want) {
		t.Fatalf("slots 键数不一致：got %v want %v", got.Slots, want)
	}
	for k, v := range want {
		if got.Slots[k] != v {
			t.Fatalf("slot %q 往返失真：got %q want %q", k, got.Slots[k], v)
		}
	}
}

// TestSQLiteSessionStore_ClearPersists Clear 同步删库：重启后不复活。
func TestSQLiteSessionStore_ClearPersists(t *testing.T) {
	path := filepath.Join(t.TempDir(), "sessions.db")
	s, err := openSessionStore(path, time.Now)
	if err != nil {
		t.Fatal(err)
	}
	sid := "clear-1"
	s.Ensure(sid, "student", "u1")
	if err := s.Sync(); err != nil {
		t.Fatal(err)
	}
	s.Clear(sid)
	if err := s.Close(); err != nil {
		t.Fatal(err)
	}

	s2, err := openSessionStore(path, time.Now)
	if err != nil {
		t.Fatal(err)
	}
	defer s2.Close()
	if got := s2.Get(sid); got != nil {
		t.Fatalf("Clear 后重启不应复活会话，got %v", got)
	}
}

// TestSQLiteSessionStore_TTLSyncAfterEvict 过期清理落库后 Sync 不复活过期会话。
func TestSQLiteSessionStore_TTLSyncAfterEvict(t *testing.T) {
	now := time.Date(2026, 9, 6, 12, 0, 0, 0, time.Local)
	path := filepath.Join(t.TempDir(), "sessions.db")
	s, err := openSessionStore(path, func() time.Time { return now })
	if err != nil {
		t.Fatal(err)
	}
	sid := "evict-1"
	s.Ensure(sid, "student", "u1")
	if err := s.Sync(); err != nil {
		t.Fatal(err)
	}

	// 推进到过期：内存中清理；全量写透只写 map 中存活会话
	now = now.Add(ttl + time.Minute)
	if got := s.Get(sid); got != nil {
		t.Fatalf("过期后应返回 nil，got %v", got)
	}
	if err := s.Sync(); err != nil {
		t.Fatal(err)
	}
	if err := s.Close(); err != nil {
		t.Fatal(err)
	}

	s2, err := openSessionStore(path, func() time.Time { return now })
	if err != nil {
		t.Fatal(err)
	}
	defer s2.Close()
	// DB 里的旧行 Updated 仍过期：加载后 TTL 判定同样过期，Get 返回 nil
	if got := s2.Get(sid); got != nil {
		t.Fatalf("过期会话重启后仍应判定过期，got %v", got)
	}
}

// hasEvent 按事件类型查找（断言辅助）。
func hasEvent(events []any, etype string) any {
	for _, ev := range events {
		if typeNameOf(ev) == etype {
			return ev
		}
	}
	return nil
}

// guard：确保 RunChat 出口 Sync 不破坏零 key 离线办理链路（内存后端行为不变）。
func TestRunChatSyncNoopOnMemoryStore(t *testing.T) {
	d := testDeps(t)
	events := ask(t, d, "sync-noop-1", "帮我预约明天晚上的羽毛球馆", "student")
	if hasEvent(events, "error") != nil {
		t.Fatalf("离线链路不应报错，events=%v", events)
	}
	if d.Sessions.Get("sync-noop-1") == nil {
		t.Fatal("内存后端会话应仍在")
	}
}
