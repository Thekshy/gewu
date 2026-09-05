package rag

import (
	"context"
	"fmt"
	"os"
	"path/filepath"
	"strings"

	"github.com/redis/go-redis/v9"

	ragv1 "gewu/pkg/gen/gewu/rag/v1"

	"gewu/internal/rag"
	"gewu/internal/svcbase"
	generatev1 "gewu/pkg/gen/gewu/generate/v1"

	"go.uber.org/zap"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"
)

// Server 检索服务：混合检索（PG chunk 元数据 + Milvus FLAT/PG 余弦向量）
// + Redis Streams 摄入流水线 + 查询改写（LLM 经 generate）。
type Server struct {
	ragv1.UnimplementedRagServiceServer

	log      *zap.Logger
	cfg      Config
	store    *pgStore
	vectors  VectorStore
	gen      *generateAdapter
	rewriter *rag.Rewriter
	rdb      *redis.Client
}

// NewServer 构造检索服务（PG 与 Redis 为必需依赖；Milvus 可选，缺省走 PG 余弦降级）。
func NewServer(cfg Config, log *zap.Logger) (*Server, error) {
	if cfg.PostgresDSN == "" {
		return nil, fmt.Errorf("rag 服务需要 POSTGRES_DSN（chunk 元数据存储）")
	}
	if cfg.RedisAddr == "" {
		return nil, fmt.Errorf("rag 服务需要 REDIS_ADDR（摄入流水线 Redis Streams）")
	}
	store, err := openPGStore(cfg.PostgresDSN)
	if err != nil {
		return nil, err
	}

	var vec VectorStore
	if cfg.MilvusAddr != "" {
		mv, err := openMilvusVectorStore(cfg.MilvusAddr)
		if err != nil {
			store.Close()
			return nil, err
		}
		vec = mv
		log.Info("向量存储：Milvus FLAT", zap.String("addr", cfg.MilvusAddr))
	} else {
		pv, err := openPGVectorStore(cfg.PostgresDSN)
		if err != nil {
			store.Close()
			return nil, err
		}
		vec = pv
		log.Warn("向量存储：PG 余弦降级实现（MILVUS_ADDR 未配置；ADR 备案）")
	}

	genCC, err := svcbase.Dial(cfg.GenerateAddr)
	if err != nil {
		store.Close()
		return nil, err
	}
	gen := newGenerateAdapter(generatev1.NewGenerateServiceClient(genCC))

	rdb := redis.NewClient(&redis.Options{Addr: cfg.RedisAddr})
	if err := rdb.Ping(context.Background()).Err(); err != nil {
		store.Close()
		return nil, fmt.Errorf("连接 Redis 失败: %w", err)
	}

	s := &Server{
		log: log, cfg: cfg,
		store:    store,
		vectors:  vec,
		gen:      gen,
		rewriter: rag.NewRewriter(gen),
		rdb:      rdb,
	}
	go s.consumeIngest(context.Background())
	return s, nil
}

// Close 释放资源。
func (s *Server) Close() {
	_ = s.store.Close()
}

// Search 混合检索。
func (s *Server) Search(ctx context.Context, req *ragv1.SearchRequest) (*ragv1.SearchResponse, error) {
	hits, err := s.search(ctx, req.GetQuery(), int(req.GetK()))
	if err != nil {
		return nil, errStatus(err)
	}
	out := make([]*ragv1.Hit, 0, len(hits))
	for _, h := range hits {
		out = append(out, &ragv1.Hit{
			ChunkId: int32(h.ChunkID), DocId: h.DocID, Seq: int32(h.Seq),
			Text: h.Text, Title: h.Title, Source: h.Source,
		})
	}
	return &ragv1.SearchResponse{Hits: out}, nil
}

// ListDocs 已入库文档列表。
func (s *Server) ListDocs(ctx context.Context, req *ragv1.ListDocsRequest) (*ragv1.ListDocsResponse, error) {
	docs, err := s.store.ListDocs(ctx)
	if err != nil {
		return nil, errStatus(err)
	}
	out := make([]*ragv1.DocInfo, 0, len(docs))
	for _, d := range docs {
		out = append(out, &ragv1.DocInfo{
			DocId: d.DocID, Title: d.Title, Source: d.Source, Updated: d.Updated, Chunks: int32(d.Chunks),
		})
	}
	return &ragv1.ListDocsResponse{Docs: out}, nil
}

// Stats 索引规模。
func (s *Server) Stats(ctx context.Context, req *ragv1.StatsRequest) (*ragv1.StatsResponse, error) {
	st, err := s.store.Stats(ctx)
	if err != nil {
		return nil, errStatus(err)
	}
	embedded := false
	if has, err := s.vectors.Has(ctx); err == nil {
		embedded = has
	}
	return &ragv1.StatsResponse{Docs: int32(st.Docs), Chunks: int32(st.Chunks), Embedded: embedded}, nil
}

// MemoryPut 写入长期记忆（P5；不接入答案生成，ADR-0005）。
func (s *Server) MemoryPut(ctx context.Context, req *ragv1.MemoryPutRequest) (*ragv1.MemoryPutResponse, error) {
	text := strings.TrimSpace(req.GetText())
	if l := len([]rune(text)); l < 1 || l > 2000 {
		return nil, status.Error(codes.InvalidArgument, "text 长度需在 1~2000 字之间")
	}
	if req.GetSessionId() == "" {
		return nil, status.Error(codes.InvalidArgument, "session_id 不能为空")
	}
	id, err := s.memoryPut(ctx, req.GetSessionId(), text)
	if err != nil {
		return nil, errStatus(err)
	}
	return &ragv1.MemoryPutResponse{Id: id}, nil
}

// MemoryRecall 语义检索记忆（向量可用走余弦，否则 recency 降级）。
func (s *Server) MemoryRecall(ctx context.Context, req *ragv1.MemoryRecallRequest) (*ragv1.MemoryRecallResponse, error) {
	query := strings.TrimSpace(req.GetQuery())
	if l := len([]rune(query)); l < 1 || l > 500 {
		return nil, status.Error(codes.InvalidArgument, "query 长度需在 1~500 字之间")
	}
	if req.GetSessionId() == "" {
		return nil, status.Error(codes.InvalidArgument, "session_id 不能为空")
	}
	k := int(req.GetK())
	if k <= 0 {
		k = 5
	}
	if k > 20 {
		k = 20
	}
	items, err := s.memoryRecall(ctx, req.GetSessionId(), query, k)
	if err != nil {
		return nil, errStatus(err)
	}
	out := make([]*ragv1.MemoryItem, 0, len(items))
	for _, m := range items {
		out = append(out, &ragv1.MemoryItem{Id: m.ID, Text: m.Text, Score: m.Score})
	}
	return &ragv1.MemoryRecallResponse{Items: out}, nil
}

// ---------- 小辅助 ----------

func errStatus(err error) error {
	return status.Error(codes.Internal, "rag 存储错误: "+err.Error())
}

func writeFile(path string, content []byte) error {
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		return err
	}
	return os.WriteFile(path, content, 0o644)
}

func zapErr(err error) zap.Field           { return zap.Error(err) }
func zapStr(key, val string) zap.Field     { return zap.String(key, val) }
func zapI64(key string, v int64) zap.Field { return zap.Int64(key, v) }
