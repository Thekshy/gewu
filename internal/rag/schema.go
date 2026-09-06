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
	// P6 父子块：新建库直接带全列（parent_id 指向父块 chunk id 的文本形式，
	// 父块自身为 NULL；is_parent=1 的父块不建向量、不进召回）。
	if _, err := db.Exec(`CREATE TABLE IF NOT EXISTS chunks (
		id           INTEGER PRIMARY KEY AUTOINCREMENT,
		doc_id       TEXT NOT NULL,
		seq          INTEGER NOT NULL,
		text         TEXT NOT NULL,
		parent_id    TEXT,
		section_path TEXT NOT NULL DEFAULT '',
		is_parent    INTEGER NOT NULL DEFAULT 0
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
	if err := migrateChunksColumns(db); err != nil {
		return err
	}
	// 父块回取索引：命中子块按 parent_id 批量取父块。
	if _, err := db.Exec(`CREATE INDEX IF NOT EXISTS idx_chunks_parent ON chunks(parent_id)`); err != nil {
		return fmt.Errorf("建 parent_id 索引失败: %w", err)
	}
	return nil
}

// migrateChunksColumns 老库幂等迁移：SQLite 的 ADD COLUMN 不支持 IF NOT EXISTS，
// 先 PRAGMA table_info 探测缺列，再逐列执行静态 DDL 补齐（对已有行取默认值）。
func migrateChunksColumns(db *sql.DB) error {
	rows, err := db.Query(`PRAGMA table_info(chunks)`)
	if err != nil {
		return fmt.Errorf("探测 chunks 表结构失败: %w", err)
	}
	defer rows.Close()
	existing := map[string]bool{}
	for rows.Next() {
		var cid int
		var name, ctype string
		var notNull, pk int
		var dflt sql.NullString
		if err := rows.Scan(&cid, &name, &ctype, &notNull, &dflt, &pk); err != nil {
			return err
		}
		existing[name] = true
	}
	if err := rows.Err(); err != nil {
		return err
	}
	// 每列一条静态 DDL 与静态错误文案（列名是代码内常量，不经任何外部输入）。
	if !existing["parent_id"] {
		if _, err := db.Exec(`ALTER TABLE chunks ADD COLUMN parent_id TEXT`); err != nil {
			return fmt.Errorf("迁移 chunks.parent_id 失败: %w", err)
		}
	}
	if !existing["section_path"] {
		if _, err := db.Exec(`ALTER TABLE chunks ADD COLUMN section_path TEXT NOT NULL DEFAULT ''`); err != nil {
			return fmt.Errorf("迁移 chunks.section_path 失败: %w", err)
		}
	}
	if !existing["is_parent"] {
		if _, err := db.Exec(`ALTER TABLE chunks ADD COLUMN is_parent INTEGER NOT NULL DEFAULT 0`); err != nil {
			return fmt.Errorf("迁移 chunks.is_parent 失败: %w", err)
		}
	}
	return nil
}
