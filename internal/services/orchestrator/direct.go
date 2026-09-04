package orchestrator

// RAG 直答与深度研究（拷贝改造自冻结 internal/agent/{direct,research}.go，
// PARITY §6/§7 逐字：编号上下文、演示文案、拆解参数、证据池去重与截断）。
// 检索经 rag 服务 RPC；LLM 经 generate。

import (
	"context"
	"fmt"
	"strings"

	generatev1 "gewu/pkg/gen/gewu/generate/v1"
	orchestratorv1 "gewu/pkg/gen/gewu/orchestrator/v1"

	"gewu/internal/agent"
	"gewu/internal/rag"

	"go.uber.org/zap"
)

// citation 编排侧引用。
type citation struct {
	N      int32
	DocID  string
	Title  string
	Source string
}

// numberedContext 命中列表 → 编号上下文与引用列表。
func numberedContext(hits []rag.Hit) (string, []citation) {
	var lines []string
	citations := make([]citation, 0, len(hits))
	for i, h := range hits {
		lines = append(lines, fmt.Sprintf("[%d] 《%s》（来源：%s）\n%s", i+1, h.Title, h.Source, h.Text))
		citations = append(citations, citation{N: int32(i + 1), DocID: h.DocID, Title: h.Title, Source: h.Source})
	}
	return strings.Join(lines, "\n\n"), citations
}

// toProtoCitations 引用列表 → 事件。
func toProtoCitations(cs []citation) []*orchestratorv1.Citation {
	out := make([]*orchestratorv1.Citation, 0, len(cs))
	for _, c := range cs {
		out = append(out, &orchestratorv1.Citation{N: c.N, DocId: c.DocID, Title: c.Title, Source: c.Source})
	}
	return out
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

// answerDirect RAG 直答（PARITY §6）：单轮混合检索 → 带引用流式生成。
func (s *Server) answerDirect(ctx context.Context, emit emitFn, question string, k int, hasKey bool) error {
	hits := s.ragSearch(ctx, question, k)
	if len(hits) == 0 {
		if err := emit(answerEvent(agent.NoDataAnswer)); err != nil {
			return err
		}
		return emit(citationsEvent(nil))
	}

	contextText, citations := numberedContext(hits)

	if !hasKey {
		// 演示模式：返回检索节选（DEMO_MODE_NOTE 逐字）
		var excerpts []string
		for i, h := range firstN(hits, 3) {
			excerpts = append(excerpts, fmt.Sprintf("[%d] 《%s》：%s…", i+1, h.Title, truncate(h.Text, 180)))
		}
		if err := emit(answerEvent(agent.DemoModeNote + "\n\n" + strings.Join(excerpts, "\n\n"))); err != nil {
			return err
		}
		return emit(citationsEvent(toProtoCitations(citations)))
	}

	messages := []*generatev1.Message{
		{Role: "system", Content: s.cfgStore.answerPrompt(ctx)},
		{Role: "user", Content: fmt.Sprintf("参考资料：\n\n%s\n\n问题：%s", contextText, question)},
	}
	if err := s.streamAnswer(ctx, emit, messages); err != nil {
		return err
	}
	return emit(citationsEvent(toProtoCitations(citations)))
}

// streamAnswer 主答案流式（generate ChatStream → answer_delta*）。
func (s *Server) streamAnswer(ctx context.Context, emit emitFn, messages []*generatev1.Message) error {
	gs, err := s.generate.ChatStream(ctx, &generatev1.ChatStreamRequest{Messages: messages})
	if err != nil {
		return err
	}
	for {
		delta, err := gs.Recv()
		if err != nil {
			if err == errEOF {
				return nil
			}
			return err
		}
		if delta.GetText() != "" {
			if err := emit(answerEvent(delta.GetText())); err != nil {
				return err
			}
		}
	}
}

// runResearch 深度研究（PARITY §7）：
// MAX_SUBQUESTIONS=4、每路 k=5、MAX_EVIDENCE=12、单条证据截 600 rune。
func (s *Server) runResearch(ctx context.Context, emit emitFn, question string, hasKey bool) error {
	const (
		k                 = 5
		maxSubquestions   = 4
		maxEvidence       = 12
		evidenceTextLimit = 600
	)
	if err := emit(statusEvent("正在拆解问题…")); err != nil {
		return err
	}
	subquestions := s.plan(ctx, question, hasKey)

	pool := map[int]rag.Hit{} // chunk_id → Hit，跨子问题去重
	var order []int           // 到达顺序
	for i, sub := range subquestions {
		hits := s.ragSearch(ctx, sub, k)
		var titles []string
		seen := map[string]bool{}
		for _, h := range firstN(hits, 3) {
			if !seen[h.Title] {
				seen[h.Title] = true
				titles = append(titles, h.Title)
			}
		}
		if err := emit(stepEvent(int32(i+1), sub, titles)); err != nil {
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
		if err := emit(answerEvent(agent.NoDataAnswer)); err != nil {
			return err
		}
		return emit(citationsEvent(nil))
	}

	var blocks []string
	citations := make([]citation, 0, len(order))
	for n, cid := range firstN(order, maxEvidence) {
		h := pool[cid]
		blocks = append(blocks, fmt.Sprintf("[%d] 《%s》（来源：%s）\n%s", n+1, h.Title, h.Source, truncate(h.Text, evidenceTextLimit)))
		citations = append(citations, citation{N: int32(n + 1), DocID: h.DocID, Title: h.Title, Source: h.Source})
	}

	if err := emit(statusEvent(fmt.Sprintf("共检索到 %d 条证据，正在交叉验证与综合…", len(order)))); err != nil {
		return err
	}

	if !hasKey {
		head := strings.Join(firstN(blocks, 3), "\n\n")
		text := fmt.Sprintf("%s\n\n围绕 %d 个子问题共检索到 %d 条相关段落，节选：\n\n%s",
			agent.DemoModeNote, len(subquestions), len(order), head)
		if err := emit(answerEvent(text)); err != nil {
			return err
		}
		return emit(citationsEvent(toProtoCitations(citations)))
	}

	messages := []*generatev1.Message{
		{Role: "system", Content: s.cfgStore.answerPrompt(ctx)},
		{Role: "user", Content: fmt.Sprintf("参考资料：\n\n%s\n\n问题：%s", strings.Join(blocks, "\n"), question)},
	}
	if err := s.streamAnswer(ctx, emit, messages); err != nil {
		return err
	}
	return emit(citationsEvent(toProtoCitations(citations)))
}

// plan LLM 拆解子问题；无 key 时退化为原问题单路检索。
func (s *Server) plan(ctx context.Context, question string, hasKey bool) []string {
	if !hasKey {
		return []string{question}
	}
	raw, err := s.generate.Chat(ctx, &generatev1.ChatRequest{
		Messages: []*generatev1.Message{
			{Role: "system", Content: s.cfgStore.plannerPrompt(ctx)},
			{Role: "user", Content: question},
		},
		Options: &generatev1.Options{JsonMode: true, Temperature: 0, MaxTokens: 400, Small: true},
	})
	if err == nil {
		if obj, perr := parseJSONObject(raw.GetContent()); perr == nil {
			if subs := jsonStrSlice(obj, "subquestions"); len(subs) > 0 {
				if len(subs) > maxSubquestions {
					subs = subs[:maxSubquestions]
				}
				return subs
			}
		}
	} else {
		s.log.Warn("子问题拆解失败，退化为单路检索", zap.Error(err))
	}
	return []string{question}
}

const maxSubquestions = 4
