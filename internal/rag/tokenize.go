// Package rag 实现检索层：语料入库、关键词检索（PG 原生 FTS，中文二元语法分词
// 下沉为 schema.go 的 rag_tokenize SQL 函数）+ pgvector halfvec HNSW 向量检索，
// RRF 融合。Tokenize 是 rag_tokenize 的 Go 参考实现（语义集合等价，单测契约）。
package rag

import (
	"regexp"
	"strings"
)

var (
	latinRe = regexp.MustCompile(`[a-zA-Z0-9]+`)
	hanRe   = regexp.MustCompile(`[\x{4e00}-\x{9fff}]`)
)

// Tokenize 英文/数字按词、中文按字符二元语法：免去分词依赖，中文召回效果可用。
// 与 Python 版语义一致：先按出现顺序收集拉丁词（小写），再拼接全部汉字的相邻二元组。
func Tokenize(text string) []string {
	var tokens []string
	for _, m := range latinRe.FindAllString(text, -1) {
		tokens = append(tokens, strings.ToLower(m))
	}
	han := hanRe.FindAllString(text, -1)
	for i := 0; i+1 < len(han); i++ {
		tokens = append(tokens, han[i]+han[i+1])
	}
	return tokens
}

// dedupKeepOrder 去重并保持首次出现顺序（测试对照 rag_tokenize、通用去重场景）。
func dedupKeepOrder(tokens []string) []string {
	seen := make(map[string]struct{}, len(tokens))
	out := tokens[:0:0]
	for _, t := range tokens {
		if _, ok := seen[t]; ok {
			continue
		}
		seen[t] = struct{}{}
		out = append(out, t)
	}
	return out
}
