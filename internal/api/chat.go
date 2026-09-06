package api

import (
	"net/http"

	"github.com/gin-gonic/gin"
)

type chatRequest struct {
	Question  string `json:"question"`
	Mode      string `json:"mode"`
	SessionID string `json:"session_id"`
	Role      string `json:"role"`
}

// chat SSE 入口：校验 → 预算闸 → 逐事件写出并立刻 flush。
// ctx 取消（客户端断开）时 emit 返回错误中止管线（编排侧借 ctx 取消传播到上游 LLM 流）。
func (s *Server) chat(c *gin.Context) {
	var req chatRequest
	if err := c.ShouldBindJSON(&req); err != nil {
		unprocessable(c, "请求体不是合法 JSON")
		return
	}
	if req.Question == "" {
		unprocessable(c, "问题不能为空")
		return
	}
	if len([]rune(req.Question)) > s.settings.MaxQuestionChars {
		unprocessable(c, "问题过长")
		return
	}
	switch req.Mode {
	case "", "auto":
		req.Mode = "auto"
	case "direct", "research", "react":
	default:
		unprocessable(c, "mode 必须为 auto/direct/research/react")
		return
	}
	switch req.Role {
	case "", "student":
		req.Role = "student"
	case "counselor":
	default:
		unprocessable(c, "role 必须为 student/counselor")
		return
	}
	if req.SessionID == "" {
		req.SessionID = "default"
	}
	if len(req.SessionID) > 64 {
		unprocessable(c, "session_id 过长（上限 64 字符）")
		return
	}

	if err := s.budget.Ensure(); err != nil {
		c.JSON(http.StatusTooManyRequests, gin.H{"detail": err.Error()})
		return
	}

	ctx := c.Request.Context()

	c.Header("Content-Type", "text/event-stream")
	c.Header("Cache-Control", "no-cache")
	c.Header("X-Accel-Buffering", "no")
	c.Status(http.StatusOK)

	flusher, _ := c.Writer.(http.Flusher)
	emit := func(ev any) error {
		buf, err := marshalNoEscape(ev)
		if err != nil {
			return err
		}
		if _, err := c.Writer.Write(append(append([]byte("data: "), buf...), '\n', '\n')); err != nil {
			return err
		}
		if flusher != nil {
			flusher.Flush()
		}
		return nil
	}

	s.deps.RunChat(ctx, emit, req.Question, req.Mode, req.SessionID, req.Role, "")
}
