# 06 · 工程防线

> demo 与可运维系统的差距不在功能在防线。格物的防线：预算、限流、评测口径、
> 架构守护、测试策略——每一条都被真实问题逼出来过。

## 预算：双层拦截 + 全路径记账

- **双层**：HTTP 入口 `budget.Ensure()`（超限 429，不进管线）+ 每次 LLM 调用内部
  `Ensure()`（ReAct 循环、嵌入、重试全部无法绕过）；
- **记账**：非流式按响应 usage.total_tokens；流式按「rune 数/2」保守估算——
  中途断开也已产出的增量必须入账，否则长回答被中断时真实消耗漏计；
- **持久化**：usage.json 写穿，跨重启累计。

## 限流与观测

按 IP 令牌桶（QPS 可配）；X-Trace-Id（uuid v4）贯穿响应头 / gin 日志 / agent 日志，
SSE 流式问题可回溯到具体请求。

## 评测：不是跑分，是回归门禁

- **入口同一**：评测与 SSE 接口走同一个 `RunChat`——被测的就是线上代码路径；
- **口径**：28 题 cascade 种子集（单轮事实/多跳/拒答 + 多轮交易型断言业务库真实状态）
  + 8 题 agent-first 集；任何改动门禁 = cascade 28/28 且 agent-first 失败集 ⊆ 已知
  flaky 集；
- **flaky 判定**：温度 0 下小模型（flash）仍非确定，同代码多次跑结果会漂。对这类
  非确定性，「无回归」的正确判据是失败集不扩大，而不是强求每题恒过——把 flaky 集
  显式记录在基线报告里（eval/reports/），后续改动以此为对照；
- **留档**：每阶段评测报告存 eval/reports/，变更与证据可对账。

## 架构守护：lint-arch

模块化单体最大的风险是边界慢慢烂掉。`scripts/lint-arch.sh` 用 go list + grep 断言
七条依赖规则（agent → rag/llm/business 单向、business ↛ rag、支撑域不 import 业务域、
api 禁 llm、routing 仅 llm、cmd/server 装配白名单），零依赖、CI 可跑。规则是随着
拆包逐步加的——每次发现一个真实接缝就固化一条规则，不是一次性设计的。

## 测试策略：接口注入 + 脚本化 LLM

- **LLMer 接口**：编排层对 LLM 的依赖收敛为最小接口（Chat/ChatStream/ChatWithTools/
  Embed），生产注入 `*llm.Client`，单测注入 mock——不发真实网络请求；
- **scriptedLLM**：原生 tool-calling 的脚本化桩（comps 队列按序弹出 Completion），
  ReAct 的终止/重复防御/到顶兜底/写操作转确认流全部可用确定性脚本测；
- ** httptest 假端点**：llm 层用 httptest 模拟 OpenAI 兼容端点，测 SSE 解析/超时/记账。

## 设计哲学小结

> 每条防线都是「廉价确定性优先」的一次应用：预算在入口先拦、评测口径先冻结基线、
> 架构规则先于越界发生、mock 先于联调。LLM 应用的工程化，一半在做模型调用，
> 另一半在做**不信任模型调用**的护栏。
