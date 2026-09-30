# P10 LLM 响应侧收口 · finish_reason 与截断防御（技术方案 + 分阶段提示词）

> 背景：对照 DeepSeek Harness / Pi 的 Agent Loop 终止理论（终止不是一个布尔条件，而是
> 分层判断 + 债务模型），盘点 gewu 的 LLM 访问层与 ReAct 引擎，确认三个缺口：
>
> 1. **`chatResponse` 不解析 `finish_reason`**——非流式带 tools 的调用（`ChatWithTools`，
>    ReAct 唯一入口）若输出撞 max_tokens 截断，`tool_calls` 里可能携带不完整 JSON 参数，
>    而当前逻辑是"有 tool_calls 就执行"（react.go:94），截断参数会被真的跑（读操作带空
>    参数执行，写操作侥幸被确认流挡住）。理论铁律：**max-tokens 判断必须先于工具解析**。
> 2. **流式路径对截断零感知**——`ChatStream` 只解析 `delta.content`，主答案（直答/深研，
>    默认 MaxTokens=2048）撞线截断时前端只看到回答戛然而止，无任何标记。
> 3. **`done` 事件无结束原因**——completed/error/aborted（客户端断开）在事件流里不可
>    区分，评测与监控无法统计截断率。
>
> 消息模型的「形状」本身是标准的 OpenAI 兼容最小集（tool_calls 配对/tools 嵌套/
> response_format 均按规范），本阶段只补响应侧字段，不动请求侧协议。
>
> 原则：
> 1. **判断顺序铁律**：截断（finish_reason=length）且带 tool_calls 时，一律不执行任何
>    调用——参数可能不完整，执行会产生真实副作用；
> 2. **修复策略选 Pi 式回填**（合成错误 observation 交回模型重发），与既有"错误回填
>    observation 不断链"哲学一致；不用 Harness 式整轮收口（gewu 无外部续行驱动）；
> 3. **SSE 契约只增不改**：`done` 加可选 `reason` 字段（omitempty，老消费者无感知），
>    其余事件形状一律不动；评测脚本只读 `latency_ms`，零改动；
> 4. 预算记账口径不变（仍按 total_tokens 入账；usage 三元组仅解析暴露，为成本分析留口）；
> 5. 门禁不降级：build/vet/test 全绿 + lint-arch 通过 + 28 题 cascade 无回归 +
>    agent-first 8 题对照基线（失败集 ⊆ 已知 flaky 集，见 eval/reports/P8-retire-baseline.md
>    §4.3）+ 报告留档 eval/reports/。

---

## 0. 现状与缺口决策表

| 位置 | 现状 | 决策 |
|---|---|---|
| `chatResponse`（llm/client.go:138） | 只有 message.content/tool_calls + usage.total_tokens | 补 `finish_reason`；usage 扩三元组（记账不动） |
| `Completion`（llm/client.go:69） | Content + ToolCalls | 补 `FinishReason string` |
| `Chat` 返回值 | string | 不动（路由/抽取/改写等辅助调用无需 finish） |
| `ChatWithTools`（llm/client.go:210） | 不透传 finish | 透传 `FinishReason` |
| `streamChunk`（llm/client.go:166） | 只有 delta.content | 补末 chunk 的 finish_reason；`ChatStream` 签名改返回 `(string, error)` |
| RunReAct 判断顺序（react.go:89-119） | 有 tool_calls 即执行 | length 且带 tool_calls → 全部不执行，合成 observation 回填，`continue` |
| `doneEvent`（agent/events.go:113） | type + latency_ms | 补 `reason`（completed/error/aborted/max_tokens，omitempty） |
| done 的发射点 | runChatInner 各路径 + RunChat 兜底各发各的 | 收口到 RunChat 单点（一次 chat 恰一个 done，reason 统一计算） |
| ReAct 截断后是否置位 truncated | — | 不置位（有自我修复，最终仍收敛出答案），记为设计决策 |
| eval/run_eval.py | 只读 latency_ms（L192-194） | 零改动 |

## 1. 设计要点

### 1.1 响应模型补齐（阶段 1）

```go
type chatResponse struct {
    Choices []struct {
        Message struct { /* 现状不变 */ } `json:"message"`
        FinishReason string `json:"finish_reason"` // stop|length|tool_calls|content_filter
    } `json:"choices"`
    Usage *usage `json:"usage"`
}

type usage struct {
    PromptTokens     int64 `json:"prompt_tokens"`
    CompletionTokens int64 `json:"completion_tokens"`
    TotalTokens      int64 `json:"total_tokens"`
}

type Completion struct {
    Content      string
    ToolCalls    []ToolCall
    FinishReason string
}
```

`ChatWithTools` 末尾带出 `resp.Choices[0].FinishReason`；`budget.Add` 仍只吃
`Usage.TotalTokens`。`Chat` 内部解析同步更新（chatResponse 是共享结构），返回值不动。

### 1.2 ReAct 截断防御（阶段 2）

`RunReAct` 循环体开头（ChatWithTools 返回后、终止判据前）插入短路：

```go
comp, err := d.LLM.ChatWithTools(ctx, msgs, llm.Options{Temperature: 0, MaxTokens: 1200}, toolDefs)
if err != nil {
    return err
}
if comp.FinishReason == "length" && len(comp.ToolCalls) > 0 {
    // 截断：参数可能不完整，全部不执行；assistant 原样回填 + 每个 call 合成
    // 错误 observation，让模型下一轮重发完整调用（Pi 式，与错误回填哲学一致）。
    // 不记 seen 指纹（未执行过）→ 重发同一调用不会被重复防御误伤。
    msgs = append(msgs, llm.Message{Role: "assistant", Content: comp.Content,
        ToolCalls: callsToMsg(comp.ToolCalls)})
    for _, call := range comp.ToolCalls {
        msgs = append(msgs, llm.Message{Role: "tool", ToolCallID: call.ID,
            Content: "输出达到 token 上限被截断，参数可能不完整，本次未执行。请重新发起完整调用。"})
    }
    continue // 消耗一个轮次预算，反复截断由 reactMaxTurns + 到顶兜底收口
}
if len(comp.ToolCalls) == 0 { /* 现有终止判据不变 */ }
```

到顶兜底（react.go:121 起）不动：截断回填已消耗轮次，最终走"强制收敛 + 部分结论"。

### 1.3 截断可见性与 done.reason（阶段 3）

- **ChatStream 返回 finish**：`func (c *Client) ChatStream(...) (finishReason string, err error)`，
  逐 chunk 捕获最后一个非空 `choices[].finish_reason` 返回；`LLMer` 接口（pipeline.go:21）
  同步改签名。
- **outcome holder 贯通**：pipeline.go 定义 `type chatOutcome struct{ truncated bool }`，
  RunChat 创建后传入 runChatInner → AnswerDirect / RunResearch（签名各加
  `outcome *chatOutcome` 参数；hybrid 两次调用、transaction.go 的 fallbackKnowledge
  同步更新调用点）。流式 finish=="length" 时：`outcome.truncated = true` 并
  `emit(statusEvt("回答已达长度上限，可能被截断"))`（status 是既有事件类型，前端直接可用；
  不改 answer 正文，避免影响评测判分）。
- **done 单点收口**：runChatInner 内所有 `emit(doneEvt(...))` 移除，done 统一在 RunChat
  结束处发射，reason 计算规则：

```go
reason := "completed"
if outcome.truncated {
    reason = "max_tokens"
}
if err != nil {
    reason = "error"
    if ctx.Err() == context.Canceled {
        reason = "aborted"
    }
}
_ = emit(doneReasonEvt(elapsedMS(t0), reason)) // error 路径 emit 失败静默，与现状一致
```

- `doneEvent` 补 `Reason string \`json:"reason,omitempty"\``（新构造器
  `doneReasonEvt`，老 `doneEvt` 保留或改造，保持一次 chat 恰一个 done 的不变量）。

## 2. 阶段总览

| 顺序 | 阶段 | 动作 | 依赖 |
|---|---|---|---|
| 0 | 基线冻结 | 28+8 评测留档（对照用） | — |
| 1 | llm 访问层补 finish_reason + usage 三元组 | 纯解析，零行为变化 | 0 |
| 2 | ReAct 截断防御 | 判断顺序 + 合成 observation | 1 |
| 3 | 流式截断可见性 + done.reason | ChatStream 签名 + outcome 贯通 + done 收口 | 1（2 无关，可并行） |
| 4 | 文档收口 | PARITY §3 契约、architecture、roadmap | 1-3 |

---

## 3. 阶段 0：基线冻结

### 提示词 P10-0
> 在 gewu 仓库执行 P10 前置基线，只跑评测不改代码：
> 1) `go build ./... && go vet ./... && go test ./...` 与 `bash scripts/lint-arch.sh` 确认全绿；
> 2) `RATE_LIMIT_PER_MINUTE=600` 起单体（默认 ROUTER_MODE=cascade），`python3 eval/run_eval.py --tag p10-baseline`（28 题）；
> 3) `ROUTER_MODE=agent-first` 重启后 `python3 eval/run_eval.py --dataset eval/dataset-agent.jsonl --tag p10-baseline-agent`（8 题）；
> 4) 两份报告存 eval/reports/，记录通过数与失败题 id（agent-first 失败集即后续各阶段的"允许 flaky 集"）。

## 4. 阶段 1：llm 访问层补 finish_reason + usage 三元组

现状：chatResponse（internal/llm/client.go:138）不解析 finish_reason，usage 只有
total_tokens。本阶段纯解析层补齐，零行为变化（budget 记账口径不动）。

### 提示词 P10-1
> 给 gewu 的 internal/llm 补响应侧字段，不改任何调用方行为：
> 1) chatResponse 按下述形状调整（新增 usage 具名结构体，含 prompt_tokens/completion_tokens/
>    total_tokens 三字段；Choices 元素补 `FinishReason string \`json:"finish_reason"\``）；
>    Chat 与 ChatWithTools 共享该结构，postJSONWithKeyTimeout 解析路径不变；
> 2) Completion（导出结构）补 `FinishReason string`，ChatWithTools 末尾带出
>    `resp.Choices[0].FinishReason`（Chat 返回 string 不动，FinishReason 仅内部可得即弃）；
> 3) budget.Add 仍只吃 Usage.TotalTokens（三元组解析暴露但不入账，注释说明"供成本分析留口"）；
> 4) 单测（沿用 client_test.go 现有 httptest 假端点模式）：
>    ① finish_reason=stop / tool_calls / length 三种响应，ChatWithTools 返回的
>       Completion.FinishReason 逐值透传；② usage 三元组字段解析正确且 budget 只加 total；
>    ③ 响应缺 finish_reason 字段时 FinishReason 为空串（兼容端点不回该字段的场景）；
> 5) 真跑冒烟：起服务问一个知识题，确认直答链路行为无任何变化（SSE 事件序列不变）。
> 门禁：build/vet/test 全绿 + lint-arch 通过 → 28 题无回归（对照 p10-baseline）→
> 留档 eval/reports/P10-llm-finish-reason.md（含新增测试清单与冒烟截图/事件流）。

## 5. 阶段 2：ReAct 截断防御（判断顺序铁律）

现状：react.go:94 唯一终止判据是"无 tool_calls 即最终回答"，但截断输出（finish_reason=
length）里可能带不完整 JSON 参数的 tool_calls，会被 parseToolArgs 容错成空 map 后真跑。

### 提示词 P10-2
> 给 gewu 的 ReAct 引擎加截断防御，判断顺序铁律：length 先于工具解析：
> 1) internal/agent/react.go 的 RunReAct 循环体，在 `if len(comp.ToolCalls) == 0` 之前插入：
>    `comp.FinishReason == "length" && len(comp.ToolCalls) > 0` 时——不执行任何调用、
>    不记 seen 指纹（未执行过，重发不得被 reactRepeatLimit 误伤）；assistant 消息原样
>    回填（Content + callsToMsg(comp.ToolCalls)），每个 call 追加一条 Role=tool、
>    ToolCallID=call.ID、Content="输出达到 token 上限被截断，参数可能不完整，本次未执行。
>    请重新发起完整调用。" 的合成 observation；然后 `continue`（消耗轮次预算）；
> 2) 到顶兜底与既有防护（指纹/maxTurns/预算熔断）全部不动；
> 3) 单测（cascade_memory_react_test.go 的 scriptedLLM comps 脚本化模式）：
>    ① 第一轮 comps 返回 {FinishReason:"length", ToolCalls:[一个读工具调用]}，第二轮
>       返回无调用的最终回答——断言：工具 Run 从未被执行（可在工具侧计数/用不存在的
>       工具名断言不触发 runAgentCall 执行路径）、合成 observation 已回填进 messages
>       （mock 需记录收到的 messages，如未记录则补记）、循环第二轮正常终止出答案；
>    ② length 且 ToolCalls 为空 → 不走截断分支（现有终止路径不受影响）；
>    ③ finish_reason=stop 且带 ToolCalls → 正常执行（防御只针对 length）；
> 4) 真跑冒烟：ROUTER_MODE=agent-first 起服务跑一个办理问题（如"帮我预约明天晚上
>    羽毛球馆"），确认 ReAct→确认流链路行为不变（截断分支靠单测覆盖，不依赖真模型撞线）。
> 门禁：build/vet/test 全绿 + lint-arch 通过 → 28 题无回归 + agent-first 8 题失败集 ⊆
> p10-baseline-agent 的失败集 → 留档 eval/reports/P10-react-truncation-guard.md。

## 6. 阶段 3：流式截断可见性 + done.reason

现状：ChatStream 只解析 delta.content，主答案撞 2048 token 上限截断完全不可见；
done 事件无结束原因，且发射点散在 runChatInner 各路径 + RunChat 兜底。

### 提示词 P10-3
> 给 gewu 补流式截断可见性与 done 结束原因，SSE 契约只增不改：
> 1) internal/llm/client.go：streamChunk 的 Choices 元素补 finish_reason 字段，逐 chunk
>    捕获最后一个非空值；ChatStream 签名改为
>    `func (c *Client) ChatStream(ctx, messages, o, onDelta) (finishReason string, err error)`
>    （错误时返回已捕获值或空串）；
> 2) internal/agent/pipeline.go：LLMer 接口 ChatStream 同步改签名；定义
>    `type chatOutcome struct{ truncated bool }`；RunChat 创建 outcome 传入 runChatInner，
>    AnswerDirect/RunResearch 签名各加 `outcome *chatOutcome` 参数（调用点同步更新：
>    pipeline 的 factual/hybrid 分支、transaction.go 的 fallbackKnowledge）；
> 3) direct.go/research.go：ChatStream 返回 finish=="length" 时置 outcome.truncated=true
>    并 emit(statusEvt("回答已达长度上限，可能被截断"))——不动 answer 正文（避免影响
>    评测判分），status 是既有事件类型；
> 4) done 收口到 RunChat 单点：删除 runChatInner 内全部 doneEvt 发射（含续轮 continue/
>    cancel 路径），RunChat 结束统一发射，reason 规则：outcome.truncated → "max_tokens"；
>    err != nil → "error"（ctx.Err()==context.Canceled 则 "aborted"）；否则 "completed"。
>    doneEvent 补 `Reason string \`json:"reason,omitempty"\``（形状向后兼容，老消费者
>    无感知）；保持一次 chat 恰一个 done 的不变量；
> 5) 同步改 5 处 mock 的 ChatStream 签名（返回值补 nil/空串即可）：
>    cascade_memory_react_test.go 的 scriptedLLM/flakyAfterQueue/flakyAlways/failingLLM、
>    api/api_test.go 的 mockLLM；
> 6) 单测：① llm 层——httptest 流式响应最后一带 finish_reason=length 的 chunk，
>    ChatStream 返回值透传；② agent 层——scriptedLLM 的 ChatStream 先回 delta 再返回
>    "length"，直答链路事件序列含截断 status 且 done.reason=max_tokens；③ 正常链路
>    done.reason=completed；④ runChatInner 返回错误时 done.reason=error 且仍恰一个 done；
> 7) 真跑验证：起服务 ①知识题 SSE 抓包确认 done 含 "reason":"completed"；②深研一道
>    长题确认正常收敛（截断路径单测覆盖）；③ curl --max-time 2 提前断开一条 chat，
>    服务端不 panic（aborted 的 done 发不出去属预期——客户端已断，静默即可）。
> 门禁：build/vet/test 全绿 + lint-arch 通过 → 28 题无回归 + agent-first 8 题失败集 ⊆
> p10-baseline-agent 失败集 → 留档 eval/reports/P10-done-reason.md（含 SSE 抓包证据）。

## 7. 阶段 4：文档收口

### 提示词 P10-4
> P10 收口文档，不改代码：
> 1) docs/PARITY.md §3 事件契约：done 事件补 reason 字段说明（completed/error/
>    aborted/max_tokens，omitempty 向后兼容）与新增的截断 status 事件语义；
> 2) docs/architecture.md：llm 域描述补"响应侧 finish_reason/usage 三元组"；ReAct 引擎
>    描述补截断防御（判断顺序铁律：length 先于工具解析，Pi 式合成 observation 回填）；
> 3) docs/roadmap.md：P10 条目（一行：动机 + 三个缺口 + 修复策略）；
> 4) 汇总 eval/reports/P10-*.md 四份报告链接进本节，标注各阶段门禁结果。

---

## 8. 执行结果（2026-09-07 收口）

四份报告与各阶段门禁结果：

| 阶段 | 报告 | 门禁 | commit |
| --- | --- | --- | --- |
| P10-0 基线 | [P10-baseline.md](../../eval/reports/P10-baseline.md) | cascade 28/28、agent-first 6/8（失败集 {ag-know-002, ag-tx-002} ⊆ §4.3 flaky） | 40b6b91（随 P10-1 入库） |
| P10-1 llm 层 | [P10-llm-finish-reason.md](../../eval/reports/P10-llm-finish-reason.md) | 全绿 + lint-arch；28 题 27/28→重跑 28/28（mtfact-002 已知 flaky）；真跑直答事件序列不变 | 40b6b91 |
| P10-2 ReAct 防御 | [P10-react-truncation-guard.md](../../eval/reports/P10-react-truncation-guard.md) | 全绿 + lint-arch；28 题 28/28；agent-first 5/8 两跑一致 ⊆ §4.3；冒烟确认流不变 | 337b4cc |
| P10-3 done.reason | [P10-done-reason.md](../../eval/reports/P10-done-reason.md) | 全绿 + lint-arch；28 题 28/28（评测脚本零改动）；agent-first 6/8 ⊆ §4.3；SSE 抓包 completed/深研收敛/断开无 panic | 8349f98 |

附：真跑撞出并顺手修复 P9 既有回归——`mode=direct` 丢失 direct→factual 归一报
"未知路由"（pipeline.go decideRoute + 守护测试，详见 P10-1 报告）。
