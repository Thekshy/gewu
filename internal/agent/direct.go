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
// userID/sessionID 用于分层装配长期记忆（P6 阶段4）。
func (d *Deps) AnswerDirect(ctx context.Context, emit emitFn, question string, k int, userID, sessionID string) error {
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

	messages := d.assembleMessages(userID, sessionID, question, context)
	streamErr := d.LLM.ChatStream(ctx, messages, llm.Options{}, func(text string) error {
		return emit(answerEvt(text))
	})
	if streamErr != nil {
		return streamErr
	}
	return emit(citationsEvt(citations))
}

// assembleMessages 消息分层装配（P6 阶段4，顺序固定，稳定内容前置以利 prompt cache）：
//  1. system（引用式作答准则）
//  2. 长期记忆（用户事实 + 本会话近期对话要点）——仅当存在记忆数据
//  3. user（参考资料 + 问题）
//
// 无记忆数据时与历史版本逐字一致（两条消息），保证无记忆基线不回归。
// 每轮对话只装配一次（直答/深研每轮恰走其一）。
func (d *Deps) assembleMessages(userID, sessionID, question, context string) []llm.Message {
	msgs := []llm.Message{{Role: "system", Content: AnswerSystem}}
	if mem := d.memoryBlock(userID, sessionID); mem != "" {
		msgs = append(msgs, llm.Message{Role: "system", Content: "已知用户信息：\n" + mem})
	}
	msgs = append(msgs, llm.Message{
		Role:    "user",
		Content: fmt.Sprintf("参考资料：\n\n%s\n\n问题：%s", context, question),
	})
	return msgs
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
