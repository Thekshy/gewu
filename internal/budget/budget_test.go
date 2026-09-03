package budget

import (
	"errors"
	"os"
	"path/filepath"
	"testing"
)

func TestNewLoadsExistingUsage(t *testing.T) {
	path := filepath.Join(t.TempDir(), "usage.json")
	_ = os.WriteFile(path, []byte(`{"date":"`+todayUTC()+`","tokens":150}`), 0o644)
	b := New(path, 1000)
	if b.Used() != 150 {
		t.Fatalf("used = %d, want 150", b.Used())
	}
}

func TestStaleDateResetsToZero(t *testing.T) {
	path := filepath.Join(t.TempDir(), "usage.json")
	_ = os.WriteFile(path, []byte(`{"date":"2000-01-01","tokens":999}`), 0o644)
	b := New(path, 1000)
	if b.Used() != 0 {
		t.Fatalf("过期用量应清零, got %d", b.Used())
	}
}

func TestCorruptFileStartsAtZero(t *testing.T) {
	path := filepath.Join(t.TempDir(), "usage.json")
	_ = os.WriteFile(path, []byte("not json"), 0o644)
	b := New(path, 100)
	if b.Used() != 0 {
		t.Fatalf("损坏文件视为 0, got %d", b.Used())
	}
}

func TestEnsureBlocksAtLimit(t *testing.T) {
	path := filepath.Join(t.TempDir(), "usage.json")
	b := New(path, 100)
	if err := b.Ensure(); err != nil {
		t.Fatalf("未达上限不应拦截: %v", err)
	}
	b.Add(100)
	err := b.Ensure()
	if err == nil {
		t.Fatal("达到上限应拦截")
	}
	if !errors.Is(err, ErrBudgetExceeded) {
		t.Fatalf("应可 errors.Is 判定, got %v", err)
	}
	want := "今日 token 预算已用尽（上限 100），请明天再试"
	if err.Error() != want {
		t.Errorf("错误消息 = %q, want %q", err.Error(), want)
	}
}

func TestAddPersistsAcrossRestart(t *testing.T) {
	path := filepath.Join(t.TempDir(), "usage.json")
	New(path, 1000).Add(42)
	if b := New(path, 1000); b.Used() != 42 {
		t.Fatalf("跨重启应读到 42, got %d", b.Used())
	}
}

func TestAddIgnoresNonPositive(t *testing.T) {
	path := filepath.Join(t.TempDir(), "usage.json")
	b := New(path, 1000)
	b.Add(0)
	b.Add(-5)
	if b.Used() != 0 {
		t.Fatalf("非正数不应入账, got %d", b.Used())
	}
}
