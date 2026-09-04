// Package svcbase 提供六个微服务共享的进程基座：zap 结构化日志、
// trace-id 贯穿（gRPC 拦截器 + context）、gRPC 服务装配（health/reflection/
// 管理端口 healthz）与优雅退出。业务逻辑不在这里——这里只保证每个进程
// 长得一样：同样的观测字段、同样的启停语义。
package svcbase

import (
	"os"
	"strings"

	"go.uber.org/zap"
	"go.uber.org/zap/zapcore"
)

// NewLogger 构造服务日志器：JSON 结构化输出到 stdout，附服务名字段。
// LOG_LEVEL=debug 可放详细日志（缺省 info）。
func NewLogger(service string) *zap.Logger {
	level := zapcore.InfoLevel
	switch strings.ToLower(os.Getenv("LOG_LEVEL")) {
	case "debug":
		level = zapcore.DebugLevel
	case "warn":
		level = zapcore.WarnLevel
	case "error":
		level = zapcore.ErrorLevel
	}
	cfg := zap.NewProductionConfig()
	cfg.Level = zap.NewAtomicLevelAt(level)
	cfg.Encoding = "json"
	cfg.EncoderConfig.TimeKey = "ts"
	cfg.EncoderConfig.EncodeTime = zapcore.ISO8601TimeEncoder
	cfg.EncoderConfig.EncodeDuration = zapcore.MillisDurationEncoder
	log, err := cfg.Build()
	if err != nil {
		// 日志器本身构造失败只能落标准错误，进程继续由调用方决定去留
		panic("构造日志器失败: " + err.Error())
	}
	return log.With(zap.String("svc", service))
}
