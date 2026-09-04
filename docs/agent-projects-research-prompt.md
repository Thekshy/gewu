# 竞品深研提示词：开源 Agent 项目「具体怎么做的」研究

> 用法：把下面整段提示词投给一个具备代码/网页研究能力的 agent（如 ZCode 新会话或 deep research 工具），
> 产出物为一份对照报告。P0 三家必须完成，其余按时间裁剪。

---（以下为提示词正文）---

# 任务：开源 Agent 项目深研 —— 它们具体是怎么做的

你是一名资深 Agent/LLM 系统工程师。请深入研究下列开源项目在特定机制上的**具体实现**（代码级，不是宣传文案），与参照系统 **gewu** 对照，产出一份研究报告。

## 0. 参照系统：gewu（对照基准）

gewu 是高校场景的 Deep Research 问答 + 业务办理系统（Go/gin + 纯 Go SQLite，单二进制）：

- **五分类路由**：`factual / research / transaction / hybrid / refusal`；LLM 结构化 JSON 输出分类，无 key 时启发式降级（长度 + 并列连词信号）
- **混合检索**：BM25（中文字符二元语法，零分词依赖）+ 向量余弦，RRF 融合
- **Deep Research**：子问题拆解（2~4 个，消解指代）→ 每子问题独立混合检索（top5）→ 跨子问题按 chunk_id 去重（≤12 条）→ 交叉综合；逐事实 `[n]` 引用，证据不足显式声明「未找到依据」，冲突处指出并给出来源
- **SSE 事件流**：全过程 `route → status → step* → answer_delta* → citations → done`，前端与评测端复用同一管线；context 取消可传播到上游 LLM 流
- **知行执行层（业务办理）**：mock 业务系统（场馆预约/请假审批）：槽位收集、多轮澄清、写操作确认流、回执、冲突恢复；日期等槽位由确定性代码换算（LLM 只负责找表述，不算日期）；权限矩阵（学生/辅导员）在工具层单一出口拦截越权
- **模型分层**：主答案用强模型，路由/拆解/槽位抽取/查询改写等辅助调用用 flash 档模型
- **评测**：26 题离线集（事实/多跳/拒答 + **多轮交易型：断言业务库真实状态**），Python 跨语言 HTTP 客户端一键出 Markdown 报告
- **成本防线**：IP 令牌桶限流 + 每日 token 预算（持久化、跨重启、原子落盘）
- 代码结构：`internal/{agent,budget,business,config,dates,llm,middleware,rag}`；编排管线 RunChat 自研（~400 行，无 LangChain 类依赖）

（可选）若你能访问本地文件系统，gewu 源码在 `/Users/mrpwn/Project/mine/gewu`，建议先读 `docs/architecture.md` 与 `internal/` 对应包再对比；不能访问就以上面摘要为准。

## 1. 研究对象与优先级

| 优先级 | 项目 | 仓库 | 主要研究面 |
|---|---|---|---|
| P0 | Onyx | github.com/onyx-dot-app/onyx | 整体架构：混合检索、检索层权限过滤、deep research、拒答与引用 |
| P0 | Open Deep Research | github.com/langchain-ai/open_deep_research | 研究链路：拆解/压缩/并行 + 评测（Deep Research Bench） |
| P0 | Rasa | github.com/RasaHQ/rasa | 槽位收集：forms 状态机、打断恢复、确认流 |
| P1 | GPT Researcher | github.com/assafelovic/gpt-researcher | 研究循环、引用、并发与上下文管理、evals |
| P1 | STORM | github.com/stanford-oval/storm | 多视角拆解、综合、引用 |
| P1 | AgentTOD / DIMF | 论文：dl.acm.org/doi/10.1145/3745021 · arxiv.org/abs/2505.14299 | LLM 槽位填充 agent 设计、任务完成评测（如有官方开源仓库一并研读） |
| P2 | Eino | github.com/cloudwego/eino | Go 编排抽象（Chain/Graph/流式/callback）对照 gewu 自研管线 |
| P2 | Dify / FastGPT | github.com/langgenius/dify · github.com/labring/FastGPT | 「问答+工作流+知识库」的产品化抽象 |

P0 三家必须完成；P1 尽量完成；P2 时间不够可合并为简短综述。

## 2. 每个项目的研究问题（逐条作答）

### Onyx
1. 混合检索：BM25 与向量如何融合（RRF？加权？归一化？），实现在哪个文件，融合参数如何配置
2. 检索层权限过滤：索引隔离 / 查询前过滤 / 查询后过滤？文档级还是 chunk 级？ACL 存储结构长什么样
3. deep research：如何触发与编排（子问题？迭代搜索？），最大轮数/预算如何控制；与普通问答的边界怎么划
4. 无证据 / 范围外问题的行为（拒答策略是什么、在哪实现）
5. 引用如何生成与渲染（答案与 chunk 的绑定方式）
6. 质量评测：内部有没有 eval 集 / 回归手段，怎么组织

### Open Deep Research
1. 编排结构：supervisor 如何生成/调度子任务？多步之间如何压缩上下文（摘要？丢弃？）
2. 并行研究单元如何实现，结果如何汇总
3. 可配置面（模型 / 搜索工具 / MCP）如何抽象
4. human-in-the-loop interrupt 怎么实现（对照 gewu 的写操作确认流）
5. Deep Research Bench：评测集构造、判定方式（人工？LLM judge？精确匹配？）、指标与跑法

### Rasa
1. Forms 状态机：`requested_slot` 流转、slot mapping（from_entity / from_text / from_trigger_intent）、条件必填槽（required_slots 条件函数）的具体实现
2. 槽位校验：`validate_<slot>` 如何挂载、校验失败如何重问
3. 中途打断与恢复：happy path 被打断后 form 如何中断与恢复，事件驱动机制
4. 确认流：预提交确认的标准做法，如何防误写
5. Rasa Pro CALM（LLM 原生、flows 范式；商业许可 source-available）公开资料中 LLM 与确定性流程的分工 —— 与 gewu「LLM 找表述、代码算值」的对照

### GPT Researcher
1. 研究循环的状态与终止条件（plan → search → read → synthesize 具体如何循环、何时停）
2. 引用机制：来源如何收集、编号、防编造
3. 上下文管理：访问过的网页如何存取（内存向量库？），长任务如何压缩
4. 并发实现与限制
5. evals：评测怎么跑、评什么

### STORM
1. 多视角提问（perspective-guided question asking）如何生成与使用 —— 与 gewu 固定拆解 2~4 个子问题的差异与优劣
2. 检索与证据组织、综合写作流程
3. 引用与来源去重

### AgentTOD / DIMF（论文研读）
1. 槽位填充 agent 的 prompt / 工具设计：LLM 输出什么结构、如何映射到业务 API
2. 意图分类与槽位填充的分工方式
3. 任务完成评测：如何断言「预订成功」（对照 gewu 的多轮交易型断言业务库状态）

### Eino
1. Chain / Graph 编排抽象的核心接口；与 gewu ~400 行自研 RunChat 管线的能力差距（多轮状态？checkpoint？）
2. 流式：流如何组合/分流，取消如何传播（对照 gewu 的 context 取消传导）
3. Agent / Multi-agent 抽象
4. 结论：gewu 当前形态是否值得迁移或局部借鉴，具体借哪些件

### Dify / FastGPT（简短）
1. workflow 节点抽象：意图分类、知识检索、工具调用如何编排
2. 对 gewu 泛化为「任意学校可配置」有什么产品化启发

## 3. 研究方法（必须遵守）

1. **证据优先级：源码 > 官方文档 > 论文正文 > 博客/README**。README 的说法要在代码里找到对应实现才算数；言行不一处要点名。
2. coding 环境下：`git clone --depth 1` 到 `/tmp` 下读码；web 环境下用 GitHub 网页跟到 blob 路径读文件。
3. 每个项目先花 10~15 分钟看目录结构定位相关模块（提示：Onyx 检索看 backend/ 下；ODR 看 src/；Rasa 看 form/slot 相关源码；GPT Researcher 看 backend/；STORM 看 knowledge_storm/；Eino 看 flow/ 与 components/ —— 以实际目录为准）。
4. **每条关键结论必须附证据**：仓库内路径（尽量 file:line）或论文章节号；关键代码片段原文引用 ≤ 15 行。
5. 记录研究时的 commit hash 或日期，保证报告可追溯。
6. 找不到的就明说「未找到/未验证」，**严禁编造路径、行号、接口名**。
7. 有条件时并行研究（每项目一个子任务），最后统一汇总。

## 4. 报告要求

保存为单个 Markdown 文件（建议名：`agent-projects-report.md`）。结构：

1. **总览**：一段话 + 一张「gewu 组件 × 竞品」对照地图（谁在哪块最强）
2. **每项目一节**：
   - TL;DR（≤3 行：它在对应问题上的核心设计与最值得 gewu 看的一点）
   - 按「第 2 节研究问题」逐条作答：机制描述 → 代码/论文证据 → 与 gewu 同/异做法及评价
   - 「可借鉴」与「应避免/不适用」清单（结合 gewu 是单人维护的单二进制 Go 项目这一现实）
3. **横向对比表**：路由 / 混合检索 / 研究链路 / 槽位与确认 / 权限 / 评测 / 流式与并发 —— 每维度各项目一句话做法
4. **给 gewu 的行动建议**：Top 10 以内，每条注明「参考项目 → gewu 具体落点（internal/xxx 的哪个机制）→ 工作量档位（小/中/大）」，必须可执行，禁止「加强」「优化」这类空泛表述

语言：中文撰写；代码、路径、术语、事件名保留英文。文风：结论先行、证据随后；禁止没有信息量的空话（如「架构先进」「值得学习」一律删掉）。
