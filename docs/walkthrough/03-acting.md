# 03 · 执行层：ReAct 引擎与写操作确认流

> 格物有两条执行路径：确定性 workflow（transaction 域）与自主 ReAct 引擎。
> 它们并存而不是替代，分界线是「确定性可复现」与「需要自主规划」。两者对写操作的
> 处理收敛到同一条确认流——这是本篇的核心。

## 两条路径的分界

| | workflow（transaction.go） | ReAct（react.go） |
|---|---|---|
| 适用 | 目标明确的办理（预约/请假/取消） | 目标明确但路径不定、组合意图 |
| 工具选择 | 正则启发式优先，flash 兜底 | 模型每轮自主决定（原生 tool_calls） |
| 状态 | TxSession 槽位状态机 | 消息历史 + 防护计数器 |
| 可复现性 | 高（评测基线） | 依赖模型行为（有护栏） |

workflow 是评测可复现的确定性基线；ReAct 服务「agent 自主组合工具」。P6 起 ReAct
两个入口：`mode=react` 显式指定；agent-first 路由的 agent 策略。

## ReAct 引擎：终止语义与防护

循环（≤8 轮）唯一的终止判据是**模型不再产出 tool_calls**——即「模型债务清零」。
围绕它的防护四件套：

1. **重复指纹**：相同 (name,args) 规范化指纹第 3 次被拒并提示换路（json.Marshal 对
   map 排序输出，同一调用必有同一指纹——去重的确定性基础）；
2. **maxTurns 上限**：到顶后不是裸报错，而是追加「不要再调用工具」强制收敛一次，
   失败再用已累积的 observations 组织部分结论——不留裸错误给用户；
3. **预算熔断**：client 每次调用内部 Ensure，ReAct 无法绕过预算；
4. **错误回填不断链**：工具执行失败转成 observation 交回模型决策，而不是终止循环。

### 对照生产级 Loop 终止理论

对照 DeepSeek Harness / Pi 的终止架构（五层边界：模型请求 → step → turn → activity →
工作流；turn 结束 = 模型债务 ∧ 消息债务双清零），格物的位置：

| 维度 | 现状 | 定性 |
|---|---|---|
| 终止判据 | 无 tool_calls = 模型债务清零 | 与主流一致 |
| 消息债务（inbox/steering） | 同步单请求架构下不存在 | 结构性规避 |
| 并行工具顺序提交 | for 循环顺序执行 | 结构性规避 |
| max-tokens 先于工具解析 | **暂不解析 finish_reason**，截断 tool 参数可能被执行 | 已知缺口 → [P10](../runbooks/P10-finish-reason.md) |

理论的价值不是照搬复杂度，而是知道自己的边界属于哪一层、缺什么、什么时候需要补。

## 写操作确认流：fail-closed 的单一出口

核心原则：**写操作不在推理循环里直接执行**。

```
workflow 入口：槽位收集（collect）→ 必填齐 → 确认摘要（confirm）
ReAct 入口：  模型发起写调用 → 必填参数齐 → 复用同一个 TxSession 确认阶段
                                    ↓
              pending_action 事件（有序参数表）→ 用户「确认」→ execute → 回执
```

两个入口收敛到同一个 `TxSession` 确认流（`agentConfirmSession` 把 agent 的自由 JSON
参数过与 workflow 相同的 normalizeSlot 确定性归一）——**agent 的自主性和写操作的
安全性通过「确认流复用」解耦**。确认后下一轮由 ClassifyReply/HandleReply 确定性接管。

这个设计等价于把「继续运行的权力」交还给人：状态不确定时停止自动续行（fail-closed），
与 Harness 的 Goal activation 默认 disarm 是同一哲学。

## 槽位收集：LLM 找表述，代码算结果

- **抽取**：flash 从用户消息抽槽位候选（`llmExtractSlots`），失败退化为确定性解析；
- **归一**：LLM 抽出的原始值一律过确定性解析器（`normalizeSlot`）——日期换算走
  internal/dates、场馆名 → venue_id 走库表、时段口语映射走表——**LLM 只负责「找出」
  表述，换算永远是代码的**，杜绝「明天」算错一天；
- **失败恢复**：执行失败区分字段级问题（重问该字段，冲突时给可选项）与其余失败
  （结束并说明）——恢复也是流程的一部分。

## 边界与已知短板

- finish_reason 不解析（截断防御缺口，P10 修复中：Pi 式——截断的 tool 参数全部不执行，
  合成错误 observation 回填让模型重发；截断那次不计指纹，重发不被防重复守卫误伤）；
- ReAct 循环内不能中途换工具集（工具表在循环外冻结）——这与「工具集只在请求边界
  变更」的工程共识一致，属刻意约束而非缺陷。
