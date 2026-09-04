package tool

import (
	toolv1 "gewu/pkg/gen/gewu/tool/v1"

	"go.uber.org/zap"
)

// Server 工具服务实现。P0 骨架：继承 Unimplemented，
// P4 落地业务系统 PG 迁移 + 权限矩阵 + user 服务端注入（metadata x-user）。
type Server struct {
	toolv1.UnimplementedToolServiceServer
	log *zap.Logger
}

// NewServer 构造工具服务。
func NewServer(log *zap.Logger) *Server { return &Server{log: log} }
