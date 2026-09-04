package gateway

import (
	"net/http"

	"github.com/gin-gonic/gin"

	ragv1 "gewu/pkg/gen/gewu/rag/v1"
)

// ---------- /api/search（PARITY §2.3） ----------

type searchRequest struct {
	Query string `json:"query"`
	K     *int   `json:"k"` // 指针区分「未提供（缺省 5）」与「显式 0（422）」
}

// search 调试端点：直接查看混合检索命中（校验文案与截断 300 逐字）。
func (s *Server) search(c *gin.Context) {
	var req searchRequest
	if err := c.ShouldBindJSON(&req); err != nil {
		unprocessable(c, "请求体不是合法 JSON")
		return
	}
	if l := len([]rune(req.Query)); l < 1 || l > 200 {
		unprocessable(c, "query 长度需在 1~200 字之间")
		return
	}
	k := 5
	if req.K != nil {
		k = *req.K
	}
	if k < 1 || k > 20 {
		unprocessable(c, "k 需在 1~20 之间")
		return
	}
	resp, err := s.rag.Search(c.Request.Context(), &ragv1.SearchRequest{Query: req.Query, K: int32(k)})
	if err != nil {
		c.JSON(http.StatusServiceUnavailable, gin.H{"detail": "rag 不可达"})
		return
	}
	type hitJSON struct {
		DocID  string `json:"doc_id"`
		Title  string `json:"title"`
		Source string `json:"source"`
		Seq    int32  `json:"seq"`
		Text   string `json:"text"`
	}
	out := make([]hitJSON, 0, len(resp.GetHits()))
	for _, h := range resp.GetHits() {
		out = append(out, hitJSON{
			DocID: h.GetDocId(), Title: h.GetTitle(), Source: h.GetSource(),
			Seq: h.GetSeq(), Text: truncateRunes(h.GetText(), 300),
		})
	}
	c.JSON(http.StatusOK, out)
}

// ---------- /api/docs（PARITY §2.2） ----------

// listDocs 已入库文档列表（按 doc_id 升序）。
func (s *Server) listDocs(c *gin.Context) {
	resp, err := s.rag.ListDocs(c.Request.Context(), &ragv1.ListDocsRequest{})
	if err != nil {
		c.JSON(http.StatusServiceUnavailable, gin.H{"detail": "rag 不可达"})
		return
	}
	type docJSON struct {
		DocID   string `json:"doc_id"`
		Title   string `json:"title"`
		Source  string `json:"source"`
		Updated string `json:"updated"`
		Chunks  int32  `json:"chunks"`
	}
	out := make([]docJSON, 0, len(resp.GetDocs()))
	for _, d := range resp.GetDocs() {
		out = append(out, docJSON{
			DocID: d.GetDocId(), Title: d.GetTitle(), Source: d.GetSource(),
			Updated: d.GetUpdated(), Chunks: d.GetChunks(),
		})
	}
	c.JSON(http.StatusOK, out)
}

// truncateRunes 按 rune 截断。
func truncateRunes(str string, n int) string {
	r := []rune(str)
	if len(r) <= n {
		return str
	}
	return string(r[:n])
}
