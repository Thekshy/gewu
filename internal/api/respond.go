package api

import (
	"encoding/json"
	"net/http"
	"strings"

	"github.com/gin-gonic/gin"
)

// unprocessable 参数校验失败（与 Python 版 FastAPI 422 语义对齐）。
func unprocessable(c *gin.Context, msg string) {
	c.JSON(http.StatusUnprocessableEntity, gin.H{"detail": msg})
}

// marshalNoEscape 序列化事件：不转义 HTML 字符也保留 UTF-8 原文
// （与 Python json.dumps(ensure_ascii=False) 的输出对齐）。
func marshalNoEscape(v any) ([]byte, error) {
	var sb strings.Builder
	enc := json.NewEncoder(&sb)
	enc.SetEscapeHTML(false)
	if err := enc.Encode(v); err != nil {
		return nil, err
	}
	// Encoder 会追加换行，去掉
	return []byte(strings.TrimRight(sb.String(), "\n")), nil
}

func truncateRunes(s string, n int) string {
	r := []rune(s)
	if len(r) <= n {
		return s
	}
	return string(r[:n])
}
