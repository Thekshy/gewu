# ADR-0010：编排层迁移 LangGraph（Go 退役）

- 状态：已接受（2026-09-30，P14 执行完毕）
- 背景：gewu 定为毕业论文项目《基于 RAG 与 LangGraph 的校园制度智能问答 Agent
  研究与实现》，同时保留求职展示与 agent 学习定位
- 关联：[P14 任务书](../P14-langgraph-migration.md)、ADR-0008（存储迁 PG——本次
  存储层零改动复用的前提）、ADR-0009（上一次"冻结规格换实现"的退役实践）

## 决策

编排层自 Go 全量迁移至 **Python + LangGraph**，Go 后端（cmd/internal/go.mod/bin）
按 ADR-0009 的退役模式删除，终态锚定 git tag `go-final`。

### 为什么迁

1. **原理验证已完成，框架价值开始大于学习价值**：Go 时代手写了级联路由、ReAct
   循环、截断防御、确认流状态机——每个都有单测与对照报告。LangGraph 提供了这些
   件的原生等价物，继续手写是重复劳动。
2. **原生机制收益**（P14 Q4 拍板"引入原生机制"而非 1:1 移植）：
   - StateGraph 显式建图：路由/管线/循环/确认门成为可视化一等公民；
   - `interrupt()`：写操作人工确认从自研跨轮状态机变为图原生暂停/恢复；
   - PostgresSaver：会话与办理流程持久化（重启续办）免费获得，吸收了 P8-1 的
     sessions.db 语义；
   - custom stream writer：PARITY SSE 十类事件契约照旧逐环节可插桩。
3. **毕设与生态**：论文题目即 LangGraph；langchain 生态（后续 ragas 评测等）顺路。

### 为什么可以低风险迁

- PARITY.md 冻结行为契约：SSE 事件/错误体/6 端点逐字段对齐，**前端零改动**；
- P12 已把检索读写收口为 PG 存储函数——存储层跨语言零改动复用；
- 评测三数据集（28 主集 + 8 agent 集 + 41 检索查询）与 run_eval.py（纯标准库）
  跨语言复用，同一把尺子验收。

### 为什么不是并存

P14 Q2 拍板全量替换退役（不留双栈）：对照实验靠 eval/reports/ 历史基线报告 +
方差基线法（跨语言差异 ≤ 自身 run-to-run 方差即等价，见任务书 §6 P14-1）；
双栈长期维护成本对个人项目不划算。triage/classic 路由策略随之退役（结论留档
walkthrough/02 与 eval/reports/），`mode=react` 是 agent 链路的显式入口。

## 后果

- 正面：确认流/会话持久化的自研代码量大幅下降；图结构即文档；论文叙事完整。
- 负面/代价：三语言史（Python→Go→Python）需要解释（本文与 README「实现语言的
  两次对比」承担）；uv 依赖管理、ruff/pytest 工具链接替 gofmt/go test；
  GLM 温度 0 非确定性在 Python 侧同样存在（评测重跑确认的纪律不变）。
- 遗留：入库 CLI（hierarchical 切分侧）未随本次移植（语料已有索引在库）；
  LangGraph Store 跨线程记忆列为后续增强（roadmap）。
