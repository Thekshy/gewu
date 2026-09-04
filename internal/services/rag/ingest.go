package rag

import (
	"context"
	"fmt"
	"path/filepath"
	"sort"
	"strings"
	"time"

	"github.com/redis/go-redis/v9"

	"gewu/internal/rag"

	ragv1 "gewu/pkg/gen/gewu/rag/v1"
)

// 摄入流水线（Redis Streams，ADR）：
//   jobs 流（gewu:ingest:jobs，consumer group rag-ingest）——任务发布
//   done 流（gewu:ingest:done）——完成回执；Ingest RPC 阻塞等待全部回执
//
// `make ingest-ms` 的阻塞语义（发布→等回执→超时报错）由此保证——
// 评测与 A/B 对照需要确定性的索引状态。embed 失败一次 → 该 run 全程降级
// 仅 BM25（PARITY §15 降级语义）。

const (
	ingestJobsStream = "gewu:ingest:jobs"
	ingestDoneStream = "gewu:ingest:done"
	ingestGroup      = "rag-ingest"
	ingestTimeout    = 10 * time.Minute
)

// ingest 阻塞式全量入库（gRPC Ingest）：corpus 目录 → 发布任务 → 等回执。
func (s *Server) Ingest(req *ragv1.IngestRequest, stream ragv1.RagService_IngestServer) error {
	ctx := stream.Context()
	files, err := filepath.Glob(filepath.Join(s.cfg.CorpusDir, "*.md"))
	if err != nil {
		return errStatus(err)
	}
	sort.Strings(files)
	if len(files) == 0 {
		return errStatus(fmt.Errorf("未找到语料文件：%s/*.md", s.cfg.CorpusDir))
	}
	if req.GetRebuild() {
		if err := s.store.Reset(ctx); err != nil {
			return errStatus(err)
		}
		if err := s.vectors.Drop(ctx); err != nil {
			return errStatus(err)
		}
		_ = stream.Send(&ragv1.IngestResponse{Line: "已清空旧索引，开始重建…"})
	}

	runID := fmt.Sprintf("run-%d", time.Now().UnixNano())
	for _, f := range files {
		if err := s.rdb.XAdd(ctx, &redis.XAddArgs{
			Stream: ingestJobsStream,
			Values: map[string]any{
				"run_id": runID, "path": f,
				"no_embed": fmt.Sprintf("%v", req.GetNoEmbed()),
			},
		}).Err(); err != nil {
			return errStatus(err)
		}
	}

	deadline := time.Now().Add(ingestTimeout)
	received := 0
	for received < len(files) {
		remain := time.Until(deadline)
		if remain <= 0 {
			return errStatus(fmt.Errorf("入库超时：%d/%d 完成", received, len(files)))
		}
		// "$" = 只等新回执（不重放历史）
		res, err := s.rdb.XRead(ctx, &redis.XReadArgs{
			Streams: []string{ingestDoneStream, "$"},
			Count:   1, Block: remain,
		}).Result()
		if err != nil || len(res) == 0 || len(res[0].Messages) == 0 {
			continue // 块超时或空读，外层 deadline 兜底
		}
		msg := res[0].Messages[0]
		if msg.Values["run_id"] != runID {
			continue // 其他 run 的回执（单实例下不会出现，防御）
		}
		received++
		line := fmt.Sprintf("  ✓ %s  %v 块  [%v]", msg.Values["doc_id"], msg.Values["chunks"], msg.Values["mode"])
		if err := stream.Send(&ragv1.IngestResponse{Line: line}); err != nil {
			return err
		}
	}

	st, err := s.store.Stats(ctx)
	if err != nil {
		return errStatus(err)
	}
	embedded := false
	if s.gen.HasKey() && !req.GetNoEmbed() {
		if has, _ := s.vectors.Has(ctx); has {
			embedded = true
		}
	}
	s.log.Info("入库完成", zapI64("docs", int64(st.Docs)), zapI64("chunks", int64(st.Chunks)))
	return stream.Send(&ragv1.IngestResponse{
		Done: true, Embedded: embedded,
		Stats: &ragv1.StatsResponse{Docs: int32(st.Docs), Chunks: int32(st.Chunks), Embedded: embedded},
	})
}

// upload 异步上传（新增演示面）：写 uploads 目录并发布摄入任务，立即返回。
func (s *Server) Upload(ctx context.Context, req *ragv1.UploadRequest) (*ragv1.UploadResponse, error) {
	name := filepath.Base(req.GetFilename())
	if name == "" || name == "." || !strings.HasSuffix(name, ".md") {
		return nil, errStatus(fmt.Errorf("仅支持 .md 文件"))
	}
	dir := filepath.Join(s.cfg.DataDir, "uploads")
	path := filepath.Join(dir, name)
	if err := writeFile(path, req.GetContent()); err != nil {
		return nil, errStatus(err)
	}
	if err := s.rdb.XAdd(ctx, &redis.XAddArgs{
		Stream: ingestJobsStream,
		Values: map[string]any{
			"run_id": fmt.Sprintf("upload-%d", time.Now().UnixNano()), "path": path, "no_embed": "false",
		},
	}).Err(); err != nil {
		return nil, errStatus(err)
	}
	return &ragv1.UploadResponse{DocId: strings.TrimSuffix(name, ".md")}, nil
}

// consumeIngest 消费循环（服务启动时拉起，ctx 结束退出）。
func (s *Server) consumeIngest(ctx context.Context) {
	// 建组（已存在则忽略）
	_ = s.rdb.XGroupCreateMkStream(ctx, ingestJobsStream, ingestGroup, "0").Err()
	degradedRuns := map[string]bool{}
	for {
		if ctx.Err() != nil {
			return
		}
		res, err := s.rdb.XReadGroup(ctx, &redis.XReadGroupArgs{
			Group: ingestGroup, Consumer: "rag-1",
			Streams: []string{ingestJobsStream, ">"},
			Count:   4, Block: 5 * time.Second,
		}).Result()
		if err != nil || len(res) == 0 {
			continue
		}
		for _, msg := range res[0].Messages {
			s.processIngestJob(ctx, msg, degradedRuns)
			_ = s.rdb.XAck(ctx, ingestJobsStream, ingestGroup, msg.ID).Err()
		}
	}
}

// processIngestJob 单文档摄入：解析→切分→（可选）embed→入库→回执。
func (s *Server) processIngestJob(ctx context.Context, msg redis.XMessage, degradedRuns map[string]bool) {
	runID := str(msg.Values["run_id"])
	path := str(msg.Values["path"])
	noEmbed := str(msg.Values["no_embed"]) == "true"

	doc, err := rag.ParseDoc(path)
	if err != nil {
		s.log.Error("摄入解析失败", zapStr("path", path), zapErr(err))
		s.xDone(ctx, runID, filepath.Base(path), 0, "失败")
		return
	}
	chunks := rag.ChunkText(doc.Text, 450, 80)
	docID := strings.TrimSuffix(filepath.Base(path), ".md")
	title := doc.Meta["title"]
	if title == "" {
		title = docID
	}

	var vectors [][]float64
	useEmbed := !noEmbed && s.gen.HasKey() && !degradedRuns[runID]
	if useEmbed && len(chunks) > 0 {
		vectors, err = rag.EmbedBatched(ctx, s.gen, chunks)
		if err != nil {
			// 端点不支持/无额度：本 run 后续文档不再重试（PARITY §15）
			s.log.Warn("向量化不可用，本批全程降级为仅 BM25 索引", zapErr(err))
			degradedRuns[runID] = true
			vectors = nil
		}
	}

	oldIDs, newIDs, err := s.store.UpsertDoc(ctx, docID, title, doc.Meta["source"], doc.Meta["updated"], chunks)
	if err != nil {
		s.log.Error("摄入入库失败", zapStr("doc", docID), zapErr(err))
		s.xDone(ctx, runID, docID, 0, "失败")
		return
	}
	if len(oldIDs) > 0 {
		if err := s.vectors.DeleteChunks(ctx, oldIDs); err != nil {
			s.log.Warn("旧向量清理失败（幂等重导）", zapErr(err))
		}
	}
	if vectors != nil && len(newIDs) == len(vectors) {
		if err := s.vectors.Upsert(ctx, newIDs, vectors); err != nil {
			s.log.Warn("向量写入失败（BM25 已入库）", zapErr(err))
		}
	}

	mode := "仅BM25"
	if vectors != nil {
		mode = "BM25+向量"
	}
	s.xDone(ctx, runID, docID, len(chunks), mode)
}

// xDone 发布完成回执。
func (s *Server) xDone(ctx context.Context, runID, docID string, chunks int, mode string) {
	_ = s.rdb.XAdd(ctx, &redis.XAddArgs{
		Stream: ingestDoneStream,
		Values: map[string]any{
			"run_id": runID, "doc_id": docID,
			"chunks": fmt.Sprintf("%d", chunks), "mode": mode,
		},
	}).Err()
}

func str(v any) string {
	if s, ok := v.(string); ok {
		return s
	}
	return ""
}
