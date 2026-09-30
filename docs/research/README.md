# research/ —— 竞品深研

| 文件 | 内容 |
| --- | --- |
| [agent-projects-report.md](./agent-projects-report.md) | **开源 Agent 项目深研报告 × gewu 对照**（2026-09-04）：Onyx / Open Deep Research / Rasa / GPT Researcher / STORM / AgentTOD / DIMF / Eino / Dify·FastGPT 的源码级研读，与 gewu 各组件逐项对照（谁在哪块最强 / gewu 位置 / 缺口清单） |
| [agent-projects-research-prompt.md](./agent-projects-research-prompt.md) | 上述研究用的下发提示词（可复用于新一轮调研） |

> 报告锚定的是 **Go 时代**的 gewu（代码行号基于当时 main）——组件级结论
> （路由分层、混检对照、评测硬度、缺口清单）对当前 LangGraph 形态仍适用；
> 其中「打断即丢会话 / 引用无后处理 / 研究链路无时钟兜底」三个缺口，前两个
> 已被 P14 的 interrupt + checkpointer 部分解决。
