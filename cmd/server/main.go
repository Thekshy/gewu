// gewu-api：格物 API 服务入口（组装根：只做 flag/配置/装配/启动）。
//
// 用法：
//
//	gewu-api                    启动 HTTP 服务（:8000）
//	gewu-api -ingest            语料入库后退出（等价 make ingest）
//	gewu-api -ingest -no-embed  仅建 BM25 索引（显式手动选项）
//	gewu-api -ingest -rebuild   删除旧索引后重建
//
// HTTP 语义（路由/handler/SSE）在 internal/api；强制有 key 启动（P6 起，
// LLM_API_KEY 缺失直接 fatal，无演示模式）。
package main

import (
	"context"
	"flag"
	"fmt"
	"os"

	"gewu/internal/agent"
	"gewu/internal/api"
	"gewu/internal/budget"
	"gewu/internal/business"
	"gewu/internal/config"
	"gewu/internal/llm"
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
	if settings.LLMKey == "" {
		fmt.Fprintln(os.Stderr, "启动失败：未配置 LLM_API_KEY（P6 起强制有 key，无演示模式）")
		os.Exit(1)
	}
	tokenBudget := budget.New(settings.DataDir+"/usage.json", settings.DailyTokenBudget)
	llmClient := llm.New(settings, tokenBudget)

	if ingestOnly {
		if !noEmbed && !llmClient.HasEmbedKey() {
			fmt.Fprintln(os.Stderr, "入库失败：未配置 EMBED_API_KEY/EMBED_BASE_URL（确要仅建 BM25 索引请显式加 -no-embed）")
			os.Exit(1)
		}
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

// run 装配依赖（存储/检索/编排/会话）并启动 HTTP 服务。
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
	sessions, err := openSessions(settings)
	if err != nil {
		return err
	}
	defer sessions.Close()

	retriever := rag.NewRetriever(store, settings.RetrievalK, llmClient)
	if settings.RerankMode != "off" {
		retriever = retriever.WithReranker(rag.NewLLMReranker(llmClient))
	}
	deps := agent.NewDeps(settings, llmClient, retriever, biz, mem)
	deps.Sessions = sessions // 默认内存版替换为 SESSION_STORE 指定的后端

	srv := api.New(deps, store, tokenBudget, settings)
	fmt.Printf("格物 Gewu API %s 监听 %s（LLM %s，路由 %s，切分 %s，rerank %s，react %s，补全 %s，会话 %s）\n",
		config.Version, addr, llmState(llmClient), settings.RouterMode, settings.ChunkMode,
		settings.RerankMode, settings.ReactMode, settings.QueryRewrite, settings.SessionStore)
	return srv.Run(addr)
}

// openSessions 按 SESSION_STORE 构造会话后端（缺省 sqlite：办理流程跨重启续办）。
func openSessions(settings *config.Settings) (agent.SessionStore, error) {
	switch settings.SessionStore {
	case "", "sqlite":
		return agent.OpenSessionStore(settings.DataDir + "/sessions.db")
	case "memory":
		return agent.NewSessionStore(), nil
	default:
		return nil, fmt.Errorf("SESSION_STORE 必须为 sqlite/memory，当前：%s", settings.SessionStore)
	}
}

func llmState(c *llm.Client) string {
	if c != nil && c.HasKey() {
		return "启用"
	}
	return "未启用（零 key 演示模式）"
}
