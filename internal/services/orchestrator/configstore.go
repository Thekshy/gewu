package orchestrator

import (
	"context"
	"database/sql"
	"encoding/json"
	"fmt"
	"sync"
	"time"

	_ "github.com/jackc/pgx/v5/stdlib"
	"github.com/redis/go-redis/v9"

	"gewu/internal/agent"

	"go.uber.org/zap"
)

// AgentConfig 编排配置。默认行 = 冻结 internal/agent/prompts.go 逐字快照
// （P2 验收：默认行与 prompts.go 逐字一致——agentConfigTestGolden 锁定）。
type AgentConfig struct {
	RouterSystem   string `json:"router_system"`
	SlotExtractSys string `json:"slot_extract_system"`
	PlannerSystem  string `json:"planner_system"`
	AnswerSystem   string `json:"answer_system"`
}

func defaultAgentConfig() AgentConfig {
	return AgentConfig{
		RouterSystem:   agent.RouterSystem,
		SlotExtractSys: agent.SlotExtractSystem,
		PlannerSystem:  agent.PlannerSystem,
		AnswerSystem:   agent.AnswerSystem,
	}
}

// configStore 配置存储：L1 进程内（30s，热路径零 RPC）→ L2 Redis（可选，
// 多实例一致性）→ L3 PG（真源）。Set 写 PG → 失效 Redis → 刷新 L1。
type configStore struct {
	pg  *sql.DB
	rdb *redis.Client
	log *zap.Logger

	mu        sync.Mutex
	l1        AgentConfig
	l1Default bool
	l1Expiry  time.Time
}

const (
	configRedisKey = "gewu:agent_config"
	configL1TTL    = 30 * time.Second
	configL2TTL    = 1 * time.Hour
)

// openConfigStore 打开配置存储。DSN 为空 → 纯内存（默认配置 + 进程内 Set）；
// Redis 地址为空 → 跳过 L2。
func openConfigStore(pgDSN, redisAddr string, log *zap.Logger) (*configStore, error) {
	cs := &configStore{log: log}
	if pgDSN != "" {
		db, err := sql.Open("pgx", pgDSN)
		if err != nil {
			return nil, err
		}
		if err := db.Ping(); err != nil {
			db.Close()
			return nil, fmt.Errorf("连接 PG 失败: %w", err)
		}
		if _, err := db.Exec(`CREATE TABLE IF NOT EXISTS agent_config (
			id INT PRIMARY KEY DEFAULT 1 CHECK (id = 1),
			router_system TEXT NOT NULL,
			slot_extract_system TEXT NOT NULL,
			planner_system TEXT NOT NULL,
			answer_system TEXT NOT NULL,
			updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
		)`); err != nil {
			db.Close()
			return nil, fmt.Errorf("建 agent_config 表失败: %w", err)
		}
		// 默认行种子：首次打开时写入（与 prompts.go 逐字一致）
		d := defaultAgentConfig()
		if _, err := db.Exec(`INSERT INTO agent_config (id, router_system, slot_extract_system, planner_system, answer_system)
			VALUES (1, $1, $2, $3, $4) ON CONFLICT (id) DO NOTHING`,
			d.RouterSystem, d.SlotExtractSys, d.PlannerSystem, d.AnswerSystem); err != nil {
			db.Close()
			return nil, fmt.Errorf("写入 agent_config 默认行失败: %w", err)
		}
		cs.pg = db
	}
	if redisAddr != "" {
		cs.rdb = redis.NewClient(&redis.Options{Addr: redisAddr})
	}
	return cs, nil
}

// Close 释放连接。
func (c *configStore) Close() error {
	if c.pg != nil {
		return c.pg.Close()
	}
	return nil
}

// Get 读配置（L1 → L2 → L3/L默认）。
func (c *configStore) Get(ctx context.Context) (AgentConfig, bool, error) {
	c.mu.Lock()
	if time.Now().Before(c.l1Expiry) {
		cfg, isDef := c.l1, c.l1Default
		c.mu.Unlock()
		return cfg, isDef, nil
	}
	c.mu.Unlock()

	// L2：Redis
	if c.rdb != nil {
		data, err := c.rdb.Get(ctx, configRedisKey).Bytes()
		if err == nil {
			var row struct {
				AgentConfig
				IsDefault bool `json:"is_default"`
			}
			if json.Unmarshal(data, &row) == nil {
				c.cacheL1(row.AgentConfig, row.IsDefault)
				return row.AgentConfig, row.IsDefault, nil
			}
		}
		// Redis miss/不可用 → 落 L3（不报错）
	}

	// L3：PG（或纯内存默认）
	if c.pg != nil {
		var cfg AgentConfig
		var updatedAt time.Time
		err := c.pg.QueryRowContext(ctx,
			`SELECT router_system, slot_extract_system, planner_system, answer_system, updated_at
			 FROM agent_config WHERE id = 1`).
			Scan(&cfg.RouterSystem, &cfg.SlotExtractSys, &cfg.PlannerSystem, &cfg.AnswerSystem, &updatedAt)
		if err != nil {
			return AgentConfig{}, false, err
		}
		isDefault := cfg == defaultAgentConfig()
		c.cacheL1(cfg, isDefault)
		if c.rdb != nil {
			c.cacheRedis(ctx, cfg, isDefault)
		}
		return cfg, isDefault, nil
	}

	d := defaultAgentConfig()
	c.cacheL1(d, true)
	return d, true, nil
}

// Set 更新配置（空字段沿用现值）→ 写 PG → 失效 Redis → 刷新 L1。
func (c *configStore) Set(ctx context.Context, in AgentConfig) (AgentConfig, error) {
	cur, _, err := c.Get(ctx)
	if err != nil {
		return AgentConfig{}, err
	}
	if in.RouterSystem != "" {
		cur.RouterSystem = in.RouterSystem
	}
	if in.SlotExtractSys != "" {
		cur.SlotExtractSys = in.SlotExtractSys
	}
	if in.PlannerSystem != "" {
		cur.PlannerSystem = in.PlannerSystem
	}
	if in.AnswerSystem != "" {
		cur.AnswerSystem = in.AnswerSystem
	}
	if c.pg != nil {
		if _, err := c.pg.ExecContext(ctx, `UPDATE agent_config
			SET router_system = $1, slot_extract_system = $2, planner_system = $3, answer_system = $4, updated_at = now()
			WHERE id = 1`,
			cur.RouterSystem, cur.SlotExtractSys, cur.PlannerSystem, cur.AnswerSystem); err != nil {
			return AgentConfig{}, err
		}
	}
	if c.rdb != nil {
		if err := c.rdb.Del(ctx, configRedisKey).Err(); err != nil {
			c.log.Warn("配置缓存失效失败", zap.Error(err))
		}
	}
	isDefault := cur == defaultAgentConfig()
	c.cacheL1(cur, isDefault)
	return cur, nil
}

func (c *configStore) cacheL1(cfg AgentConfig, isDefault bool) {
	c.mu.Lock()
	c.l1, c.l1Default, c.l1Expiry = cfg, isDefault, time.Now().Add(configL1TTL)
	c.mu.Unlock()
}

func (c *configStore) cacheRedis(ctx context.Context, cfg AgentConfig, isDefault bool) {
	data, err := json.Marshal(struct {
		AgentConfig
		IsDefault bool `json:"is_default"`
	}{cfg, isDefault})
	if err != nil {
		return
	}
	if err := c.rdb.Set(ctx, configRedisKey, data, configL2TTL).Err(); err != nil {
		c.log.Warn("配置写入 Redis 失败（跳过 L2）", zap.Error(err))
	}
}

// ---------- 管线取用（热路径：L1 命中零 RPC） ----------

func (c *configStore) routerPrompt(ctx context.Context) string {
	cfg, _, err := c.Get(ctx)
	if err != nil {
		return agent.RouterSystem // 存储异常退回内置默认，不阻断问答
	}
	return cfg.RouterSystem
}

func (c *configStore) slotExtractPrompt() string { return agent.SlotExtractSystem }

func (c *configStore) plannerPrompt(ctx context.Context) string {
	cfg, _, err := c.Get(ctx)
	if err != nil {
		return agent.PlannerSystem
	}
	return cfg.PlannerSystem
}

func (c *configStore) answerPrompt(ctx context.Context) string {
	cfg, _, err := c.Get(ctx)
	if err != nil {
		return agent.AnswerSystem
	}
	return cfg.AnswerSystem
}
