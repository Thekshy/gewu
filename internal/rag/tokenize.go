// Package rag 实现检索层：语料入库、BM25（中文字符二元语法）+ 向量余弦混合检索、RRF 融合。
//
// 设计取舍：校园知识库规模在千级 chunk，暴力余弦足够快，因此不引入外部向量数据库，
// 换取零部署依赖。所有 SQL 均为静态语句 + 参数绑定。
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

// dedupKeepOrder 去重并保持首次出现顺序（BM25 查询侧、向量去重等场景）。
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
