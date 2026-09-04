package rag

import (
	"context"
	"log"
	"strings"
	"sync"

	"gewu/internal/llm"
)

// rewriteSystem 查询改写提示词：口语 → 政策术语检索串（逐字对照 Python 版）。
const rewriteSystem = `你是校园政策检索的查询改写器。把用户的口语化问题改写为适合关键词检索的查询串：
1. 保留核心实体（图书馆、转专业、奖学金、体测……）与数字；
2. 把口语说法换成政策文件用语（如：最多→上限，借书→外借 借阅，钱→元，挂科→不及格，发学位证→授予学位）；
3. 输出 10~25 个字的查询词串，不解释、不使用引号。
只输出改写后的查询串本身。`

// Rewriter 进程内查询改写器，带并发安全的缓存（导出供微服务 rag 复用——
// 决策 A 同族的接口化改动，行为零变化）。
type Rewriter struct {
	client LLMer

	mu    sync.RWMutex
	cache map[string]string
}

// NewRewriter 构造改写器。
func NewRewriter(client LLMer) *Rewriter {
	return &Rewriter{client: client, cache: map[string]string{}}
}

// Expand 返回「原查询 + 改写词串」（保召回）；失败或无 key 时返回原查询。
// 解决无向量检索时的词法失配：改写只做词面归一，不改变语义。
func (r *Rewriter) Expand(ctx context.Context, query string) string {
	if r.client == nil || !r.client.HasKey() {
		return query
	}
	r.mu.RLock()
	cached, ok := r.cache[query]
	r.mu.RUnlock()
	if ok {
		return cached
	}
	rewritten, err := r.client.Chat(ctx, []llm.Message{
		{Role: "system", Content: rewriteSystem},
		{Role: "user", Content: query},
	}, llm.Options{Temperature: 0, MaxTokens: 80, Small: true})
	if err != nil {
		log.Printf("[rag] 查询改写失败，使用原查询：%v", err)
		return query
	}
	rewritten = strings.Trim(rewritten, "\"“” \n\t")
	var result string
	if rewritten != "" {
		result = query + " " + rewritten
	} else {
		result = query
	}
	r.mu.Lock()
	r.cache[query] = result
	r.mu.Unlock()
	return result
}
