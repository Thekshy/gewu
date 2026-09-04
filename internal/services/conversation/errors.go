package conversation

import (
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"
)

func internalErr(err error) error {
	return status.Error(codes.Internal, "conversation 存储错误: "+err.Error())
}
