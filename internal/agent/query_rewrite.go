package agent

import (
	"context"
	"fmt"
	"log"
	"regexp"
	"strings"

	"gewu/internal/llm"
)

// P7+ 上下文补全（多轮指代消解）。
//
// 动机：路由（L0/L1/L2）与检索改写此前只吃裸问题——多轮追问"那第二个条件
// 是什么""我的情况符合吗"在路由和检索两侧双双失配（评测集全是单轮知识题，
// 这个盲区在旧门禁里不可见）。解法采用业界主流的"路由前补全"：小模型把
// follow-up 补全成自包含问题，补全结果贯通路由与检索两个环节（一次补全
// 双收益）；原始问题保留给记忆 episodic 存档与用户可见层。
//
// 触发门控（全部满足才调 LLM，缺一即原样返回，单轮会话零成本）：
//  1. QUERY_REWRITE 开启（默认 on，可一键回退）；
//  2. 有 LLM 且配了 key；
//  3. 本会话已有对话历史（episodic）；
//  4. 问题命中指代信号词（那/这/它/第二个/我的情况…）。
//
// 补全是增强不是依赖：解析失败/输出异常一律静默回退原问题。

// anaphoraRe 指代/省略信号词。偏召回：误命中只多花一次 flash 调用
// （补全器对自包含问题原样返回），漏命中则功能失效。
var anaphoraRe = regexp.MustCompile(`那|这|它|他|她|也|呢|上述|刚才|前面|上面|之前|第二|这种情况|我的情况|另外`)

// ResolveQuery 上下文补全入口。返回 (补全后的问题, 是否发生了补全)。
// 补全后的问题由 pipeline 贯通路由（decideRoute）与检索/生成（各 handler）。
func (d *Deps) ResolveQuery(ctx context.Context, question, userID, sessionID string) (string, bool) {
	if d.Settings == nil || d.Settings.QueryRewrite == "off" {
		return question, false
	}
	if d.LLM == nil || !d.LLM.HasKey() || d.Memory == nil {
		return question, false
	}
	if !anaphoraRe.MatchString(question) {
		return question, false
	}
	episodes, err := d.Memory.RecentEpisodes(sessionID, 6)
	if err != nil || len(episodes) == 0 {
		return question, false
	}

	var sb strings.Builder
	sb.WriteString("已知用户信息：\n")
	if facts, ferr := d.Memory.RecentFacts(userID, maxFactsInContext); ferr == nil && len(facts) > 0 {
		for _, f := range facts {
			fmt.Fprintf(&sb, "- %s/%s：%s\n", f.Kind, f.Key, f.Value)
		}
	} else {
		sb.WriteString("（暂无）\n")
	}
	sb.WriteString("最近对话：\n")
	for _, ep := range episodes {
		sb.WriteString(ep + "\n")
	}
	sb.WriteString("\n本轮问题：" + question)

	raw, err := d.LLM.Chat(ctx, []llm.Message{
		{Role: "system", Content: QueryRewriteSystem},
		{Role: "user", Content: sb.String()},
	}, llm.Options{JSONMode: true, Temperature: 0, MaxTokens: 200, Small: true})
	if err != nil {
		log.Printf("[agent] 上下文补全失败，使用原问题：%v", err)
		return question, false
	}
	obj, perr := parseJSONObject(raw)
	if perr != nil {
		log.Printf("[agent] 上下文补全输出非法 JSON，回退原问题：%s", truncate(raw, 120))
		return question, false
	}
	rewritten := strings.TrimSpace(jsonStr(obj, "rewritten"))
	if rewritten == "" || rewritten == question {
		if rewritten == "" {
			log.Printf("[agent] 上下文补全返回空，回退原问题")
		}
		return question, false
	}
	if n := len([]rune(rewritten)); n < 2 || n > d.Settings.MaxQuestionChars {
		log.Printf("[agent] 上下文补全输出长度异常（%d rune），回退原问题", n)
		return question, false
	}
	return rewritten, true
}
