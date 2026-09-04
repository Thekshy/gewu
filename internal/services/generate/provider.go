package generate

import (
	"bufio"
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/url"
	"strings"
	"time"

	generatev1 "gewu/pkg/gen/gewu/generate/v1"

	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"
)

// provider OpenAI 兼容端点访问层——从冻结单体 internal/llm/client.go 拷贝改造：
//   - 参数与解析逐字一致（PARITY §13：json_mode、thinking 私有参数、max_tokens
//     缺省 2048、模型分层、SSE 手工解析、[DONE] 干净终止、心跳行静默跳过）；
//   - 预算从进程内文件计量改为 budgetMeter（本服务集中计量）；
//   - 新增受限重试：仅「连接失败且请求未发出」重试 1 次，流式不重试（ADR）。
type provider struct {
	cfg    Config
	meter  *budgetMeter
	http   *http.Client
	hasKey bool
}

func newProvider(cfg Config, meter *budgetMeter) *provider {
	return &provider{
		cfg:    cfg,
		meter:  meter,
		http:   &http.Client{Transport: &http.Transport{MaxIdleConnsPerHost: 8}},
		hasKey: cfg.APIKey != "",
	}
}

// requestTimeout 与冻结单体一致；基线评测出现过 200s 级端点抖动，不能更短。
const requestTimeout = 10 * time.Minute

// thinking 智谱私有参数，OpenAI 等端点不识别会报错，故按配置开关。
type thinking struct {
	Type string `json:"type"`
}

type chatRequest struct {
	Model          string      `json:"model"`
	Messages       []*genMsg   `json:"messages"`
	Temperature    float64     `json:"temperature"`
	MaxTokens      int         `json:"max_tokens"`
	Stream         bool        `json:"stream,omitempty"`
	ResponseFormat *respFormat `json:"response_format,omitempty"`
	Thinking       *thinking   `json:"thinking,omitempty"`
}

type genMsg struct {
	Role    string `json:"role"`
	Content string `json:"content"`
}

type respFormat struct {
	Type string `json:"type"`
}

type chatResponse struct {
	Choices []struct {
		Message struct {
			Content string `json:"content"`
		} `json:"message"`
	} `json:"choices"`
	Usage *struct {
		TotalTokens int64 `json:"total_tokens"`
	} `json:"usage"`
}

type embedRequest struct {
	Model string   `json:"model"`
	Input []string `json:"input"`
}

type embedResponse struct {
	Data []struct {
		Index     int       `json:"index"`
		Embedding []float64 `json:"embedding"`
	} `json:"data"`
	Usage *struct {
		TotalTokens int64 `json:"total_tokens"`
	} `json:"usage"`
}

// streamChunk 流式补全的单个 SSE 增量。
type streamChunk struct {
	Choices []struct {
		Delta struct {
			Content string `json:"content"`
		} `json:"delta"`
	} `json:"choices"`
}

func errNoKey() error {
	return status.Error(codes.FailedPrecondition, "未配置 LLM_API_KEY，无法调用模型")
}

// model 选模型：small=辅助小模型（路由/拆解/抽槽/改写），否则主模型。
func (p *provider) model(small bool) string {
	if small {
		return p.cfg.SmallModel
	}
	return p.cfg.MainModel
}

// url 拼接端点地址（base 以/结尾或未带/均可）。
func (p *provider) url(path string) string {
	return strings.TrimRight(p.cfg.BaseURL, "/") + path
}

// toMessages proto 消息 → 请求体（跳过空消息）。
func toMessages(msgs []*generatev1.Message) []*genMsg {
	out := make([]*genMsg, 0, len(msgs))
	for _, m := range msgs {
		if m == nil {
			continue
		}
		out = append(out, &genMsg{Role: m.Role, Content: m.Content})
	}
	return out
}

// buildBody 组装补全请求体（温度/max_tokens 取显式值；max_tokens<=0 补 2048）。
func (p *provider) buildBody(messages []*genMsg, o *generatev1.Options, stream bool) chatRequest {
	maxTokens := 2048
	jsonMode := false
	var temp float64
	small := false
	if o != nil {
		if o.MaxTokens > 0 {
			maxTokens = int(o.MaxTokens)
		}
		jsonMode = o.JsonMode
		temp = o.Temperature
		small = o.Small
	}
	body := chatRequest{
		Model:       p.model(small),
		Messages:    messages,
		Temperature: temp,
		MaxTokens:   maxTokens,
		Stream:      stream,
	}
	if jsonMode {
		body.ResponseFormat = &respFormat{Type: "json_object"}
	}
	if p.cfg.DisableThinking {
		body.Thinking = &thinking{Type: "disabled"}
	}
	return body
}

// chat 同步补全；实际用量计入预算。调用方（orchestrator/rag）各自降级。
func (p *provider) chat(ctx context.Context, messages []*generatev1.Message, o *generatev1.Options) (string, error) {
	if !p.hasKey {
		return "", errNoKey()
	}
	if err := p.meter.ensure(); err != nil {
		return "", err
	}
	body := p.buildBody(toMessages(messages), o, false)
	var resp chatResponse
	if err := p.postJSON(ctx, p.url("/chat/completions"), body, &resp); err != nil {
		return "", err
	}
	if resp.Usage != nil {
		p.meter.add(resp.Usage.TotalTokens)
	}
	if len(resp.Choices) == 0 {
		return "", wrapInternal(fmt.Errorf("模型响应缺少 choices"))
	}
	return resp.Choices[0].Message.Content, nil
}

// chatStream 流式补全：每个文本增量回调 onDelta（返回 error 时中断），
// 结束后按「字符数/2」保守估算入账（rune 计数，与冻结单体一致）。
// 无论正常结束还是中途断开（ctx 取消/回调报错），已产出的增量都要入账
// （客户端断开不烧后续 token，但已产出部分照记——冻结单体的修复行为）。
func (p *provider) chatStream(ctx context.Context, messages []*generatev1.Message, o *generatev1.Options, onDelta func(string) error) error {
	if !p.hasKey {
		return errNoKey()
	}
	if err := p.meter.ensure(); err != nil {
		return err
	}
	body := p.buildBody(toMessages(messages), o, true)
	// 流式只用于主答案生成，永远主模型（PARITY §13）
	ctx, cancel := context.WithTimeout(ctx, requestTimeout)
	defer cancel()

	req, err := p.newRequest(ctx, http.MethodPost, p.url("/chat/completions"), body)
	if err != nil {
		return err
	}
	resp, err := p.http.Do(req)
	if err != nil {
		return wrapInternal(fmt.Errorf("LLM 流式请求失败: %w", err))
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		b, _ := io.ReadAll(io.LimitReader(resp.Body, 4096))
		return wrapInternal(fmt.Errorf("LLM 流式请求 HTTP %d: %s", resp.StatusCode, strings.TrimSpace(string(b))))
	}

	var runes int64
	account := func() { p.meter.add(maxI64(1, runes/2)) }
	var cbErr error // 下游断开（onDelta 报错）——原样透传，不包装（与冻结单体一致）
	err = scanSSE(resp.Body, func(data []byte) error {
		if string(data) == "[DONE]" {
			return io.EOF // 约定：scanSSE 把它当作干净终止
		}
		var chunk streamChunk
		if err := json.Unmarshal(data, &chunk); err != nil {
			return nil // 兼容端点插入的注释/心跳行
		}
		if len(chunk.Choices) > 0 {
			text := chunk.Choices[0].Delta.Content
			if text != "" {
				runes += int64(len([]rune(text)))
				if err := onDelta(text); err != nil {
					cbErr = err
					return err
				}
			}
		}
		return nil
	})
	account()
	if err != nil && !errors.Is(err, io.EOF) {
		if cbErr != nil {
			return cbErr
		}
		return wrapInternal(err)
	}
	return nil
}

// embed 文本向量化（调用方自行做归一化），结果按输入顺序返回。
func (p *provider) embed(ctx context.Context, texts []string) ([][]float64, error) {
	if !p.hasKey {
		return nil, errNoKey()
	}
	if err := p.meter.ensure(); err != nil {
		return nil, err
	}
	var resp embedResponse
	err := p.postJSON(ctx, p.url("/embeddings"), embedRequest{Model: p.cfg.EmbedModel, Input: texts}, &resp)
	if err != nil {
		return nil, err
	}
	if resp.Usage != nil {
		p.meter.add(resp.Usage.TotalTokens)
	} else {
		var total int64
		for _, t := range texts {
			total += int64(len([]rune(t)))
		}
		p.meter.add(total / 2)
	}
	out := make([][]float64, len(texts))
	for _, d := range resp.Data {
		if d.Index >= 0 && d.Index < len(out) {
			out[d.Index] = d.Embedding
		}
	}
	return out, nil
}

// postJSON 发送 JSON 请求并把响应解析到 out；受限重试（仅连接失败且请求未发出）。
// body 为结构体——由 newRequest 统一 marshal 一次（[]byte 二次 marshal 会变 base64）。
func (p *provider) postJSON(ctx context.Context, u string, body any, out any) error {
	ctx, cancel := context.WithTimeout(ctx, requestTimeout)
	defer cancel()
	do := func() error {
		req, err := p.newRequest(ctx, http.MethodPost, u, body)
		if err != nil {
			return err
		}
		resp, err := p.http.Do(req)
		if err != nil {
			return err
		}
		defer resp.Body.Close()
		if resp.StatusCode != http.StatusOK {
			b, _ := io.ReadAll(io.LimitReader(resp.Body, 4096))
			return fmt.Errorf("LLM 请求 HTTP %d: %s", resp.StatusCode, strings.TrimSpace(string(b)))
		}
		if err := json.NewDecoder(resp.Body).Decode(out); err != nil {
			return fmt.Errorf("解析 LLM 响应失败: %w", err)
		}
		return nil
	}
	err := do()
	if err != nil && isDialFailure(err) {
		err = do() // ADR：连接失败（请求未发出）重试 1 次；流式不走此路径
	}
	return wrapInternal(err)
}

// isDialFailure 判定「连接失败且请求未发出」：TCP 拨号阶段失败。
func isDialFailure(err error) bool {
	var ue *url.Error
	if !errors.As(err, &ue) {
		return false
	}
	var oe *net.OpError
	return errors.As(ue.Err, &oe) && oe.Op == "dial"
}

// wrapInternal 保持错误文案并映射为 gRPC 状态（ctx 取消/超时保持原语义）。
func wrapInternal(err error) error {
	if err == nil {
		return nil
	}
	if errors.Is(err, context.Canceled) {
		return status.Error(codes.Canceled, err.Error())
	}
	if errors.Is(err, context.DeadlineExceeded) {
		return status.Error(codes.DeadlineExceeded, err.Error())
	}
	return status.Error(codes.Internal, err.Error())
}

func (p *provider) newRequest(ctx context.Context, method, u string, body any) (*http.Request, error) {
	buf, err := json.Marshal(body)
	if err != nil {
		return nil, err
	}
	req, err := http.NewRequestWithContext(ctx, method, u, bytes.NewReader(buf))
	if err != nil {
		return nil, err
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Authorization", "Bearer "+p.cfg.APIKey)
	return req, nil
}

// scanSSE 逐行解析 SSE：提取 data: 负载并回调；回调返回 io.EOF 表示正常终止。
func scanSSE(r io.Reader, onData func([]byte) error) error {
	br := bufio.NewReaderSize(r, 32*1024)
	for {
		line, err := br.ReadString('\n')
		if line != "" {
			trimmed := strings.TrimRight(line, "\r\n")
			if strings.HasPrefix(trimmed, "data:") {
				data := strings.TrimSpace(strings.TrimPrefix(trimmed, "data:"))
				if data != "" {
					if cbErr := onData([]byte(data)); cbErr != nil {
						return cbErr
					}
				}
			}
		}
		if err != nil {
			if errors.Is(err, io.EOF) {
				return nil
			}
			return err
		}
	}
}

func maxI64(a, b int64) int64 {
	if a > b {
		return a
	}
	return b
}
