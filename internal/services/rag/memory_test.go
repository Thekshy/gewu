package rag

import (
	"context"
	"os"
	"testing"

	"go.uber.org/zap"
)

// 记忆语义套件（P5）：Put/Recall + recency 降级路径（PG 必需）。
func TestMemoryStore(t *testing.T) {
	dsn := os.Getenv("TEST_PG_DSN")
	if dsn == "" {
		t.Skip("TEST_PG_DSN 未设置，跳过（需 PG）")
	}
	ctx := context.Background()
	store, err := openPGStore(dsn)
	if err != nil {
		t.Fatal(err)
	}
	defer store.Close()
	if err := store.openMemory(); err != nil {
		t.Fatal(err)
	}
	if _, err := store.db.ExecContext(ctx, `DELETE FROM kb_memory WHERE session_id LIKE 'memtest-%'`); err != nil {
		t.Fatal(err)
	}
	s := &Server{store: store, log: zap.NewNop(), gen: &generateAdapter{}}

	// 空 Recall
	items, err := s.memoryRecall(ctx, "memtest-a", "任意查询", 5)
	if err != nil || len(items) != 0 {
		t.Fatalf("空会话 Recall 应为空: %v %v", items, err)
	}

	// Put 三条
	id1, err := s.memoryPut(ctx, "memtest-a", "第一条：我在准备转专业申请")
	if err != nil || id1 == "" {
		t.Fatalf("Put1: %v %v", id1, err)
	}
	id2, err := s.memoryPut(ctx, "memtest-a", "第二条：我在准备转专业申请")
	if err != nil || id2 == "" || id2 == id1 {
		t.Fatalf("Put2: %v %v", id2, err)
	}
	if _, err := s.memoryPut(ctx, "memtest-b", "别的会话的记忆"); err != nil {
		t.Fatal(err)
	}

	// 无 key（测试环境 gen 未接线）→ recency 降级：会话隔离 + 最近优先
	items, err = s.memoryRecall(ctx, "memtest-a", "转专业", 5)
	if err != nil {
		t.Fatal(err)
	}
	if len(items) != 2 {
		t.Fatalf("会话隔离失败: %v", items)
	}
	if items[0].ID != id2 {
		t.Fatalf("recency 降级应最近优先: %v", items)
	}
	if items[0].Score != 0 {
		t.Fatalf("降级路径 score 应为 0: %v", items[0])
	}

	// k 截断
	for i := 0; i < 3; i++ {
		_, _ = s.memoryPut(ctx, "memtest-c", "填充记忆")
	}
	items, err = s.memoryRecall(ctx, "memtest-c", "查询", 2)
	if err != nil || len(items) != 2 {
		t.Fatalf("k 截断: %v %v", items, err)
	}

	// 清理
	if _, err := store.db.ExecContext(ctx, `DELETE FROM kb_memory WHERE session_id LIKE 'memtest-%'`); err != nil {
		t.Fatal(err)
	}
}
