package rag

import (
	"math"
	"sort"
)

// BM25Index 进程内倒排索引（从 Store 的私有实现提取为导出类型——公式与
// 行为逐字一致，供 SQLite Store 与微服务 rag（PG 数据源）共用同一份代码，
// 「行为对齐靠同一份代码」）。
//
// 参数：k1=1.5, b=0.75, idf=ln(1+(N-df+0.5)/(df+0.5))；查询侧 token 去重
// 后累计；doc_len 下限 1；平局按 chunk id 升序（构造性确定）。
type BM25Index struct {
	postings map[string]map[int]int // token -> chunk -> tf
	docLen   map[int]int
	idf      map[string]float64
	avgdl    float64
}

// NewBM25Index 空索引（AddDoc 累计 → Finalize 定型）。
func NewBM25Index() *BM25Index {
	return &BM25Index{
		postings: map[string]map[int]int{},
		docLen:   map[int]int{},
		idf:      map[string]float64{},
	}
}

// AddDoc 累计一个 chunk（同一 id 只能添加一次——调用方保证）。
func (b *BM25Index) AddDoc(chunkID int, text string) {
	tokens := Tokenize(text)
	if n := len(tokens); n < 1 {
		b.docLen[chunkID] = 1
	} else {
		b.docLen[chunkID] = n
	}
	counts := map[string]int{}
	for _, t := range tokens {
		counts[t]++
	}
	for t, tf := range counts {
		if b.postings[t] == nil {
			b.postings[t] = map[int]int{}
		}
		b.postings[t][chunkID] = tf
	}
}

// Finalize 计算 idf 与 avgdl（全部 AddDoc 完成后调用一次）。
func (b *BM25Index) Finalize() {
	var totalLen float64
	for _, l := range b.docLen {
		totalLen += float64(l)
	}
	n := len(b.docLen)
	if n == 0 {
		n = 1
	}
	b.avgdl = totalLen / float64(n)
	for t, plist := range b.postings {
		b.idf[t] = math.Log(1 + (float64(n)-float64(len(plist))+0.5)/(float64(len(plist))+0.5))
	}
}

// Search 返回按 BM25 得分降序的前 k 个 (chunkID, score)。
func (b *BM25Index) Search(query string, k int) []Scored {
	scores := map[int]float64{}
	const k1, bNorm = 1.5, 0.75
	for _, token := range dedupKeepOrder(Tokenize(query)) {
		plist, ok := b.postings[token]
		if !ok {
			continue
		}
		idf := b.idf[token]
		for cid, tf := range plist {
			denom := float64(tf) + k1*(1-bNorm+bNorm*float64(b.docLen[cid])/b.avgdl)
			scores[cid] += idf * float64(tf) * (k1 + 1) / denom
		}
	}
	out := make([]Scored, 0, len(scores))
	for cid, sc := range scores {
		out = append(out, Scored{ID: cid, Score: sc})
	}
	sort.Slice(out, func(i, j int) bool {
		if out[i].Score != out[j].Score {
			return out[i].Score > out[j].Score
		}
		return out[i].ID < out[j].ID // 平分按 chunk id 升序（Go 版确定化）
	})
	if len(out) > k {
		out = out[:k]
	}
	return out
}

// DocCount 已收录 chunk 数。
func (b *BM25Index) DocCount() int { return len(b.docLen) }
