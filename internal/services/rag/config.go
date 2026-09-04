package rag

import (
	"path/filepath"

	"gewu/internal/svcbase"
)

// Config 服务进程配置。
type Config struct {
	Addr      string
	AdminAddr string

	GenerateAddr string // embed 与查询改写走 generate
	PostgresDSN  string // docs/chunks 元数据（空 = 不可用，服务启动报错）
	RedisAddr    string // 摄入 MQ（Redis Streams）；空 = 不可用
	MilvusAddr   string // 向量存储；空 = PG 余弦降级实现

	DataDir    string
	CorpusDir  string
	RetrievalK int
}

// LoadConfig 从环境变量装配（缺省对齐冻结单体 DATA_DIR/CORPUS_DIR 语义）。
func LoadConfig() Config {
	dataDir := svcbase.EnvOr("DATA_DIR", "data")
	return Config{
		Addr:      svcbase.EnvOr("RAG_ADDR", ":9005"),
		AdminAddr: svcbase.EnvOr("RAG_ADMIN_ADDR", ":9105"),

		GenerateAddr: svcbase.NormalizeTarget(svcbase.EnvOr("GENERATE_ADDR", ":9003")),
		PostgresDSN:  svcbase.EnvOr("POSTGRES_DSN", ""),
		RedisAddr:    svcbase.EnvOr("REDIS_ADDR", ""),
		MilvusAddr:   svcbase.EnvOr("MILVUS_ADDR", ""),

		DataDir:    dataDir,
		CorpusDir:  svcbase.EnvOr("CORPUS_DIR", filepath.Join(dataDir, "corpus")),
		RetrievalK: envInt("RETRIEVAL_K", 6),
	}
}

func envInt(key string, def int) int {
	if v := svcbase.EnvOr(key, ""); v != "" {
		if n, err := parseInt(v); err == nil && n > 0 {
			return n
		}
	}
	return def
}
