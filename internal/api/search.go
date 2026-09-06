package api

import (
	"net/http"

	"github.com/gin-gonic/gin"
)

type searchRequest struct {
	Query string `json:"query"`
	K     *int   `json:"k"` // 指针区分「未提供（缺省 5）」与「显式 0（422）」，对齐 pydantic ge=1 语义
}

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
	hits, err := s.deps.Retriever.Search(c.Request.Context(), req.Query, k)
	if err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"detail": err.Error()})
		return
	}
	type hitJSON struct {
		DocID  string `json:"doc_id"`
		Title  string `json:"title"`
		Source string `json:"source"`
		Seq    int    `json:"seq"`
		Text   string `json:"text"`
	}
	out := make([]hitJSON, 0, len(hits))
	for _, h := range hits {
		out = append(out, hitJSON{DocID: h.DocID, Title: h.Title, Source: h.Source, Seq: h.Seq, Text: truncateRunes(h.Text, 300)})
	}
	c.JSON(http.StatusOK, out)
}
