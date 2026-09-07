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
	"sync"
	"time"

	"gewu/internal/budget"
	"gewu/internal/config"
)

// ErrNoKey 未配置密钥（零 key 演示模式）时调用模型。
var ErrNoKey = errors.New("未配置 LLM_API_KEY，无法调用模型")

// Message 一条对话消息（system/user/assistant/tool）。
// ToolCalls / ToolCallID 仅供原生 tool-calling 多轮协议使用（agent-first 链路）：
// assistant 消息回填 tool_calls，tool 结果消息携带 tool_call_id。
type Message struct {
	Role       string        `json:"role"`
	Content    string        `json:"content"`
	ToolCalls  []ToolCallMsg `json:"tool_calls,omitempty"`
	ToolCallID string        `json:"tool_call_id,omitempty"`
}

// ToolDef 原生 tool-calling 的工具定义（OpenAI 兼容形状，GLM 支持）。
type ToolDef struct {
	Type     string       `json:"type"` // "function"
	Function ToolFunction `json:"function"`
}

// ToolFunction 工具的名称/描述/参数 JSON Schema。
type ToolFunction struct {
	Name        string         `json:"name"`
	Description string         `json:"description"`
	Parameters  map[string]any `json:"parameters"`
}

// ToolCallMsg assistant 消息里回填的工具调用（id 用于配对 tool 结果）。
type ToolCallMsg struct {
	ID       string `json:"id"`
	Type     string `json:"type"` // "function"
	Function struct {
		Name      string `json:"name"`
		Arguments string `json:"arguments"` // JSON 编码的参数字符串
	} `json:"function"`
}

// ToolCall ChatWithTools 解析后的单次调用（Arguments 已是字符串原文，调用方自行 Unmarshal）。
type ToolCall struct {
	ID        string
	Name      string
	Arguments string
}

// Completion ChatWithTools 的结果：文本与工具调用并存
// （模型可能在同一条消息里既说话又发起调用；无调用时 ToolCalls 为空，Content 即最终回答）。
// FinishReason 透传端点的结束原因（stop/length/tool_calls/content_filter…）：
// length 且带 ToolCalls 时参数可能不完整，调用方必须先于工具解析判断（P10）。
type Completion struct {
	Content      string
	ToolCalls    []ToolCall
	FinishReason string
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
	Tools          []ToolDef   `json:"tools,omitempty"`       // 原生 tool-calling（agent-first）
	ToolChoice     string      `json:"tool_choice,omitempty"` // 缺省 auto
}

type respFormat struct {
	Type string `json:"type"`
}

// usage 用量三元组（OpenAI 兼容）。记账口径不变：budget.Add 只吃 TotalTokens；
// prompt/completion 两元解析暴露，供成本分析留口（P10）。
type usage struct {
	PromptTokens     int64 `json:"prompt_tokens"`
	CompletionTokens int64 `json:"completion_tokens"`
	TotalTokens      int64 `json:"total_tokens"`
}

type chatResponse struct {
	Choices []struct {
		Message struct {
			Content   string        `json:"content"`
			ToolCalls []ToolCallMsg `json:"tool_calls"`
		} `json:"message"`
		// FinishReason 输出为何终止：stop（自然结束）/ length（撞 max_tokens 截断）/
		// tool_calls（正常发起调用）/ content_filter。缺字段端点为空串。
		FinishReason string `json:"finish_reason"`
	} `json:"choices"`
	Usage *usage `json:"usage"`
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
	return endpointURL(c.s.LLMBaseURL, path)
}

// endpointURL 按 base 拼路径（chat 与 embed 通道各自传 base）。
func endpointURL(base, path string) string {
	return strings.TrimRight(base, "/") + path
}

// embedBase embed 通道端点：EMBED_BASE_URL 优先，缺省回退 LLM_BASE_URL。
func (c *Client) embedBase() string {
	if c.s.EmbedBaseURL != "" {
		return c.s.EmbedBaseURL
	}
	return c.s.LLMBaseURL
}

// embedKey embed 通道密钥：EMBED_API_KEY 优先，缺省回退 LLM_API_KEY。
func (c *Client) embedKey() string {
	if c.s.EmbedAPIKey != "" {
		return c.s.EmbedAPIKey
	}
	return c.s.LLMKey
}

// HasEmbedKey embed 通道是否可用（ingest 启动校验用）。
func (c *Client) HasEmbedKey() bool { return c.embedKey() != "" }

// ChatWithTools 原生 tool-calling 补全：请求携带工具清单，响应解析 content 与
// tool_calls 并存。模型不再发起调用时 ToolCalls 为空——Content 即最终回答
// （agent 循环的唯一终止判据）。用量同样计入预算；不用 JSONMode
// （response_format 与 tools 在部分端点互斥）。
func (c *Client) ChatWithTools(ctx context.Context, messages []Message, o Options, tools []ToolDef) (*Completion, error) {
	if !c.HasKey() {
		return nil, ErrNoKey
	}
	if err := c.budget.Ensure(); err != nil {
		return nil, err
	}
	o.applyDefaults()
	body := chatRequest{
		Model:       c.model(o.Small),
		Messages:    messages,
		Temperature: o.Temperature,
		MaxTokens:   o.MaxTokens,
		Tools:       tools,
	}
	if c.s.LLMDisableThinking {
		body.Thinking = &thinking{Type: "disabled"}
	}
	var resp chatResponse
	if err := c.postJSON(ctx, c.url("/chat/completions"), body, &resp); err != nil {
		return nil, err
	}
	if resp.Usage != nil {
		c.budget.Add(resp.Usage.TotalTokens)
	}
	if len(resp.Choices) == 0 {
		return nil, fmt.Errorf("模型响应缺少 choices")
	}
	msg := resp.Choices[0].Message
	out := &Completion{Content: msg.Content, FinishReason: resp.Choices[0].FinishReason}
	for _, tc := range msg.ToolCalls {
		if tc.Function.Name == "" {
			continue // 端点偶发占位调用
		}
		out.ToolCalls = append(out.ToolCalls, ToolCall{
			ID:        tc.ID,
			Name:      tc.Function.Name,
			Arguments: tc.Function.Arguments,
		})
	}
	return out, nil
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

	req, err := c.newRequest(ctx, http.MethodPost, c.url("/chat/completions"), c.s.LLMKey, body)
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
	account := func() { c.budget.Add(max64(1, runes/2)) }
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
	// 无论正常结束还是中途断开（客户端取消/回调报错），已产出的增量都要入账，
	// 否则长回答被中断时这部分真实消耗会漏计。
	account()
	if err != nil && !errors.Is(err, io.EOF) {
		return err
	}
	return nil
}

// mmInputItem 火山多模态向量接口的 input 元素（非标准形状）。
type mmInputItem struct {
	Type string `json:"type"`
	Text string `json:"text"`
}

type mmEmbedRequest struct {
	Model          string        `json:"model"`
	EncodingFormat string        `json:"encoding_format"`
	Input          []mmInputItem `json:"input"`
}

// mmEmbedResponse 火山多模态接口响应：data 是对象（data.embedding），非数组，
// 且不支持批量（多个 text 只返回一个联合向量）——必须逐条请求。
type mmEmbedResponse struct {
	Data struct {
		Embedding []float64 `json:"embedding"`
	} `json:"data"`
	Usage *struct {
		TotalTokens int64 `json:"total_tokens"`
	} `json:"usage"`
}

// embedConcurrency 多模态逐条请求的并发度（火山限流友好，ingest 千级 chunk 足够）。
const embedConcurrency = 4

// embedTimeout 单条 embedding 请求的独立超时：小请求被卡不必等 chat 的 10 分钟。
const embedTimeout = 45 * time.Second

// Embed 文本向量化（调用方自行做归一化），结果按输入顺序返回。
// 双通道：EMBED_MODE=ark_multimodal 走火山 /embeddings/multimodal（逐条、并发 4、保序）；
// 其余（text/空）走标准 /embeddings 批量接口。chat 与 embed 的 base/key 相互独立，
// EMBED_* 缺省时回退 LLM_*。
func (c *Client) Embed(ctx context.Context, texts []string) ([][]float64, error) {
	if len(texts) == 0 {
		return nil, nil
	}
	if !c.HasEmbedKey() {
		return nil, ErrNoKey
	}
	if err := c.budget.Ensure(); err != nil {
		return nil, err
	}
	if c.s.EmbedMode == "ark_multimodal" {
		return c.embedMultimodal(ctx, texts)
	}
	return c.embedText(ctx, texts)
}

// embedText 标准 OpenAI 兼容批量向量化（按 data[].index 归位）。
func (c *Client) embedText(ctx context.Context, texts []string) ([][]float64, error) {
	var resp embedResponse
	err := c.postJSONWithKeyTimeout(ctx, c.url("/embeddings"), c.embedKey(),
		embedRequest{Model: c.s.EmbedModel, Input: texts}, &resp, embedTimeout)
	if err != nil {
		return nil, err
	}
	if resp.Usage != nil {
		c.budget.Add(resp.Usage.TotalTokens)
	} else {
		c.estimateEmbedUsage(texts)
	}
	out := make([][]float64, len(texts))
	for _, d := range resp.Data {
		if d.Index >= 0 && d.Index < len(out) {
			out[d.Index] = d.Embedding
		}
	}
	// 与 multimodal 通道一致：供应商漏回某个 index 时不静默，明确报错。
	for i, v := range out {
		if len(v) == 0 {
			return nil, fmt.Errorf("第 %d 条向量缺失（响应未返回 index=%d）", i, i)
		}
	}
	return out, nil
}

// embedMultimodal 火山 /embeddings/multimodal：接口不支持批量，逐条发请求。
//
// 固定 worker pool（在飞请求数 = embedConcurrency，与 len(texts) 无关——
// 千级 chunk 不会瞬间建上千个阻塞协程）；任一条失败记 firstErr 后立即 cancel
// 其余在途请求（首错即停，不再烧完整批配额）；按输入下标保序回填，
// 整体失败不产出半截结果。
func (c *Client) embedMultimodal(ctx context.Context, texts []string) ([][]float64, error) {
	out := make([][]float64, len(texts))
	ctx, cancel := context.WithCancel(ctx)
	defer cancel()
	var (
		wg       sync.WaitGroup
		mu       sync.Mutex
		firstErr error
		jobs     = make(chan int, len(texts)) // 带缓冲：worker 提前退出时投递不阻塞
	)
	for i := range texts {
		jobs <- i
	}
	close(jobs)
	worker := func() {
		defer wg.Done()
		for i := range jobs {
			if ctx.Err() != nil {
				return // 已有失败取消：快速退出，不再发新请求
			}
			var resp mmEmbedResponse
			err := c.postJSONWithKeyTimeout(ctx, endpointURL(c.embedBase(), "/embeddings/multimodal"), c.embedKey(),
				mmEmbedRequest{Model: c.s.EmbedModel, EncodingFormat: "float",
					Input: []mmInputItem{{Type: "text", Text: texts[i]}}}, &resp, embedTimeout)
			if err == nil && len(resp.Data.Embedding) == 0 {
				err = fmt.Errorf("向量响应为空")
			}
			if err != nil {
				err = fmt.Errorf("第 %d 条向量化失败: %w", i, err)
				mu.Lock()
				if firstErr == nil {
					firstErr = err
					cancel() // 首错即停
				}
				mu.Unlock()
				return
			}
			out[i] = resp.Data.Embedding // 各 worker 写互不相同的下标，无数据竞争
			if resp.Usage != nil {
				c.budget.Add(resp.Usage.TotalTokens)
			} else {
				c.estimateEmbedUsage([]string{texts[i]})
			}
		}
	}
	wg.Add(embedConcurrency)
	for w := 0; w < embedConcurrency; w++ {
		go worker()
	}
	wg.Wait()
	if firstErr != nil {
		return nil, firstErr
	}
	for i, v := range out {
		if len(v) == 0 {
			return nil, fmt.Errorf("第 %d 条向量响应为空", i)
		}
	}
	return out, nil
}

// estimateEmbedUsage 响应缺 usage 时按 rune 数/2 保守估算入账。
func (c *Client) estimateEmbedUsage(texts []string) {
	var total int64
	for _, t := range texts {
		total += int64(len([]rune(t)))
	}
	c.budget.Add(total / 2)
}

// postJSON 发送 JSON 请求并把响应解析到 out（chat 通道，LLM key，10min 超时）。
func (c *Client) postJSON(ctx context.Context, url string, body any, out any) error {
	return c.postJSONWithKeyTimeout(ctx, url, c.s.LLMKey, body, out, requestTimeout)
}

// postJSONWithKey 发送 JSON 请求并把响应解析到 out（key 由调用方指定，
// chat 传 LLMKey、embed 传 embedKey——两家供应商密钥互不相通）。
func (c *Client) postJSONWithKey(ctx context.Context, url, key string, body any, out any) error {
	return c.postJSONWithKeyTimeout(ctx, url, key, body, out, requestTimeout)
}

// postJSONWithKeyTimeout 带调用方指定超时的底层 POST：chat 长流式用
// requestTimeout(10min)，embedding 小请求用 embedTimeout(45s) 独立限时。
func (c *Client) postJSONWithKeyTimeout(ctx context.Context, url, key string, body any, out any, timeout time.Duration) error {
	ctx, cancel := context.WithTimeout(ctx, timeout)
	defer cancel()
	req, err := c.newRequest(ctx, http.MethodPost, url, key, body)
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

func (c *Client) newRequest(ctx context.Context, method, url, key string, body any) (*http.Request, error) {
	buf, err := json.Marshal(body)
	if err != nil {
		return nil, err
	}
	req, err := http.NewRequestWithContext(ctx, method, url, bytes.NewReader(buf))
	if err != nil {
		return nil, err
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Authorization", "Bearer "+key)
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
