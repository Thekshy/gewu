package orchestrator

import (
	"io"

	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"
)

// errEOF 统一 EOF 判定（gRPC 流结束）。
var errEOF = io.EOF

// errText 取错误的可展示文本（gRPC 状态消息优先）。
func errText(err error) string {
	if st, ok := status.FromError(err); ok && st.Message() != "" {
		return st.Message()
	}
	return err.Error()
}

func internalStatus(err error) error {
	return status.Error(codes.Internal, "orchestrator 配置存储错误: "+err.Error())
}
