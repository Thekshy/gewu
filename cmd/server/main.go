// gewu-api：格物 API 服务入口。
//
// 用法：
//
//	gewu-api                    启动 HTTP 服务（:8000）
//	gewu-api -ingest            语料入库后退出（等价 make ingest）
//	gewu-api -ingest -no-embed  仅建 BM25 索引（显式手动选项）
//	gewu-api -ingest -rebuild   删除旧索引后重建
//
// P6 阶段0：强制有 key 启动——LLM_API_KEY 缺失直接 fatal，不再有零 key 演示模式。
package main

import (
	"context"
	"encoding/json"
	"flag"
	"fmt"
	"net/http"
	"os"
	"strings"

	"github.com/gin-gonic/gin"

	"gewu/internal/agent"
	"gewu/internal/budget"
	"gewu/internal/business"
	"gewu/internal/config"
	"gewu/internal/llm"
	"gewu/internal/middleware"
	"gewu/internal/rag"
)

func main() {
	var (
		ingestOnly bool
		noEmbed    bool
		rebuild    bool
		addr       string
	)
	flag.BoolVar(&ingestOnly, "ingest", false, "语料入库后退出（不启动服务）")
	flag.BoolVar(&noEmbed, "no-embed", false, "入库时只建 BM25 索引（显式手动选项）")
	flag.BoolVar(&rebuild, "rebuild", false, "入库前删除旧索引文件")
	flag.StringVar(&addr, "addr", ":8000", "HTTP 监听地址")
	flag.Parse()

	settings := config.Load()
	// 启动校验：强制有 key（P6 去无 key——不再演示模式启动）。
	if settings.LLMKey == "" {
		fmt.Fprintln(os.Stderr, "启动失败：未配置 LLM_API_KEY（P6 起强制有 key，无演示模式）")
		os.Exit(1)
	}
	tokenBudget := budget.New(settings.DataDir+"/usage.json", settings.DailyTokenBudget)
	llmClient := llm.New(settings, tokenBudget)

	if ingestOnly && !noEmbed && !llmClient.HasEmbedKey() {
		fmt.Fprintln(os.Stderr, "入库失败：未配置 EMBED_API_KEY/EMBED_BASE_URL（确要仅建 BM25 索引请显式加 -no-embed）")
		os.Exit(1)
	}

	if ingestOnly {
		st, path, err := rag.Ingest(context.Background(), settings, llmClient, rag.IngestOptions{NoEmbed: noEmbed, Rebuild: rebuild})
		if err != nil {
			fmt.Fprintf(os.Stderr, "入库失败：%v\n", err)
			os.Exit(1)
		}
		emb := "否"
		if st.Embedded {
			emb = "是"
		}
		fmt.Printf("\n入库完成：%d 篇文档 / %d 个 chunk（含父子块）/ 向量化=%s → %s\n", st.Docs, st.Chunks, emb, path)
		return
	}

	if err := run(settings, llmClient, tokenBudget, addr); err != nil {
		fmt.Fprintf(os.Stderr, "服务退出：%v\n", err)
		os.Exit(1)
	}
}

// server 聚合 HTTP 层依赖。
type server struct {
	deps     *agent.Deps
	store    *rag.Store
	budget   *budget.TokenBudget
	llm      *llm.Client
	settings *config.Settings
}

// run 装配依赖并启动 HTTP 服务。
func run(settings *config.Settings, llmClient *llm.Client, tokenBudget *budget.TokenBudget, addr string) error {
	store, err := rag.Open(settings.IndexPath)
	if err != nil {
		return err
	}
	defer store.Close()
	biz, err := business.Open(settings.DataDir + "/business.db")
	if err != nil {
		return err
	}
	defer biz.Close()
	mem, err := agent.OpenMemory(settings.DataDir + "/memory.db")
	if err != nil {
		return err
	}
	defer mem.Close()

	retriever := rag.NewRetriever(store, settings.RetrievalK, llmClient)
	if settings.RerankMode != "off" {
		retriever = retriever.WithReranker(rag.NewLLMReranker(llmClient))
	}
	deps := agent.NewDeps(settings, llmClient, retriever, biz, mem)

	srv := &server{deps: deps, store: store, budget: tokenBudget, llm: llmClient, settings: settings}

	if os.Getenv("GIN_MODE") == "" {
		gin.SetMode(gin.ReleaseMode)
	}
	router := srv.newRouter()
	fmt.Printf("格物 Gewu API %s 监听 %s（LLM %s，路由 %s，切分 %s，rerank %s，react %s，补全 %s）\n",
		config.Version, addr, llmState(llmClient), settings.RouterMode, settings.ChunkMode,
		settings.RerankMode, settings.ReactMode, settings.QueryRewrite)
	return router.Run(addr)
}

func llmState(c *llm.Client) string {
	if c != nil && c.HasKey() {
		return "启用"
	}
	return "未启用（零 key 演示模式）"
}

// newRouter 装配全部路由与中间件（顺序：限流最前，再 CORS）。
func (s *server) newRouter() *gin.Engine {
	r := gin.New()
	r.Use(gin.Logger(), gin.Recovery())
	r.Use(middleware.NewRateLimiter(s.settings.RateLimitPerMinute).Handler())
	r.Use(cors())

	r.GET("/api/health", s.health)
	r.GET("/api/docs", s.listDocs)
	r.POST("/api/search", s.search)
	r.POST("/api/chat", s.chat)
	r.POST("/api/business/reset", s.businessReset)
	r.GET("/api/business/overview", s.businessOverview)
	return r
}

// cors 允许任意 Origin/Method/Header（公开 demo）。
func cors() gin.HandlerFunc {
	return func(c *gin.Context) {
		c.Header("Access-Control-Allow-Origin", "*")
		c.Header("Access-Control-Allow-Methods", "*")
		c.Header("Access-Control-Allow-Headers", "*")
		if c.Request.Method == http.MethodOptions {
			c.AbortWithStatus(http.StatusNoContent)
			return
		}
		c.Next()
	}
}

// ---------- 处理器 ----------

func (s *server) health(c *gin.Context) {
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

func (s *server) listDocs(c *gin.Context) {
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

type searchRequest struct {
	Query string `json:"query"`
	K     *int   `json:"k"` // 指针区分「未提供（缺省 5）」与「显式 0（422）」，对齐 pydantic ge=1 语义
}

func (s *server) search(c *gin.Context) {
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

type chatRequest struct {
	Question  string `json:"question"`
	Mode      string `json:"mode"`
	SessionID string `json:"session_id"`
	Role      string `json:"role"`
}

func (s *server) chat(c *gin.Context) {
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

	// SSE：手工逐事件写入并立刻 flush；ctx 取消（客户端断开）时 emit 返回错误中止管线
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

func (s *server) businessReset(c *gin.Context) {
	if err := s.deps.Business.Reset(); err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"detail": err.Error()})
		return
	}
	c.JSON(http.StatusOK, gin.H{"status": "ok"})
}

func (s *server) businessOverview(c *gin.Context) {
	bookings, err := s.deps.Business.AllBookings()
	if err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"detail": err.Error()})
		return
	}
	tickets, err := s.deps.Business.AllTickets()
	if err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"detail": err.Error()})
		return
	}
	if bookings == nil {
		bookings = []business.BookingFull{}
	}
	if tickets == nil {
		tickets = []business.TicketView{}
	}
	c.JSON(http.StatusOK, gin.H{"bookings": bookings, "tickets": tickets})
}

// ---------- 辅助 ----------

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
