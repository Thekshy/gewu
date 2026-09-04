// rag 检索服务入口：gRPC :9005（混合检索 + 摄入 + 记忆）。
// 阻塞式入库 CLI：rag -ingest [-no-embed] [-rebuild]（make ingest-ms）。
package main

import (
	"context"
	"flag"
	"fmt"
	"io"
	"os"

	"google.golang.org/grpc"

	"gewu/internal/services/rag"
	"gewu/internal/svcbase"
	ragv1 "gewu/pkg/gen/gewu/rag/v1"

	"go.uber.org/zap"
)

func main() {
	var (
		ingestOnly bool
		noEmbed    bool
		rebuild    bool
	)
	flag.BoolVar(&ingestOnly, "ingest", false, "阻塞式语料入库后退出（经服务流水线，等价 make ingest-ms）")
	flag.BoolVar(&noEmbed, "no-embed", false, "入库时只建 BM25 索引")
	flag.BoolVar(&rebuild, "rebuild", false, "入库前清空旧索引")
	flag.Parse()

	log := svcbase.NewLogger("rag")
	cfg := rag.LoadConfig()

	if ingestOnly {
		if err := runIngestCLI(cfg, log, noEmbed, rebuild); err != nil {
			fmt.Fprintf(os.Stderr, "入库失败：%v\n", err)
			os.Exit(1)
		}
		return
	}

	ctx, stop := svcbase.MainSignalContext()
	defer stop()
	srv, err := rag.NewServer(cfg, log)
	if err != nil {
		log.Fatal("构造检索服务失败", zap.Error(err))
	}
	defer srv.Close()
	err = svcbase.RunGRPC(ctx, svcbase.GRPCConfig{
		Name:      "rag",
		Addr:      cfg.Addr,
		AdminAddr: cfg.AdminAddr,
		Register: func(g *grpc.Server) {
			ragv1.RegisterRagServiceServer(g, srv)
		},
	}, log)
	if err != nil {
		log.Fatal("服务退出", zap.Error(err))
	}
}

// runIngestCLI 阻塞式入库客户端：发布后等待完成回执（服务侧超时报错）。
func runIngestCLI(cfg rag.Config, log *zap.Logger, noEmbed, rebuild bool) error {
	cc, err := svcbase.Dial(svcbase.NormalizeTarget(cfg.Addr))
	if err != nil {
		return err
	}
	defer cc.Close()
	stream, err := ragv1.NewRagServiceClient(cc).Ingest(context.Background(),
		&ragv1.IngestRequest{NoEmbed: noEmbed, Rebuild: rebuild})
	if err != nil {
		return err
	}
	embedded := false
	for {
		ev, err := stream.Recv()
		if err == io.EOF {
			break
		}
		if err != nil {
			return err
		}
		if ev.GetLine() != "" {
			fmt.Println(ev.GetLine())
		}
		if ev.GetDone() {
			embedded = ev.GetEmbedded()
			fmt.Printf("\n入库完成：%d 篇文档 / %d 个 chunk / 向量化=%v（经 Redis Streams 流水线）\n",
				ev.GetStats().GetDocs(), ev.GetStats().GetChunks(), embedded)
		}
	}
	return nil
}
