package conversation

import "gewu/internal/svcbase"

// Config 服务进程配置。
type Config struct {
	Addr      string
	AdminAddr string

	PostgresDSN string // 空 = 内存存储（开发降级；compose 内由编排注入 PG DSN）
}

// LoadConfig 从环境变量装配。
func LoadConfig() Config {
	return Config{
		Addr:        svcbase.EnvOr("CONVERSATION_ADDR", ":9002"),
		AdminAddr:   svcbase.EnvOr("CONVERSATION_ADMIN_ADDR", ":9102"),
		PostgresDSN: svcbase.EnvOr("POSTGRES_DSN", ""),
	}
}

// OpenStore 按配置构造存储：DSN 为空用内存实现（重启即失，开发模式），
// 否则连 PG（重启保留——有意差异，PARITY-MS 备案）。
func (c Config) OpenStore() (Store, func() error, error) {
	if c.PostgresDSN == "" {
		return NewMemoryStore(), func() error { return nil }, nil
	}
	pg, err := OpenPG(c.PostgresDSN)
	if err != nil {
		return nil, nil, err
	}
	return pg, pg.Close, nil
}
