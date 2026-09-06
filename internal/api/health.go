package api

import (
	"net/http"

	"github.com/gin-gonic/gin"

	"gewu/internal/config"
	"gewu/internal/rag"
)

func (s *Server) health(c *gin.Context) {
	stats, err := s.store.GetStats()
	if err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"detail": err.Error()})
		return
	}
	c.JSON(http.StatusOK, gin.H{
		"status":     "ok",
		"version":    config.Version,
		"llm":        s.deps.HasKey(),
		"embeddings": s.deps.HasKey() && stats.Embedded,
		"docs":       stats.Docs,
		"chunks":     stats.Chunks,
		"budget":     gin.H{"used": s.budget.Used(), "limit": s.budget.Limit()},
	})
}

func (s *Server) listDocs(c *gin.Context) {
	docs, err := s.store.ListDocs()
	if err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"detail": err.Error()})
		return
	}
	if docs == nil {
		docs = []rag.DocInfo{}
	}
	c.JSON(http.StatusOK, docs)
}
