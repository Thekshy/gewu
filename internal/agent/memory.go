package agent

import (
	"context"
	"database/sql"
	"fmt"
	"log"
	"strings"
	"time"

	"gewu/internal/llm"
)

// P6 阶段4：长期记忆（单体 SQLite 版，不引入 PG/Redis/pgvector）。
//
// 分层：
//   - memory_fact     用户级结构化事实（profile|preference|constraint），LLM 异步抽取
//     后 UPSERT 覆盖（同 key 只留最新值），装配时全量注入；
//   - memory_episodic 会话级原始对话（每轮 user/assistant 两条），装配时取本会话
//     最近 N 条作"近期对话要点"。
//
// 注入顺序固定：system → 长期记忆 → RAG 知识 → 当前问题（assembleMessages）。
// 记忆是增强不是依赖：无记忆库/无数据/LLM 失败时主链路结构与行为不变。

// MemoryStore SQLite 长期记忆存储。
type MemoryStore struct {
	db *sql.DB
}

// OpenMemory 打开（或创建）记忆库并建表。建议独立文件 data/memory.db（职责清晰）。
func OpenMemory(path string) (*MemoryStore, error) {
	db, err := sql.Open("sqlite", path)
	if err != nil {
		return nil, err
	}
	db.SetMaxOpenConns(1)
	// DDL 为静态字面量，无任何外部输入；逐条执行便于定位失败。
	if _, err := db.Exec(`CREATE TABLE IF NOT EXISTS memory_episodic (
		id         INTEGER PRIMARY KEY AUTOINCREMENT,
		session_id TEXT NOT NULL,
		user_id    TEXT NOT NULL,
		kind       TEXT NOT NULL,
		text       TEXT NOT NULL,
		created_at TEXT NOT NULL DEFAULT (datetime('now'))
	)`); err != nil {
		db.Close()
		return nil, fmt.Errorf("建 memory_episodic 表失败: %w", err)
	}
	if _, err := db.Exec(`CREATE INDEX IF NOT EXISTS idx_memory_episodic_session
		ON memory_episodic(session_id, id)`); err != nil {
		db.Close()
		return nil, fmt.Errorf("建 memory_episodic 索引失败: %w", err)
	}
	if _, err := db.Exec(`CREATE TABLE IF NOT EXISTS memory_fact (
		user_id    TEXT NOT NULL,
		kind       TEXT NOT NULL,
		key        TEXT NOT NULL,
		value      TEXT NOT NULL,
		updated_at TEXT NOT NULL DEFAULT (datetime('now')),
		PRIMARY KEY (user_id, kind, key)
	)`); err != nil {
		db.Close()
		return nil, fmt.Errorf("建 memory_fact 表失败: %w", err)
	}
	return &MemoryStore{db: db}, nil
}

// Close 关闭底层连接。
func (m *MemoryStore) Close() error {
	if m == nil || m.db == nil {
		return nil
	}
	return m.db.Close()
}

// Fact 一条用户级结构化事实。
type Fact struct {
	Kind  string // profile | preference | constraint
	Key   string
	Value string
}

// UpsertFacts 事实写入：同 (user_id,kind,key) 新值覆盖旧值。
// 语句为静态字面量，全部用户数据经 ? 占位符参数化传入。
func (m *MemoryStore) UpsertFacts(userID string, facts []Fact) error {
	if m == nil {
		return nil
	}
	for _, f := range facts {
		if strings.TrimSpace(f.Key) == "" || strings.TrimSpace(f.Value) == "" {
			continue
		}
		if _, err := m.db.Exec(
			`INSERT INTO memory_fact (user_id, kind, key, value, updated_at)
			 VALUES (?, ?, ?, ?, datetime('now'))
			 ON CONFLICT(user_id, kind, key)
			 DO UPDATE SET value = excluded.value, updated_at = datetime('now')`,
			userID, f.Kind, f.Key, f.Value,
		); err != nil {
			return err
		}
	}
	return nil
}

// Facts 取用户全部事实（按 kind,key 排序，注入顺序确定）。
func (m *MemoryStore) Facts(userID string) ([]Fact, error) {
	if m == nil {
		return nil, nil
	}
	rows, err := m.db.Query(
		`SELECT kind, key, value FROM memory_fact WHERE user_id = ? ORDER BY kind, key`, userID)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var out []Fact
	for rows.Next() {
		var f Fact
		if err := rows.Scan(&f.Kind, &f.Key, &f.Value); err != nil {
			return nil, err
		}
		out = append(out, f)
	}
	return out, rows.Err()
}

// maxFactsInContext 注入 prompt 的事实条数上限：长期会话事实会持续累积，
// 只注入最近固化的 Top-N，超出的保留在库但不进 prompt（P7）。
const maxFactsInContext = 20

// RecentFacts 按固化时间倒序取最近 n 条事实（时间同秒的平局按 kind,key
// 稳定排序，保证注入顺序确定）。
func (m *MemoryStore) RecentFacts(userID string, n int) ([]Fact, error) {
	if m == nil || n <= 0 {
		return nil, nil
	}
	rows, err := m.db.Query(
		`SELECT kind, key, value FROM memory_fact WHERE user_id = ?
		 ORDER BY updated_at DESC, kind, key LIMIT ?`, userID, n)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var out []Fact
	for rows.Next() {
		var f Fact
		if err := rows.Scan(&f.Kind, &f.Key, &f.Value); err != nil {
			return nil, err
		}
		out = append(out, f)
	}
	return out, rows.Err()
}

// AppendEpisode 记录一轮对话的原始文本（kind: user | assistant）。
func (m *MemoryStore) AppendEpisode(sessionID, userID, kind, text string) error {
	if m == nil || strings.TrimSpace(text) == "" {
		return nil
	}
	_, err := m.db.Exec(
		`INSERT INTO memory_episodic (session_id, user_id, kind, text) VALUES (?, ?, ?, ?)`,
		sessionID, userID, kind, text)
	return err
}

// RecentEpisodes 取本会话最近 n 条对话文本（时间正序：旧 → 新）。
func (m *MemoryStore) RecentEpisodes(sessionID string, n int) ([]string, error) {
	if m == nil || n <= 0 {
		return nil, nil
	}
	rows, err := m.db.Query(
		`SELECT kind, text FROM (
		   SELECT kind, text, id FROM memory_episodic
		   WHERE session_id = ? ORDER BY id DESC LIMIT ?
		 ) ORDER BY id ASC`, sessionID, n)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var out []string
	for rows.Next() {
		var kind, text string
		if err := rows.Scan(&kind, &text); err != nil {
			return nil, err
		}
		label := "用户"
		if kind == "assistant" {
			label = "助手"
		}
		out = append(out, label+"："+text)
	}
	return out, rows.Err()
}

// consolidatePrompt 事实抽取提示词（glm-5.3-flash，JSONMode）。
const consolidatePrompt = `从对话中抽取关于该用户的稳定事实、偏好或约束（如专业、年级、绩点、姓名、宿舍、目标院校/方向）。
只抽取明确表达或可直接确定的信息，不要推测。key 用简短英文标识（如 major、grade、gpa、dorm、goal），
value 保留用户原表述。每条含 kind（profile=身份事实 / preference=偏好 / constraint=约束条件）。
只输出 JSON：{"facts":[{"kind":"profile","key":"major","value":"计算机科学"}]}；没有可抽取信息时输出 {"facts":[]}`

// consolidateTimeout 单轮记忆固化的超时（异步路径，失败只告警）。
const consolidateTimeout = 60 * time.Second

// consolidateAsync 会话结束后固化记忆（不阻塞回答路径）。
// episodic 原文同步落库（毫秒级 SQLite 写，不含 LLM）：下一轮的上下文补全
// （ResolveQuery）立即能看到上一轮对话，不受异步抽取延迟影响；
// 事实抽取仍是异步 LLM 调用——"不在用户等待路径做 LLM"的原则不变。
func (d *Deps) consolidateAsync(_ context.Context, userID, sessionID, question, answer string) {
	if d.Memory == nil {
		return
	}
	if err := d.appendEpisodes(userID, sessionID, question, answer); err != nil {
		log.Printf("[agent] episodic 写入失败（不影响主链路）：%v", err)
	}
	if d.LLM == nil || !d.LLM.HasKey() {
		return
	}
	go func() {
		ctx, cancel := context.WithTimeout(context.Background(), consolidateTimeout)
		defer cancel()
		if err := d.extractFacts(ctx, userID, question, answer); err != nil {
			log.Printf("[agent] 记忆固化失败（不影响主链路）：%v", err)
		}
	}()
}

// Consolidate 一轮对话的固化（同步版，测试与手动触发用）：
// episodic 原文入库 + LLM 抽取稳定事实 UPSERT。
func (d *Deps) Consolidate(ctx context.Context, userID, sessionID, question, answer string) error {
	if d.Memory == nil {
		return nil
	}
	if err := d.appendEpisodes(userID, sessionID, question, answer); err != nil {
		return err
	}
	return d.extractFacts(ctx, userID, question, answer)
}

// appendEpisodes 原文落库（纯 SQLite 写，无 LLM）。
func (d *Deps) appendEpisodes(userID, sessionID, question, answer string) error {
	if err := d.Memory.AppendEpisode(sessionID, userID, "user", question); err != nil {
		return fmt.Errorf("episodic 写入失败: %w", err)
	}
	if err := d.Memory.AppendEpisode(sessionID, userID, "assistant", answer); err != nil {
		return fmt.Errorf("episodic 写入失败: %w", err)
	}
	return nil
}

// extractFacts LLM 抽取稳定事实并 UPSERT；无 LLM 时跳过。
func (d *Deps) extractFacts(ctx context.Context, userID, question, answer string) error {
	if d.LLM == nil || !d.LLM.HasKey() {
		return nil
	}
	raw, err := d.LLM.Chat(ctx, []llm.Message{
		{Role: "system", Content: consolidatePrompt},
		{Role: "user", Content: "用户：" + question + "\n助手：" + truncate(answer, 800)},
	}, llm.Options{JSONMode: true, Temperature: 0, MaxTokens: 300, Small: true})
	if err != nil {
		return fmt.Errorf("事实抽取调用失败: %w", err)
	}
	obj, perr := parseJSONObject(raw)
	if perr != nil {
		return nil // 抽取输出不合法：放弃本轮（原文已留存），不影响主链路
	}
	var facts []Fact
	if arr, ok := obj["facts"].([]any); ok {
		for _, item := range arr {
			m, ok := item.(map[string]any)
			if !ok {
				continue
			}
			facts = append(facts, Fact{
				Kind:  jsonStr(m, "kind"),
				Key:   jsonStr(m, "key"),
				Value: jsonStr(m, "value"),
			})
		}
	}
	if len(facts) == 0 {
		return nil
	}
	if err := d.Memory.UpsertFacts(userID, facts); err != nil {
		return fmt.Errorf("事实写入失败: %w", err)
	}
	return nil
}

// memoryBlock 组装注入 prompt 的长期记忆块（fact + 本会话近期对话要点）。
// 事实按固化时间倒序最多注入 maxFactsInContext 条（超出的保留在库不进 prompt）。
// 空数据返回空串——assembleMessages 据此保持与历史版本逐字一致的消息结构。
func (d *Deps) memoryBlock(userID, sessionID string) string {
	if d.Memory == nil {
		return ""
	}
	var lines []string
	if facts, err := d.Memory.RecentFacts(userID, maxFactsInContext); err != nil {
		log.Printf("[agent] 记忆读取失败（按无记忆处理）：%v", err)
	} else {
		for _, f := range facts {
			lines = append(lines, fmt.Sprintf("- %s/%s：%s", f.Kind, f.Key, f.Value))
		}
	}
	if episodes, err := d.Memory.RecentEpisodes(sessionID, 4); err != nil {
		log.Printf("[agent] 会话历史读取失败（按无记忆处理）：%v", err)
	} else if len(episodes) > 0 {
		lines = append(lines, "近期对话要点：")
		lines = append(lines, episodes...)
	}
	return strings.Join(lines, "\n")
}
