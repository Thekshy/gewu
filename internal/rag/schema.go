package rag

import (
	"database/sql"
	"fmt"
)

// execSchema 建表（IF NOT EXISTS，重复调用幂等）。DDL 为静态字面量，无任何外部输入。
func execSchema(db *sql.DB) error {
	if _, err := db.Exec(`CREATE TABLE IF NOT EXISTS docs (
		id      TEXT PRIMARY KEY,
		title   TEXT NOT NULL,
		source  TEXT NOT NULL,
		updated TEXT NOT NULL DEFAULT ''
	)`); err != nil {
		return fmt.Errorf("建 docs 表失败: %w", err)
	}
	if _, err := db.Exec(`CREATE TABLE IF NOT EXISTS chunks (
		id     INTEGER PRIMARY KEY AUTOINCREMENT,
		doc_id TEXT NOT NULL,
		seq    INTEGER NOT NULL,
		text   TEXT NOT NULL
	)`); err != nil {
		return fmt.Errorf("建 chunks 表失败: %w", err)
	}
	if _, err := db.Exec(`CREATE TABLE IF NOT EXISTS vectors (
		chunk_id INTEGER PRIMARY KEY,
		dim      INTEGER NOT NULL,
		data     BLOB NOT NULL
	)`); err != nil {
		return fmt.Errorf("建 vectors 表失败: %w", err)
	}
	return nil
}
