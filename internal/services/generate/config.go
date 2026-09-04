// Package generate 生成服务：LLM 网关（provider 接口 + 模型分层，PARITY §13）+
// 每日 token 预算集中计量。P0 骨架——BudgetStatus 已可用（health 聚合依赖），
// Chat/ChatStream/Embed 在 P1 接入。
package generate

import (
	"strconv"

	"gewu/internal/svcbase"
)

// Config 服务进程配置。
type Config struct {
	Addr      string
	AdminAddr string

	APIKey           string // LLM_API_KEY，空 = 零 key 演示模式
	BaseURL          string
	MainModel        string
	SmallModel       string
	EmbedModel       string
	DisableThinking  bool
	DailyTokenBudget int64 // 每日 token 上限（PARITY §12.2）
}

// LoadConfig 从环境变量装配；缺省值与冻结单体 internal/config 一致。
func LoadConfig() Config {
	budget := int64(2_000_000)
	if v, err := strconv.ParseInt(svcbase.EnvOr("DAILY_TOKEN_BUDGET", ""), 10, 64); err == nil && v > 0 {
		budget = v
	}
	return Config{
		Addr:      svcbase.EnvOr("GENERATE_ADDR", ":9003"),
		AdminAddr: svcbase.EnvOr("GENERATE_ADMIN_ADDR", ":9103"),

		APIKey:           svcbase.EnvOr("LLM_API_KEY", ""),
		BaseURL:          svcbase.EnvOr("LLM_BASE_URL", "https://open.bigmodel.cn/api/paas/v4/"),
		MainModel:        svcbase.EnvOr("LLM_MODEL", "glm-5.3"),
		SmallModel:       svcbase.EnvOr("LLM_SMALL_MODEL", "glm-5.3-flash"),
		EmbedModel:       svcbase.EnvOr("EMBED_MODEL", "embedding-3"),
		DisableThinking:  svcbase.EnvOr("LLM_DISABLE_THINKING", "false") == "true",
		DailyTokenBudget: budget,
	}
}
