package rag

import (
	"context"
	"fmt"
	"sort"

	client "github.com/milvus-io/milvus-sdk-go/v2/client"
	"github.com/milvus-io/milvus-sdk-go/v2/entity"

	"gewu/internal/rag"
)

// milvusVectorStore Milvus standalone 默认实现（ADR）：
//   - FLAT 精确索引 + IP 度量；入库前 L2 归一化 ⇒ IP = 余弦，与冻结单体
//     暴力余弦数学等价；
//   - 检索结果在本侧按 (score, chunk_id) 稳定化重排——Milvus 并列分数的
//     返回序不保证按 id，重排保证与单体「平局按 chunk id 升序」逐位一致。
const milvusCollection = "gewu_kb_chunks"

type milvusVectorStore struct {
	cli client.Client
}

func openMilvusVectorStore(addr string) (*milvusVectorStore, error) {
	cli, err := client.NewClient(context.Background(), client.Config{Address: addr})
	if err != nil {
		return nil, fmt.Errorf("连接 Milvus 失败: %w", err)
	}
	return &milvusVectorStore{cli: cli}, nil
}

// Name 实现名。
func (m *milvusVectorStore) Name() string { return "milvus-flat" }

// ensureCollection 建集合（首向量定型维度）与 FLAT/IP 索引。
func (m *milvusVectorStore) ensureCollection(ctx context.Context, dim int) error {
	has, err := m.cli.HasCollection(ctx, milvusCollection)
	if err != nil {
		return err
	}
	if has {
		return nil
	}
	schema := entity.NewSchema().WithName(milvusCollection).WithAutoID(false).
		WithField(entity.NewField().WithName("chunk_id").
			WithDataType(entity.FieldTypeInt64).WithIsPrimaryKey(true)).
		WithField(entity.NewField().WithName("embedding").
			WithDataType(entity.FieldTypeFloatVector).WithDim(int64(dim)))
	if err := m.cli.CreateCollection(ctx, schema, 1 /*shard*/); err != nil {
		return err
	}
	idx, err := entity.NewIndexFlat(entity.IP) // FLAT 精确索引 + IP 度量
	if err != nil {
		return err
	}
	return m.cli.CreateIndex(ctx, milvusCollection, "embedding", idx, false)
}

// Upsert 写入（幂等重导的旧向量删除由调用方先行 DeleteChunks）。
func (m *milvusVectorStore) Upsert(ctx context.Context, ids []int64, vectors [][]float64) error {
	if len(ids) == 0 {
		return nil
	}
	if err := m.ensureCollection(ctx, len(vectors[0])); err != nil {
		return err
	}
	idsCol := entity.NewColumnInt64("chunk_id", ids)
	vecs := make([][]float32, len(vectors))
	for i, v := range vectors {
		vecs[i] = make([]float32, len(v))
		for j, x := range v {
			vecs[i][j] = float32(x)
		}
	}
	vecCol := entity.NewColumnFloatVector("embedding", len(vectors[0]), vecs)
	if _, err := m.cli.Insert(ctx, milvusCollection, "", idsCol, vecCol); err != nil {
		return err
	}
	return m.cli.Flush(ctx, milvusCollection, false)
}

// Search IP 检索 + (score, id) 稳定化重排。
func (m *milvusVectorStore) Search(ctx context.Context, query []float64, k int) ([]rag.Scored, error) {
	has, err := m.cli.HasCollection(ctx, milvusCollection)
	if err != nil || !has {
		return nil, err
	}
	q := make([]float32, len(query))
	for i, x := range query {
		q[i] = float32(x)
	}
	sp, _ := entity.NewIndexFlatSearchParam()
	results, err := m.cli.Search(ctx, milvusCollection, nil, "",
		[]string{"chunk_id"}, []entity.Vector{entity.FloatVector(q)}, "embedding",
		entity.IP, k, sp)
	if err != nil {
		return nil, err
	}
	out := make([]rag.Scored, 0, k)
	for _, res := range results {
		for i := 0; i < res.ResultCount; i++ {
			id, err := res.IDs.Get(i)
			if err != nil {
				continue
			}
			out = append(out, rag.Scored{ID: int(id.(int64)), Score: float64(res.Scores[i])})
		}
	}
	// 稳定化重排：Milvus 并列分数的返回序不保证按 id（P3 逐位一致的关键）
	sort.Slice(out, func(i, j int) bool {
		if out[i].Score != out[j].Score {
			return out[i].Score > out[j].Score
		}
		return out[i].ID < out[j].ID
	})
	if len(out) > k {
		out = out[:k]
	}
	return out, nil
}

// Has 是否存在向量。
func (m *milvusVectorStore) Has(ctx context.Context) (bool, error) {
	has, err := m.cli.HasCollection(ctx, milvusCollection)
	if err != nil || !has {
		return false, err
	}
	stats, err := m.cli.GetCollectionStatistics(ctx, milvusCollection)
	if err != nil {
		return false, err
	}
	return stats["row_count"] != "0", nil
}

// DeleteChunks 按主键删除。
func (m *milvusVectorStore) DeleteChunks(ctx context.Context, ids []int64) error {
	has, err := m.cli.HasCollection(ctx, milvusCollection)
	if err != nil || !has {
		return err
	}
	return m.cli.DeleteByPks(ctx, milvusCollection, "chunk_id", entity.NewColumnInt64("chunk_id", ids))
}

// Drop 清空集合（rebuild 用）。
func (m *milvusVectorStore) Drop(ctx context.Context) error {
	has, err := m.cli.HasCollection(ctx, milvusCollection)
	if err != nil {
		return err
	}
	if has {
		return m.cli.DropCollection(ctx, milvusCollection)
	}
	return nil
}
