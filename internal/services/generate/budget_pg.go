package generate

import (
	"database/sql"
	"fmt"

	_ "github.com/jackc/pgx/v5/stdlib"
)

// pgSink 预算持久化（PG）：UTC 日期为键写穿，语义对齐冻结单体的 usage.json
// （add 时同步落盘；文件损坏/无记录视为 0）。DSN 未配置时用内存模式
// （重启清零——开发降级，PARITY-MS 备案）。
type pgSink struct{ db *sql.DB }

func openPGSink(dsn string) (*pgSink, error) {
	db, err := sql.Open("pgx", dsn)
	if err != nil {
		return nil, err
	}
	if err := db.Ping(); err != nil {
		db.Close()
		return nil, fmt.Errorf("连接 PG 失败: %w", err)
	}
	if _, err := db.Exec(`CREATE TABLE IF NOT EXISTS budget_usage (
		date   TEXT PRIMARY KEY,
		tokens BIGINT NOT NULL DEFAULT 0
	)`); err != nil {
		db.Close()
		return nil, fmt.Errorf("建 budget_usage 表失败: %w", err)
	}
	return &pgSink{db: db}, nil
}

func (p *pgSink) Close() error { return p.db.Close() }

// LoadToday 取某 UTC 日期的已用量（无记录返回 0）。
func (p *pgSink) LoadToday(date string) (int64, error) {
	var tokens int64
	err := p.db.QueryRow(`SELECT tokens FROM budget_usage WHERE date = $1`, date).Scan(&tokens)
	if err == sql.ErrNoRows {
		return 0, nil
	}
	return tokens, err
}

// Persist 写穿（UPSERT）。
func (p *pgSink) Persist(date string, used int64) error {
	_, err := p.db.Exec(`INSERT INTO budget_usage (date, tokens) VALUES ($1, $2)
		ON CONFLICT (date) DO UPDATE SET tokens = $2`, date, used)
	return err
}
