package agent

import (
	"context"
	"fmt"
	"strings"

	"gewu/internal/llm"
	"gewu/internal/rag"
)

// numberedContext 命中列表 → 编号上下文与引用列表。
func numberedContext(hits []rag.Hit) (string, []Citation) {
	var lines []string
	citations := make([]Citation, 0, len(hits))
	for i, h := range hits {
		lines = append(lines, fmt.Sprintf("[%d] 《%s》（来源：%s）\n%s", i+1, h.Title, h.Source, h.Text))
		citations = append(citations, Citation{N: i + 1, DocID: h.DocID, Title: h.Title, Source: h.Source})
	}
	return strings.Join(lines, "\n\n"), citations
}

// AnswerDirect RAG 直答：单轮混合检索 → 带引用流式生成。
// 产出事件流：answer_delta* → citations。
func (d *Deps) AnswerDirect(ctx context.Context, emit emitFn, question string, k int) error {
	hits, err := d.Retriever.Search(ctx, question, k)
	if err != nil {
		return err
	}
	if len(hits) == 0 {
		if err := emit(answerEvt(NoDataAnswer)); err != nil {
			return err
		}
		return emit(citationsEvt(nil))
	}

	context, citations := numberedContext(hits)

	if d.LLM == nil || !d.LLM.HasKey() {
		// 演示模式：返回检索节选
		var excerpts []string
		for i, h := range firstN(hits, 3) {
			excerpts = append(excerpts, fmt.Sprintf("[%d] 《%s》：%s…", i+1, h.Title, truncate(h.Text, 180)))
		}
		if err := emit(answerEvt(DemoModeNote + "\n\n" + strings.Join(excerpts, "\n\n"))); err != nil {
			return err
		}
		return emit(citationsEvt(citations))
	}

	messages := []llm.Message{
		{Role: "system", Content: AnswerSystem},
		{Role: "user", Content: fmt.Sprintf("参考资料：\n\n%s\n\n问题：%s", context, question)},
	}
	streamErr := d.LLM.ChatStream(ctx, messages, llm.Options{}, func(text string) error {
		return emit(answerEvt(text))
	})
	if streamErr != nil {
		return streamErr
	}
	return emit(citationsEvt(citations))
}

// truncate 按 rune 截断。
func truncate(s string, n int) string {
	r := []rune(s)
	if len(r) <= n {
		return s
	}
	return string(r[:n])
}

func firstN[T any](list []T, n int) []T {
	if len(list) <= n {
		return list
	}
	return list[:n]
}
