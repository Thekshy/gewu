package business

import (
	"database/sql"
	"fmt"
	"os"
	"path/filepath"

	_ "modernc.org/sqlite" // 纯 Go SQLite 驱动，免 CGO
)

// db 对 *sql.DB 的轻封装：单连接串行化（与 Python sqlite3 单连接语义一致），
// 并提供事务小助手统一 rollback/commit 形态。
type db struct {
	raw *sql.DB
}

// openDB 打开（或创建）业务库并建表。
func openDB(path string) (*db, error) {
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		return nil, err
	}
	raw, err := sql.Open("sqlite", path)
	if err != nil {
		return nil, err
	}
	raw.SetMaxOpenConns(1)
	if err := execBusinessSchema(raw); err != nil {
		raw.Close()
		return nil, err
	}
	return &db{raw: raw}, nil
}

// execBusinessSchema 建表（静态字面量 DDL，无任何外部输入）。
func execBusinessSchema(d *sql.DB) error {
	if _, err := d.Exec(`CREATE TABLE IF NOT EXISTS venues (
		id TEXT PRIMARY KEY,
		name TEXT NOT NULL,
		kind TEXT NOT NULL,
		capacity INTEGER NOT NULL
	)`); err != nil {
		return fmt.Errorf("建 venues 表失败: %w", err)
	}
	if _, err := d.Exec(`CREATE TABLE IF NOT EXISTS bookings (
		id INTEGER PRIMARY KEY AUTOINCREMENT,
		venue_id TEXT NOT NULL,
		date TEXT NOT NULL,
		slot TEXT NOT NULL,
		purpose TEXT NOT NULL DEFAULT '',
		user TEXT NOT NULL,
		status TEXT NOT NULL DEFAULT '有效',
		created_at TEXT NOT NULL
	)`); err != nil {
		return fmt.Errorf("建 bookings 表失败: %w", err)
	}
	if _, err := d.Exec(`CREATE TABLE IF NOT EXISTS leave_tickets (
		id INTEGER PRIMARY KEY AUTOINCREMENT,
		user TEXT NOT NULL,
		leave_type TEXT NOT NULL,
		start_date TEXT NOT NULL,
		end_date TEXT NOT NULL,
		days INTEGER NOT NULL,
		reason TEXT NOT NULL,
		approver_level TEXT NOT NULL,
		status TEXT NOT NULL DEFAULT '待审批',
		created_at TEXT NOT NULL
	)`); err != nil {
		return fmt.Errorf("建 leave_tickets 表失败: %w", err)
	}
	return nil
}

func (d *db) query(queryStr string, args ...any) (*sql.Rows, error) {
	return d.raw.Query(queryStr, args...)
}

func (d *db) queryRow(queryStr string, args ...any) *sql.Row {
	return d.raw.QueryRow(queryStr, args...)
}

func (d *db) begin() (*tx, error) {
	t, err := d.raw.Begin()
	if err != nil {
		return nil, err
	}
	return &tx{raw: t}, nil
}

// Close 关闭底层连接。
func (d *db) Close() error { return d.raw.Close() }

// tx 事务小助手。
type tx struct {
	raw *sql.Tx
}

func (t *tx) exec(stmt string, args ...any) (sql.Result, error) {
	return t.raw.Exec(stmt, args...)
}

func (t *tx) commit() error   { return t.raw.Commit() }
func (t *tx) rollback() error { return t.raw.Rollback() }
