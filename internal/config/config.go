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
	LLMKey             string // LLM_API_KEY，chat 主端点密钥（启动时强制非空）
	LLMBaseURL         string // 任意 OpenAI 兼容端点
	LLMModel           string // 主答案模型
	LLMSmallModel      string // 辅助调用（路由/抽取/改写等）模型
	LLMDisableThinking bool

	// Embed 独立通道（P6 阶段0：chat=智谱 GLM，embedding=火山方舟）。
	// EmbedAPIKey/EmbedBaseURL 缺省时回退 LLM_*（同供应商部署的场景零配置）。
	EmbedAPIKey  string // EMBED_API_KEY
	EmbedBaseURL string // EMBED_BASE_URL
	EmbedModel   string // EMBED_MODEL
	EmbedMode    string // EMBED_MODE：text（标准 /embeddings）| ark_multimodal（火山多模态，不支持批量）

	// P6 行为开关（默认开启新链路，旧实现保留可回退）。
	RouterMode   string // ROUTER_MODE：cascade（默认，级联路由）| classic（旧单次 LLM 分类）
	ChunkMode    string // CHUNK_MODE：hierarchical（默认，父子块）| flat（旧单层切分）
	RerankMode   string // RERANK_MODE：on（默认，LLM 精排）| off
	ReactMode    string // REACT_MODE：off（默认，纯 workflow）| on（路径不定的办理问题转 ReAct）
	QueryRewrite string // QUERY_REWRITE：on（默认，多轮指代消解补全）| off（路由/检索只见裸问题）

	// P8-1 会话持久化：办理流程状态（槽位/确认）的存储后端。
	SessionStore string // SESSION_STORE：sqlite（默认，跨重启续办）| memory（进程内 map）

	DataDir   string
	CorpusDir string // 缺省 {DataDir}/corpus
	IndexPath string // 缺省 {DataDir}/index.db

	// PGDSN 检索索引存储连接串（P12：PostgreSQL + pgvector，make pg-up 起本地容器）。
	PGDSN string // PG_DSN，缺省 postgres://gewu:gewu@127.0.0.1:5433/gewu?sslmode=disable

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
		EmbedMode:          "text",
		RouterMode:         "cascade",
		ChunkMode:          "hierarchical",
		RerankMode:         "on",
		ReactMode:          "off",
		QueryRewrite:       "on",
		SessionStore:       "sqlite",
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
	s.PGDSN = firstNonEmpty(lookup("PG_DSN"), "postgres://gewu:gewu@127.0.0.1:5433/gewu?sslmode=disable")
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
		"LLM_API_KEY", "LLM_BASE_URL", "LLM_MODEL", "LLM_SMALL_MODEL",
		"EMBED_API_KEY", "EMBED_BASE_URL", "EMBED_MODEL", "EMBED_MODE",
		"ROUTER_MODE", "CHUNK_MODE", "RERANK_MODE", "REACT_MODE", "QUERY_REWRITE",
		"SESSION_STORE",
		"LLM_DISABLE_THINKING", "DATA_DIR", "CORPUS_DIR", "INDEX_PATH", "PG_DSN",
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
	case "EMBED_API_KEY":
		s.EmbedAPIKey = val
	case "EMBED_BASE_URL":
		s.EmbedBaseURL = val
	case "EMBED_MODEL":
		s.EmbedModel = val
	case "EMBED_MODE":
		s.EmbedMode = val
	case "ROUTER_MODE":
		s.RouterMode = val
	case "CHUNK_MODE":
		s.ChunkMode = val
	case "RERANK_MODE":
		s.RerankMode = val
	case "REACT_MODE":
		s.ReactMode = val
	case "QUERY_REWRITE":
		s.QueryRewrite = val
	case "SESSION_STORE":
		s.SessionStore = val
	case "LLM_DISABLE_THINKING":
		s.LLMDisableThinking = parseBool(val)
	case "DATA_DIR":
		s.DataDir = val
	case "CORPUS_DIR":
		s.CorpusDir = val
	case "INDEX_PATH":
		s.IndexPath = val
	case "PG_DSN":
		s.PGDSN = val
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
