// Package llm 实现统一封装的 OpenAI 兼容访问层（智谱 / DeepSeek / OpenAI / vLLM 直换）。
//
// 不引入 SDK 与框架：非流式与流式补全、向量化只依赖 net/http + encoding/json，
// 流式响应用 bufio 手工解析 SSE 行，配合调用方传入的 context 实现取消传播。
package llm

import (
	"bufio"
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"strings"
	"time"

	"gewu/internal/budget"
	"gewu/internal/config"
)

// ErrNoKey 未配置密钥（零 key 演示模式）时调用模型。
var ErrNoKey = errors.New("未配置 LLM_API_KEY，无法调用模型")

// Message 一条对话消息（system/user/assistant）。
type Message struct {
	Role    string `json:"role"`
	Content string `json:"content"`
}

// Options 单次补全的调节参数。
type Options struct {
	JSONMode    bool
	Temperature float64
	MaxTokens   int
	Small       bool // true=辅助调用走小模型（路由/抽取/改写等）
}

// applyDefaults 零值语义与 Python 版缺省一致：max_tokens=2048。
// （OpenAI 兼容端点普遍拒绝 max_tokens=0，必须显式给值。）
func (o *Options) applyDefaults() {
	if o.MaxTokens <= 0 {
		o.MaxTokens = 2048
	}
}

// Client OpenAI 兼容端点客户端，进程内共享。
type Client struct {
	s      *config.Settings
	budget *budget.TokenBudget
	http   *http.Client
}

// New 构造客户端；无 key 时仍可构造（HasKey=false），调用时报 ErrNoKey。
func New(s *config.Settings, b *budget.TokenBudget) *Client {
	return &Client{
		s:      s,
		budget: b,
		http:   &http.Client{Transport: &http.Transport{MaxIdleConnsPerHost: 8}},
	}
}

// HasKey 是否配置了密钥（决定零 key 降级路径）。
func (c *Client) HasKey() bool { return c.s.LLMKey != "" }

// model 选模型：small=true 用小模型，主答案用主模型（模型分层见 PARITY §13）。
func (c *Client) model(small bool) string {
	if small {
		return c.s.LLMSmallModel
	}
	return c.s.LLMModel
}

// thinking 是智谱私有参数，OpenAI 等端点不识别会报错，故按配置开关。
type thinking struct {
	Type string `json:"type"`
}

type chatRequest struct {
	Model          string      `json:"model"`
	Messages       []Message   `json:"messages"`
	Temperature    float64     `json:"temperature"`
	MaxTokens      int         `json:"max_tokens"`
	Stream         bool        `json:"stream,omitempty"`
	ResponseFormat *respFormat `json:"response_format,omitempty"`
	Thinking       *thinking   `json:"thinking,omitempty"`
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

// requestTimeout 与 openai SDK 缺省一致；基线评测出现过 200s 级端点抖动，不能更短。
const requestTimeout = 10 * time.Minute

// url 拼接端点地址（base 以/结尾或未带/均可）。
func (c *Client) url(path string) string {
	return strings.TrimRight(c.s.LLMBaseURL, "/") + path
}

// Chat 同步补全，返回完整文本；实际用量计入每日预算。
func (c *Client) Chat(ctx context.Context, messages []Message, o Options) (string, error) {
	if !c.HasKey() {
		return "", ErrNoKey
	}
	if err := c.budget.Ensure(); err != nil {
		return "", err
	}
	o.applyDefaults()
	body := chatRequest{
		Model:       c.model(o.Small),
		Messages:    messages,
		Temperature: o.Temperature,
		MaxTokens:   o.MaxTokens,
	}
	if o.JSONMode {
		body.ResponseFormat = &respFormat{Type: "json_object"}
	}
	if c.s.LLMDisableThinking {
		body.Thinking = &thinking{Type: "disabled"}
	}
	var resp chatResponse
	if err := c.postJSON(ctx, c.url("/chat/completions"), body, &resp); err != nil {
		return "", err
	}
	if resp.Usage != nil {
		c.budget.Add(resp.Usage.TotalTokens)
	}
	if len(resp.Choices) == 0 {
		return "", fmt.Errorf("模型响应缺少 choices")
	}
	return resp.Choices[0].Message.Content, nil
}

// ChatStream 流式补全：每个文本增量回调 onDelta（返回 error 时中断），
// 结束后按「字符数/2」保守估算入账（rune 计数，与 Python len() 一致）。
// ctx 取消（客户端断开）会立刻中止上游读取。
func (c *Client) ChatStream(ctx context.Context, messages []Message, o Options, onDelta func(string) error) error {
	if !c.HasKey() {
		return ErrNoKey
	}
	if err := c.budget.Ensure(); err != nil {
		return err
	}
	o.applyDefaults()
	body := chatRequest{
		Model:       c.model(false), // 流式只用于主答案生成，永远主模型
		Messages:    messages,
		Temperature: o.Temperature,
		MaxTokens:   o.MaxTokens,
		Stream:      true,
	}
	if c.s.LLMDisableThinking {
		body.Thinking = &thinking{Type: "disabled"}
	}
	ctx, cancel := context.WithTimeout(ctx, requestTimeout)
	defer cancel()

	req, err := c.newRequest(ctx, http.MethodPost, c.url("/chat/completions"), body)
	if err != nil {
		return err
	}
	resp, err := c.http.Do(req)
	if err != nil {
		return fmt.Errorf("LLM 流式请求失败: %w", err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		b, _ := io.ReadAll(io.LimitReader(resp.Body, 4096))
		return fmt.Errorf("LLM 流式请求 HTTP %d: %s", resp.StatusCode, strings.TrimSpace(string(b)))
	}

	var runes int64
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
					return err
				}
			}
		}
		return nil
	})
	if err != nil && !errors.Is(err, io.EOF) {
		return err
	}
	c.budget.Add(max64(1, runes/2))
	return nil
}

// Embed 文本向量化（调用方自行做归一化），结果按输入顺序返回。
func (c *Client) Embed(ctx context.Context, texts []string) ([][]float64, error) {
	if !c.HasKey() {
		return nil, ErrNoKey
	}
	if err := c.budget.Ensure(); err != nil {
		return nil, err
	}
	var resp embedResponse
	err := c.postJSON(ctx, c.url("/embeddings"), embedRequest{Model: c.s.EmbedModel, Input: texts}, &resp)
	if err != nil {
		return nil, err
	}
	if resp.Usage != nil {
		c.budget.Add(resp.Usage.TotalTokens)
	} else {
		var total int64
		for _, t := range texts {
			total += int64(len([]rune(t)))
		}
		c.budget.Add(total / 2)
	}
	out := make([][]float64, len(texts))
	for _, d := range resp.Data {
		if d.Index >= 0 && d.Index < len(out) {
			out[d.Index] = d.Embedding
		}
	}
	return out, nil
}

// postJSON 发送 JSON 请求并把响应解析到 out。
func (c *Client) postJSON(ctx context.Context, url string, body any, out any) error {
	ctx, cancel := context.WithTimeout(ctx, requestTimeout)
	defer cancel()
	req, err := c.newRequest(ctx, http.MethodPost, url, body)
	if err != nil {
		return err
	}
	resp, err := c.http.Do(req)
	if err != nil {
		return fmt.Errorf("LLM 请求失败: %w", err)
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

func (c *Client) newRequest(ctx context.Context, method, url string, body any) (*http.Request, error) {
	buf, err := json.Marshal(body)
	if err != nil {
		return nil, err
	}
	req, err := http.NewRequestWithContext(ctx, method, url, bytes.NewReader(buf))
	if err != nil {
		return nil, err
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Authorization", "Bearer "+c.s.LLMKey)
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

func max64(a, b int64) int64 {
	if a > b {
		return a
	}
	return b
}
