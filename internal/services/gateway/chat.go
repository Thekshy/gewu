package gateway

import (
	"errors"
	"io"
	"net/http"
	"strconv"
	"time"

	"github.com/gin-gonic/gin"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"

	orchestratorv1 "gewu/pkg/gen/gewu/orchestrator/v1"
)

// chatRequest /api/chat 请求体（PARITY §2.4）。
type chatRequest struct {
	Question  string `json:"question"`
	Mode      string `json:"mode"`
	SessionID string `json:"session_id"`
	Role      string `json:"role"`
}

// chat SSE 流式问答：校验（文案逐字）→ orchestrator 流 → SSE 帧透传。
// 预算耗尽：orchestrator 在流建立前返回 RESOURCE_EXHAUSTED → 此处映射 429
// （发生在写 SSE 头之前，与冻结单体 chat 入口预检行为一致）。
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
	if len([]rune(req.Question)) > s.cfg.MaxQuestionChars {
		unprocessable(c, "问题过长")
		return
	}
	switch req.Mode {
	case "", "auto":
		req.Mode = "auto"
	case "direct", "research":
	default:
		unprocessable(c, "mode 必须为 auto/direct/research")
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

	stream, err := s.orchestrator.Chat(c.Request.Context(), &orchestratorv1.ChatRequest{
		Question:  req.Question,
		Mode:      req.Mode,
		SessionId: req.SessionID,
		Role:      req.Role,
	})
	if err != nil {
		s.mapStreamError(c, err)
		return
	}
	// 首帧先行：gRPC server-streaming 的流前错误（预检耗尽/下游不可达）
	// 在首个 Recv 才浮现——必须在写 SSE 头之前完成映射
	first, err := stream.Recv()
	if err != nil {
		s.mapStreamError(c, err)
		return
	}

	// SSE：手工逐事件写入并立刻 flush；ctx 取消（客户端断开）时 Recv 报错退出
	c.Header("Content-Type", "text/event-stream")
	c.Header("Cache-Control", "no-cache")
	c.Header("X-Accel-Buffering", "no")
	c.Status(http.StatusOK)

	flusher, _ := c.Writer.(http.Flusher)
	t0 := time.Now()
	handleFrame := func(ev *orchestratorv1.ChatResponse) bool {
		frame, err := eventJSON(ev)
		if err != nil {
			_ = s.writeFrame(c, errorFrame("事件序列化失败: "+err.Error()), flusher)
			_ = s.writeFrame(c, doneFrame(time.Since(t0).Milliseconds()), flusher)
			return false
		}
		if err := s.writeFrame(c, frame, flusher); err != nil {
			return false // 写失败 = 客户端断开
		}
		return true
	}
	if !handleFrame(first) {
		return
	}
	for {
		ev, err := stream.Recv()
		if errors.Is(err, io.EOF) {
			return
		}
		if err != nil {
			// 客户端已断开：直接结束（上游取消经 ctx 传播，无需再补帧）
			if c.Request.Context().Err() != nil {
				return
			}
			// 极罕见：orchestrator 中途崩溃/网络断——补 error+done 保持契约形状
			// （latency 为网关侧近似值；orchestrator 自身兜底不会走到这里）
			_ = s.writeFrame(c, errorFrame(status.Convert(err).Message()), flusher)
			_ = s.writeFrame(c, doneFrame(time.Since(t0).Milliseconds()), flusher)
			return
		}
		if !handleFrame(ev) {
			return
		}
	}
}

// mapStreamError 流前错误 → HTTP 映射（尚未写 SSE 头）。
func (s *Server) mapStreamError(c *gin.Context, err error) {
	st := status.Convert(err)
	switch st.Code() {
	case codes.ResourceExhausted:
		c.JSON(http.StatusTooManyRequests, gin.H{"detail": st.Message()})
	case codes.InvalidArgument:
		unprocessable(c, st.Message())
	case codes.Unavailable:
		c.JSON(http.StatusServiceUnavailable, gin.H{"detail": "orchestrator 不可达"})
	default:
		c.JSON(http.StatusInternalServerError, gin.H{"detail": st.Message()})
	}
}

// writeFrame 写一帧 SSE（data: {JSON}\n\n）并立刻 flush。
func (s *Server) writeFrame(c *gin.Context, frame []byte, flusher http.Flusher) error {
	if _, err := c.Writer.Write(append(append([]byte("data: "), frame...), '\n', '\n')); err != nil {
		return err
	}
	if flusher != nil {
		flusher.Flush()
	}
	return nil
}

func errorFrame(msg string) []byte {
	b, _ := marshalNoEscape(sseError{Type: "error", Message: msg})
	return b
}

func doneFrame(ms int64) []byte {
	b, _ := marshalNoEscape(sseDone{Type: "done", LatencyMS: ms})
	return b
}

func unprocessable(c *gin.Context, msg string) {
	c.JSON(http.StatusUnprocessableEntity, gin.H{"detail": msg})
}

// parseMaxQuestionChars MAX_QUESTION_CHARS 环境解析（缺省 500，非法回落）。
func parseMaxQuestionChars(v string) int {
	if n, err := strconv.Atoi(v); err == nil && n > 0 {
		return n
	}
	return 500
}
