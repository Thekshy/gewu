package rag

import (
	"context"

	"gewu/internal/rag"
)

// VectorStore 向量存储接口（ADR：默认 Milvus FLAT；资源受限降级 PG 余弦）。
// 语义对齐冻结单体的暴力余弦：L2 归一化后点积，平局按 chunk id 升序
// （Milvus 返回后由本侧按 (score, id) 稳定化重排，保证逐位一致）。
type VectorStore interface {
	// Upsert 写入一批向量（调用方已 L2 归一化；ids 与 vecs 一一对应）。
	Upsert(ctx context.Context, ids []int64, vectors [][]float64) error
	// Search 余弦检索前 k（已按 (score, chunk_id) 稳定排序）。
	Search(ctx context.Context, query []float64, k int) ([]rag.Scored, error)
	// Has 库中是否存在向量。
	Has(ctx context.Context) (bool, error)
	// DeleteChunks 删除指定 chunk 的向量（幂等重导时清理旧向量）。
	DeleteChunks(ctx context.Context, ids []int64) error
	// Drop 清空全部向量（rebuild 用）。
	Drop(ctx context.Context) error
	// Name 实现名（日志/报告标注用）。
	Name() string
}
