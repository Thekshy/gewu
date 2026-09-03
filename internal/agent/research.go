package agent

import (
	"context"
	"fmt"
	"log"
	"strings"

	"gewu/internal/llm"
	"gewu/internal/rag"
)

// Deep Research 链路：拆解子问题 → 逐路检索 → 证据聚合去重 → 交叉综合作答（PARITY §7）。
const (
	maxSubquestions   = 4  // 子问题上限
	maxEvidence       = 12 // 证据条数上限，控制综合阶段的上下文长度
	evidenceTextLimit = 600
)

// plan LLM 拆解子问题；无 key 时退化为原问题单路检索。
func (d *Deps) plan(ctx context.Context, question string) []string {
	if d.LLM == nil || !d.LLM.HasKey() {
		return []string{question}
	}
	raw, err := d.LLM.Chat(ctx, []llm.Message{
		{Role: "system", Content: PlannerSystem},
		{Role: "user", Content: question},
	}, llm.Options{JSONMode: true, Temperature: 0, MaxTokens: 400, Small: true})
	if err == nil {
		if obj, perr := parseJSONObject(raw); perr == nil {
			if subs := jsonStrSlice(obj, "subquestions"); len(subs) > 0 {
				if len(subs) > maxSubquestions {
					subs = subs[:maxSubquestions]
				}
				return subs
			}
		}
	} else {
		log.Printf("[agent] 子问题拆解失败，退化为单路检索：%v", err)
	}
	return []string{question}
}

// RunResearch 产出事件流：status / step* → answer_delta* → citations。
func (d *Deps) RunResearch(ctx context.Context, emit emitFn, question string, k int) error {
	if k <= 0 {
		k = 5
	}
	if err := emit(statusEvt("正在拆解问题…")); err != nil {
		return err
	}
	subquestions := d.plan(ctx, question)

	pool := map[int]rag.Hit{} // chunk_id → Hit，跨子问题去重
	var order []int           // 到达顺序
	for i, sub := range subquestions {
		hits, err := d.Retriever.Search(ctx, sub, k)
		if err != nil {
			return err
		}
		var titles []string
		seen := map[string]bool{}
		for _, h := range firstN(hits, 3) {
			if !seen[h.Title] {
				seen[h.Title] = true
				titles = append(titles, h.Title)
			}
		}
		if err := emit(stepEvt(i+1, sub, titles)); err != nil {
			return err
		}
		for _, h := range hits {
			if _, ok := pool[h.ChunkID]; !ok {
				pool[h.ChunkID] = h
				order = append(order, h.ChunkID)
			}
		}
	}

	if len(order) == 0 {
		if err := emit(answerEvt(NoDataAnswer)); err != nil {
			return err
		}
		return emit(citationsEvt(nil))
	}

	var blocks []string
	citations := make([]Citation, 0, len(order))
	for n, cid := range firstN(order, maxEvidence) {
		h := pool[cid]
		blocks = append(blocks, fmt.Sprintf("[%d] 《%s》（来源：%s）\n%s", n+1, h.Title, h.Source, truncate(h.Text, evidenceTextLimit)))
		citations = append(citations, Citation{N: n + 1, DocID: h.DocID, Title: h.Title, Source: h.Source})
	}

	if err := emit(statusEvt(fmt.Sprintf("共检索到 %d 条证据，正在交叉验证与综合…", len(order)))); err != nil {
		return err
	}

	if d.LLM == nil || !d.LLM.HasKey() {
		head := strings.Join(firstN(blocks, 3), "\n\n")
		text := fmt.Sprintf("%s\n\n围绕 %d 个子问题共检索到 %d 条相关段落，节选：\n\n%s",
			DemoModeNote, len(subquestions), len(order), head)
		if err := emit(answerEvt(text)); err != nil {
			return err
		}
		return emit(citationsEvt(citations))
	}

	messages := []llm.Message{
		{Role: "system", Content: AnswerSystem},
		{Role: "user", Content: fmt.Sprintf("参考资料：\n\n%s\n\n问题：%s", strings.Join(blocks, "\n"), question)},
	}
	streamErr := d.LLM.ChatStream(ctx, messages, llm.Options{}, func(text string) error {
		return emit(answerEvt(text))
	})
	if streamErr != nil {
		return streamErr
	}
	return emit(citationsEvt(citations))
}
