// Package config 提供全局配置：环境变量优先，仓库根 .env 文件兜底。
// 变量名与被替换的 Python 版（config.py）保持一致，见 docs/PARITY.md §14。
package config

import (
	"os"
	"path/filepath"
	"strconv"
	"strings"
)

// Version 对外暴露的服务版本（Python 版 __version__）。
const Version = "0.1.0"

// Settings 为模型无关的全局配置，进程内单例。
type Settings struct {
	LLMKey             string // LLM_API_KEY，空串 = 零 key 演示模式
	LLMBaseURL         string // 任意 OpenAI 兼容端点
	LLMModel           string // 主答案模型
	LLMSmallModel      string // 辅助调用（路由/抽取/改写等）模型
	EmbedModel         string
	LLMDisableThinking bool

	DataDir   string
	CorpusDir string // 缺省 {DataDir}/corpus
	IndexPath string // 缺省 {DataDir}/index.db

	RateLimitPerMinute int
	DailyTokenBudget   int64

	RetrievalK       int
	MaxQuestionChars int
}

// Default 返回内置缺省值（与 Python 版一致）。
func Default() *Settings {
	return &Settings{
		LLMBaseURL:         "https://open.bigmodel.cn/api/paas/v4/",
		LLMModel:           "glm-5.3",
		LLMSmallModel:      "glm-5.3-flash",
		EmbedModel:         "embedding-3",
		DataDir:            "data",
		RateLimitPerMinute: 20,
		DailyTokenBudget:   2_000_000,
		RetrievalK:         6,
		MaxQuestionChars:   500,
	}
}

// Load 构造配置：先取缺省，再叠加 .env（工作目录及其上级查找仓库根），
// 最后叠加进程环境变量（优先级最高，与 pydantic-settings 语义一致）。
func Load() *Settings {
	s := Default()
	if env := firstExistingEnvFile(); env != "" {
		applyEnvFile(s, env)
	}
	applyOSEnv(s)
	s.CorpusDir = firstNonEmpty(lookup("CORPUS_DIR"), filepath.Join(s.DataDir, "corpus"))
	s.IndexPath = firstNonEmpty(lookup("INDEX_PATH"), filepath.Join(s.DataDir, "index.db"))
	return s
}

// firstExistingEnvFile 从工作目录逐级向上找第一个存在的 .env（最多 4 级），
// 覆盖「仓库根 make run」与「apps/api 目录直跑」两种用法。
func firstExistingEnvFile() string {
	dir, err := os.Getwd()
	if err != nil {
		return ""
	}
	for i := 0; i < 4 && dir != string(filepath.Separator) && dir != "."; i++ {
		p := filepath.Join(dir, ".env")
		if st, err := os.Stat(p); err == nil && !st.IsDir() {
			return p
		}
		dir = filepath.Dir(dir)
	}
	return ""
}

// applyEnvFile 解析 .env 的 key=value 行（# 注释忽略），写入尚未由环境变量提供的字段。
func applyEnvFile(s *Settings, path string) {
	data, err := os.ReadFile(path)
	if err != nil {
		return
	}
	for _, line := range strings.Split(string(data), "\n") {
		line = strings.TrimSpace(line)
		if line == "" || strings.HasPrefix(line, "#") {
			continue
		}
		key, val, ok := strings.Cut(line, "=")
		if !ok {
			continue
		}
		key = strings.TrimSpace(key)
		if _, fromOS := os.LookupEnv(key); fromOS {
			continue // 进程环境变量优先，.env 不覆盖
		}
		setField(s, key, strings.TrimSpace(val))
	}
}

func applyOSEnv(s *Settings) {
	for _, key := range []string{
		"LLM_API_KEY", "LLM_BASE_URL", "LLM_MODEL", "LLM_SMALL_MODEL", "EMBED_MODEL",
		"LLM_DISABLE_THINKING", "DATA_DIR", "CORPUS_DIR", "INDEX_PATH",
		"RATE_LIMIT_PER_MINUTE", "DAILY_TOKEN_BUDGET", "RETRIEVAL_K", "MAX_QUESTION_CHARS",
	} {
		if v, ok := os.LookupEnv(key); ok {
			setField(s, key, v)
		}
	}
}

func setField(s *Settings, key, val string) {
	switch key {
	case "LLM_API_KEY":
		s.LLMKey = val
	case "LLM_BASE_URL":
		s.LLMBaseURL = val
	case "LLM_MODEL":
		s.LLMModel = val
	case "LLM_SMALL_MODEL":
		s.LLMSmallModel = val
	case "EMBED_MODEL":
		s.EmbedModel = val
	case "LLM_DISABLE_THINKING":
		s.LLMDisableThinking = parseBool(val)
	case "DATA_DIR":
		s.DataDir = val
	case "CORPUS_DIR":
		s.CorpusDir = val
	case "INDEX_PATH":
		s.IndexPath = val
	case "RATE_LIMIT_PER_MINUTE":
		if n, err := strconv.Atoi(val); err == nil {
			s.RateLimitPerMinute = n
		}
	case "DAILY_TOKEN_BUDGET":
		if n, err := strconv.ParseInt(val, 10, 64); err == nil {
			s.DailyTokenBudget = n
		}
	case "RETRIEVAL_K":
		if n, err := strconv.Atoi(val); err == nil && n > 0 {
			s.RetrievalK = n
		}
	case "MAX_QUESTION_CHARS":
		if n, err := strconv.Atoi(val); err == nil && n > 0 {
			s.MaxQuestionChars = n
		}
	}
}

func parseBool(v string) bool {
	switch strings.ToLower(strings.TrimSpace(v)) {
	case "1", "true", "yes", "on":
		return true
	}
	return false
}

func lookup(key string) string { return os.Getenv(key) }

func firstNonEmpty(vals ...string) string {
	for _, v := range vals {
		if v != "" {
			return v
		}
	}
	return ""
}
