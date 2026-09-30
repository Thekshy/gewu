# 开源 Agent 项目深研报告 × gewu 对照

> 研究日期：2026-09-04。由 8 个并行子任务对下列项目做源码级研读后汇总，证据优先级：源码 > 官方文档 > 论文正文 > 博客/README。
>
> | 项目 | 版本锚点 | 获取方式 |
> |---|---|---|
> | Onyx | `e3320d8`（2026-09-03 UTC） | git clone --depth 1 |
> | Open Deep Research (ODR) | `1b7d2e8`（2026-08-10） | SSH 克隆（https 被代理阻断） |
> | Rasa（+ rasa-sdk） | `c4069568` / `e4b6d12`（2025-12-18） | codeload tarball（clone 被阻断） |
> | GPT Researcher | `5cdad9cb`（2026-06-23） | codeload tarball |
> | STORM | `fb951af`（2025-09-30，此后无新提交） | 镜像克隆，hash 交叉验证 |
> | AgentTOD / DIMF | 论文：TOIS 10.1145/3745021 / arXiv:2505.14299 | web 研读（详见该节） |
> | Eino | v0.9.19（2026-09-01 tag；commit hash 不可得） | goproxy.cn 模块源码包 |
> | Dify / FastGPT | `65b4091`（2026-09-03）/ `308a5cd`（2026-08-31） | 镜像克隆 + graphon wheel |
>
> 未验证事项集中声明：AgentTOD 期刊版正文在付费墙后未读到（以其 ACL 会议版前身 **AutoTOD 官方仓库**代码为证据）；DIMF 论文无官方开源实现（已检索确认不存在）；Eino 的 commit hash 因网络原因未取得，以模块版本号锚定；Onyx 正处于 Vespa→OpenSearch 迁移期，两套实现并存，本文两套都给证据。gewu 侧代码行号基于当前 main（Go 版），编排主体实为 `internal/agent/pipeline.go`（150 行）+ `transaction.go`（694 行），文档中「RunChat ~400 行」的说法已过时。

---

## 1. 总览

gewu 的每个核心组件都能在竞品中找到「工业化加强版」，但**没有一家在 gewu 的约束条件（单人维护、单二进制、纯 Go SQLite、封闭语料、办事场景）下比 gewu 的取舍更合理**：Onyx/ODR 的迭代深研链路成本超预算一个量级；Rasa/CALM 验证了 gewu「LLM 出 JSON、确定性代码管状态机」的分工是范式正确（gewu 已是 CALM 的手工微缩版）；评测上 gewu 的「断言业务库真实状态」在所有项目中最硬（AutoTOD 同源，DIMF/GPT Researcher 均不如）；gewu 真正的缺口集中在五处——**打断即丢会话、引用无后处理、研究链路无时钟兜底、无 CI 回归、槽位无条件必填**，全部是小工作量修补。

**gewu 组件 × 竞品对照地图（谁在哪块最强 / gewu 位置）**：

| gewu 组件 | 最强参照 | 参照强在哪 | gewu 现状判断 |
|---|---|---|---|
| 五分类路由 | DIMF（消融数据）/ Dify·FastGPT（产品化节点） | DIMF 证明小模型分步调用 >> 单次调用（Combined 58.9→97.7） | 已同构，缺「去历史」瘦身实验 |
| 混合检索 | Onyx | 索引内归一化加权 + 跨查询**加权** RRF（k=50，权重 1.3/1.0/0.7/0.5） | 单层等权 RRF（k=60）在纯 SQLite 下是正确选择，无需追 |
| Deep Research 链路 | ODR / Onyx | supervisor 循环 + 子代理并行 + 边界保真压缩 + 总时钟收敛 | 一轮式性价比更高；应补「总时钟 + 证据字数上限」两个兜底 |
| 拆解策略 | STORM | persona × 多轮追问（广度 × 深度二维） | 固定 2~4 子问题的成本差一个量级，校园问答不需要 persona |
| 槽位与确认 | Rasa / CALM | requested_slot 游标、打断恢复、patterns 体系 | gewu 确认流已超开源 Rasa 默认形态；缺「打断不丢」「条件必填」 |
| 权限 | FastGPT（检索层 authTmbId）/ Onyx（chunk 冗余 ACL） | 检索层按对话用户过滤 | 单租户下工具层单一出口正确，检索层过滤待私有语料出现再加 |
| 引用 | STORM / Onyx | 后处理三件套 + 全角括号兜底正则 | gewu 只有生成端提示词约束，是**最便宜的加固点** |
| 评测 | AutoTOD（库断言）+ Onyx（prompt 触发 CI） | 断言式 + 路径触发 + 非阻塞 | 断言路线正确且领先；缺 CI 触发与「单据集合双向断言」 |
| 流式/编排 | Eino | StreamReader 背压取消 + Copy 分身观测 + checkpoint | emitFn 在 gewu 规模下更直接；**不迁移**，条件性借 react |

---

## 2. Onyx（P0：整体架构）

仓库 `onyx-dot-app/onyx` @ `e3320d8`。检索在 `backend/onyx/` 下；正处 Vespa→OpenSearch 迁移期（`document_index/factory.py:126-134` 由 DB 状态决定后端）。

**TL;DR**：混合检索是两层融合（索引内归一化加权 + 应用层多查询加权 RRF）；权限是 chunk 冗余 ACL + 索引内查询前过滤；deep research 是用户手动触发的 orchestrator-subagent 双层循环（8×8 周期 + 三层时钟）；**代码层不存在拒答策略**。最值得 gewu 看：多查询加权 RRF 的权重设计、`collapse_citations` 引用重编号、prompt 路径触发的非阻塞评测 CI。

### 2.1 混合检索

**机制**：两层。第一层在索引内：一条 Vespa YQL 同时做向量（content+title 双 nearestNeighbor）与关键词（weakAnd）检索，global-phase 对候选集做线性归一化后的加权组合 `alpha*norm(向量)+(1-alpha)*norm(BM25)`，再乘 document_boost / recency_bias / chunk_boost（`backend/onyx/document_index/vespa/app_config/schemas/danswer_chunk.sd.jinja:249-279`，rerank-count 1000）。`HYBRID_ALPHA=0.5`、`TITLE_CONTENT_RATIO=0.10` 由 `configs/chat_configs.py:76-83` 配置；keyword 型查询降到 0.2 并切换 BM25 主导的 rank profile（`vespa_document_index.py:941-957`、`search_runner.py:65`）。OpenSearch 对应物是 normalization-processor，权重 title-vec 0.1 / content-vec 0.45 / keyword 0.45（`document_index/opensearch/search.py:62-74,110-151`）。

第二层在应用层：`internal_search` 工具让 LLM 生成 1 条语义改写 + 最多 3 条关键词扩展（`secondary_llm_flows/query_expansion.py:163-164`），每条各跑完整混合检索，结果用**加权 RRF（k=50）**融合：

```python
# tools/tool_implementations/search/search_utils.py:87-94
for rank, item in enumerate(result_list, start=1):
    item_id = id_extractor(item)
    rrf_scores[item_id] += weight / (k + rank)
```

权重硬编码且注释明言有意不开放配置（`search/tool_implementations/search/constants.py:4-20`）：语义改写 1.3 / 关键词扩展 1.0 / 非定制查询 0.7 / **原始查询 0.5**；搜索 UI 流程另用 2.0/1.0（`ee/onyx/search/process_search_query.py:163-186`）。

**与 gewu 对照**：骨架同（BM25+向量→RRF），Onyx 多了「索引内加权」与「跨查询加权」两层。gewu 等权 RRF 对单一改写查询是合理默认；若未来把原始问题也作为一路，Onyx 对原始查询降权 0.5 的做法值得直接抄。索引内归一化层依赖引擎能力，纯 Go SQLite 复刻不划算。

### 2.2 检索层权限过滤

**机制**：查询前过滤、索引引擎内完成；权限**按 chunk 冗余存储**（每个索引文档即一个 chunk，携带所属文档完整 ACL），语义是文档级：

```python
# document_index/vespa/indexing_utils.py:214-218
ACCESS_CONTROL_LIST: {acl_entry: 1 for acl_entry in chunk.access.to_acl()},
```

查询时用 Vespa 原生 `weightedSet()` 过滤（用户 ACL 上万条时 OR 链会 HTTP 400，`vespa_request_builders.py:46-60,195-201`）。ACL 来源：`DocumentAccess{user_emails, user_groups, is_public, ...}`（`access/models.py:176-199`）从 Postgres 文档-凭证联表聚合（`db/document.py:781-826`）；用户侧集合见 `access/access.py:129-135`（匿名=仅 PUBLIC）。组合语义有专文（`document_index/FILTER_SEMANTICS.md:21-29`）。细节：文档尚未索引时按最小权限处理（fail-closed，`access/access.py:93-99`）。

**与 gewu 对照**：异。gewu 单租户、语料公开、权限收口在工具层——定位差异而非缺陷。可记一个点：若 gewu 未来接辅导员内部文件，照「文档级 ACL 表 + 查询期过滤」形态做即可（SQLite 一张 doc_acl 表 + WHERE 过滤），不要抄 chunk 冗余。

### 2.3 deep research：触发、编排、预算

**机制**：触发是**用户手动开关**（前端 toggle `web/src/sections/input/AppInputBar.tsx:549-565` → 请求字段 `deep_research: bool`，分流点 `chat/process_message.py:1372-1374`），非 LLM 路由。编排三阶段（`deep_research/dr_loop.py`）：可选澄清（LLM 无工具调用则反问并终止本轮，:299-312）→ 流式生成研究计划（:314-398）→ 编排循环 `MAX_ORCHESTRATOR_CYCLES=8`（reasoning 模型 4，:97-100），每轮 `tool_choice=REQUIRED` 发起 `research_agent`（并行 ≤3）/`think`/`generate_report` 调用；子代理（`tools/fake_tools/research_agent.py:216+`）内部再跑 ≤8 轮工具循环，产出中间报告，经 `collapse_citations` 重编号合并回主循环。子代理任务必须**自包含**（看不到用户原始问题，orchestration 层提示词约束）。

预算控制全是「周期数 + 时钟」，无 token 预算：主循环 30 分钟强制终报（`dr_loop.py:85,441-451`）；子代理 30 分钟硬超时 + 12 分钟强制中间报告（`research_agent.py:88-91`）；终报 ≤20000 token（`dr_loop.py:80`）；编排步 `max_tokens=1024` 防死循环（:541）；入口要求模型输入窗口 ≥50000（:227-230）。

**与 gewu 对照**：gewu 路由自动判 research、一次拆解一轮检索；Onyx 手动触发、双层迭代可烧几十次调用跑半小时。gewu 自动路由贴合校园场景，但**无任何轮数/时钟兜底**是真实缺口——「总时钟 + 强制收敛出报告」两个廉价保险值得抄（Go 侧一个 `context.WithTimeout`）。Onyx 子代理任务自包含约束与 gewu 拆解时消解指代同思路。gewu 的 hybrid（问答+办理）是 Onyx 没有的形态（其 DR 工具白名单明确排除非搜索工具，`dr_loop.py:241-243`）。

### 2.4 无证据 / 范围外行为

**机制**：代码层无拒答策略。检索为空仅记日志并把 `{"results": []}` 交给 LLM（`search_tool.py:1052-1065`）；默认系统提示词无「无证据须拒答」指令（`prompts/chat_prompts.py:13-18`，全目录 grep 无命中）；反向地，普通循环最后一轮提醒是「必须尽力作答」（`chat_prompts.py:52-54`）。唯一主动反问路径是 DR 澄清步骤。

**与 gewu 对照**：gewu 的确定性拒答（refusal 路由类 + 检索空走固定话术不进生成，`internal/agent/direct.go`）在办事场景更正确——可测试、可审计。Onyx 的做法适合开放式知识问答，不学。

### 2.5 引用生成与渲染

**机制**：四步：① 检索结果按 `document_id` 分 citation_id（同文档多 section 共号，`tools/tool_implementations/utils.py:46-57,80-83`）；② 提示词强制内联 `[1],[2]`（`chat_prompts.py:40-44`，经 `chat/prompt_utils.py:252-255` 注入）；③ 流式正则改写器 `DynamicCitationProcessor`（`chat/citation_processor.py:69+`）逐 token 识别 `[1]`/`[1,2]`/`[[1]]`/**`【1】`（全角括号）**，代码块跳过、不完整引用 hold back、防指数回溯（:195-213），可改写为 `[[n]](url)` 并先 yield `CitationInfo` 给前端；④ 跨子任务重编号 `collapse_citations`（`chat/citation_utils.py:99-220`）按 document_id 去重映射为连续新编号。

**与 gewu 对照**：大方向同，三处值得抄：(a) 同文档共享编号让用户侧引用更干净（gewu 绑 chunk 粒度）；(b) **全角括号兜底正则**——中文模型输出 `【1】` 时 gewu 会丢引用，直接抄；(c) 重编号后处理——gewu 聚合编号是静态的，补一步按证据 ID 重排成本很低。

### 2.6 质量评测

**机制**：四套：(1) Braintrust chat eval 框架（`evals/`，per-test 可配 `expected_tools` 工具断言，数据集在云端不入库）；(2) 搜索质量回归脚本（`tests/regression/search_quality/`，ground truth 按 doc_link 匹配出 rank 分布，手工跑）；(3) 答案质量回归（同目录 answer_quality，docker 化，手工）；(4) **CI 中唯一的 prompt 回归门**：`.github/workflows/pr-connector-filter-eval.yml` 只在 prompt 文件变更时触发，28 用例跑真 LLM，每 case 重试 2 次，`continue-on-error: true`——测试头注释明言 "a red run is a signal to read, not a merge gate"。RRF 另有 10+ 纯单元测试在常规 CI（`tests/unit/onyx/tools/test_search_utils.py:32-214`）。

**与 gewu 对照**：gewu 26 题集 + 引用召回在形式上像 Onyx 的手工回归脚本，但缺 CI 触发。Onyx 的三原则——**路径触发（只改 prompt 才跑）+ 非阻塞 + 确定性断言优先**——是单人项目抄得起的形态。

### 2.7 可借鉴 / 应避免

**可借鉴**：① 原始查询降权 0.5 的加权 RRF（若加多路查询）；② 全角括号引用兜底正则；③ collapse_citations 式重编号；④ 「总时钟 + 强制收敛」预算兜底；⑤ prompt 路径触发 + 非阻塞评测 CI；⑥ 工具断言式零方差指标混入评测；⑦ 子任务自包含提示词约束。

**应避免**：索引内归一化融合层（引擎能力，SQLite 无对应物）；chunk 级 ACL 冗余（多租户 SaaS 设计）；8×8 双层迭代研究（成本超 gewu 场景一个量级）；空结果交 LLM 发挥的拒答观；Braintrust 外部云依赖；双索引后端并存的迁移态。

---

## 3. Open Deep Research（P0：研究链路与评测）

仓库 `langchain-ai/open_deep_research` @ `1b7d2e8`。现行实现在 `src/open_deep_research/`（5 文件 ~2300 行）；`src/legacy/` 是两代旧实现；`tests/` 是 LangSmith 评测脚手架。

**TL;DR**：单文件 LangGraph 图 `clarify → write_research_brief → supervisor(⇄tools) → final_report`，子研究员整个 ReAct 轨迹经 `compress_research`「保真清理（明令禁止摘要）」压缩成一条 ToolMessage 回流；并行是纯 `asyncio.gather`（≤5）；**现行版不用 `interrupt()`**，HITL 靠图终止 + 持久化重入；评测深度绑定 LangSmith + LLM judge。对 gewu 最有价值的是三层超限退化兜底与「编排行为精确断言」的评测思路。

### 3.1 编排与上下文压缩

**机制**：`write_research_brief` 把多轮对话收敛成一段自包含 research_brief 并 **override** supervisor 的消息线程（`deep_researcher.py:163-175`）。supervisor 是双节点循环子图（:353-363），每轮一次 LLM 调用绑定 `ConductResearch`/`ResearchComplete`/`think_tool` 三工具；退出条件：迭代超 `max_researcher_iterations`（默认 6）/无工具调用/调用 ResearchComplete（:247-255）。压缩发生在 researcher→supervisor 边界：子图输出 schema 只暴露 `compressed_research` + `raw_notes`（`state.py:92-96`），压缩语义是**去重清理而非摘要**：

```python
# prompts.py:224-226
DO NOT summarize the information. I want the raw information
returned, just in a cleaner format.
```

三层超限退化：压缩遇 token limit 从后往前删消息重试 ≤3 次（`deep_researcher.py:569-571` + `utils.py:848-866`）；终报超限按「模型上限×4 字符」起步截断、每轮 ×0.9（:666-682）；网页抓取按 URL 去重 + 每页 ≤50000 字符先摘要（`utils.py:70-110`）。两个点名缺陷：`think_tool` 的反思文本会混进终报 findings（:259 + `utils.py:599-601` 只按 tool 类型过滤不区分工具）；子研究员异常分支 `if is_token_limit_exceeded(...) or True:` 恒真——任一失败即终结整个研究阶段（:332-342）。

**与 gewu 对照**：gewu 固定拆解一轮即止、≤12 条硬截断且无超限退化路径（prompt 超限直接失败）。gewu 不需要 supervisor 循环，但「超限→渐进截断重试」「摘要失败→返回原文」（`utils.py:206-213`）的兜底思想应抄。

### 3.2 并行与汇总

**机制**：同一轮多个 `ConductResearch` 调用截断到 `max_concurrent_research_units`（默认 5）后 `asyncio.gather` 并发 invoke 同一 researcher 子图（`deep_researcher.py:295-305`），超发调用收到错误 ToolMessage 让模型重发（:291-321）。子研究员内部工具调用同样 gather 并发（:474-479）。汇总无显式 merge 节点：压缩结果各成一条 ToolMessage 回 supervisor；raw_notes 拼接绕过 supervisor 直接写父状态（:323-330）。

**与 gewu 对照**：gewu 子问题串行检索本地 SQLite（毫秒级），并行收益小；但「收集一批 → gather → 结果逐个包装回上下文」在 Go 里就是 `errgroup + SetLimit`，是接外部慢 IO（校方第三方 API）时的现成模式。ODR 的「超发即报错让 LLM 重试」比信号量排队粗糙，Go 实现用 semaphore 更干净。

### 3.3 可配置面

**机制**：三层——`init_chat_model(configurable_fields=...)` 全局单例 + 四角色模型独立配置（summarization/research/compression/final_report，`configuration.py:121-212`）；pydantic Configuration 每字段带 `x_oap_ui_config` UI 元数据，环境变量优先于 configurable（:236-247）；工具动态拼装 `[ResearchComplete, think_tool] + 搜索工具(4 选 1 枚举) + MCP 工具白名单`（`utils.py:569-597`）。MCP 走 `MultiServerMCPClient` 单 server + Supabase OAuth token 交换（`utils.py:250-524`，200 余行认证胶水）。**言行不一点名**：README 宣称支持 DuckDuckGo/Exa 等，现行 `SearchAPI` 枚举只有 anthropic/openai/tavily/none 四项（`configuration.py:11-17`），DDG/exa 依赖是 legacy 残留。

**与 gewu 对照**：同的是「配置对象 + env 覆盖 + 按名选模型」；gewu 不需要 UI 元数据与 MCP 生态，但**「工具白名单 + 动态拼装注册表」**形态可低成本借鉴——未来加 web 搜索/校方 API 时不改管线。

### 3.4 human-in-the-loop

**机制**：现行版**不用 `interrupt()`**（grep 仅 legacy 命中）：`clarify_with_user` 结构化判定需要澄清时 `Command(goto=END)` 吐出问题结束本轮（`deep_researcher.py:104-109`），用户下轮回复后图从 START 重跑，靠 checkpointer/thread_id 恢复多轮上下文；prompt 约束「已问过就几乎不要再问」（`prompts.py:12`）。legacy 才是 `interrupt()` 教科书用法（`src/legacy/graph.py:175-192`：`interrupt(msg)` → `Command(resume=True)` 恢复）。

**与 gewu 对照**：gewu 的 PhaseCollect→PhaseConfirm 状态机与 ODR 现行版「相位制 + 重入」异构同义。在 Go/无框架语境下 gewu 的选择正确——`interrupt()` 的价值依赖框架级挂起恢复运行时。可借鉴一点：ODR 的 clarify 是**可配置开关 + LLM 先判是否需要澄清**，gewu 若加「办理前澄清」可用小模型先判，避免每单多一轮往返。

### 3.5 评测与 Deep Research Bench

**机制**：仓库内全走 LangSmith：6 个 LLM-as-judge evaluator（judge 默认 gpt-4.1，`evaluators.py:8-10`）——overall_quality（6 子维度一次评出）/relevance/structure/correctness/groundedness（报告拆 claims 逐条对照 raw_notes，得分=grounded 占比，:146-151）/completeness，全部结构化输出 1-5 分；另有 pairwise（claude-opus-4 thinking 开 16000 预算）与 **supervisor 并行度精确断言**（断言首条消息 tool_calls 数 == 参考值，`supervisor_parallel_evaluation.py:10-17`）。Deep Research Bench：100 道 PhD 级任务（50 英 50 中、22 领域、专家手写黄金报告）；主指标 RACE（judge 先逐题动态生成 criteria 再四维加权打分）+ FACT（引用对爬源验证）；全量跑一次 $20~$100。

**与 gewu 对照**：gewu 无平台依赖的断言式评测在「办理」场景**比 LLM judge 更可靠更便宜**（ODR 系统内 eval 无一精确匹配业务事实），应保留为主体。可抄三点：编排行为精确断言（事件序列/子问题数/并行度）；groundedness 的「拆 claims 对照返回的 citations 而非自由发挥」；实验配置全量快照进报告 metadata。

### 3.6 可借鉴 / 应避免

**可借鉴**：① 超限三层退化（截断重试/摘要失败返原文）；② brief 式拆解提示词补「禁止臆造默认值」约束；③ think_tool 式 prompt 级预算话术（「简单查询 2-3 次封顶、连续重复即停」）备用；④ errgroup 并行模式；⑤ 编排行为断言评测；⑥ 「去重保真而非摘要」的压缩措辞。

**应避免**：LangGraph/LangSmith 平台绑定；LLM-as-judge 作为唯一质量口径；自造 interrupt 抽象；恒真异常分支式失败处理（并行化须 per-task 失败隔离）；反思文本混入证据（汇聚处按类型过滤）；文档与代码漂移；单 server MCP + OAuth 全家桶。

---

## 4. Rasa（P0：槽位收集，含 CALM 公开资料）

仓库 `RasaHQ/rasa` @ `c4069568`（main，2025-12-18；codeload tarball 获取）+ `rasa-sdk` @ `e4b6d12`。核心：`rasa/core/actions/forms.py`（737 行）、`actions/loops.py`、`actions/action.py`、`shared/core/{slot_mappings,trackers,events}.py`、`core/policies/rule_policy.py`。开源版 Apache-2.0；CALM 为商业 source-available，仅用公开文档。

**TL;DR**：开源 form 是 LoopAction 状态机——`ActiveLoop` 事件激活、`requested_slot` 做游标、`SlotSet` 事件流记账，**全部状态由事件列表重放导出**；校验/动态必填/确认流全是 custom action 约定而非内建；打断 = form 抛 `ActionExecutionRejection` 让位其他 policy，恢复靠 RulePolicy 改判 + `LoopInterrupted` 事件——槽位不丢，因为它们独立于 loop 生命周期。gewu 已是 CALM 范式的手工微缩版，主要缺口是**打断后丢会话**与**条件必填槽**。

### 4.1 Forms 状态机

**机制**：每轮生命周期：① policy 预测 form → `ActiveLoop(form)` 激活（`loops.py:54-55`），激活时先跑 `action_extract_slots` 预填（含 from_trigger_intent）再 `validate_<form>`（`forms.py:562-647`）；② 每条用户消息后 processor 自动执行 `ActionExtractSlots`（`processor.py:168`），遍历所有槽的 mappings 过五道闸：合法性→intent 匹配→mapping conditions→unique entity→取值（`action.py:1283-1325`）；③ `request_next_slot` 优先采纳 custom action 写入的 `SlotSet(REQUESTED_SLOT, x)`（动态表单），否则按 domain `required_slots` 顺序找第一个空槽，发游标事件 + `utter_ask_{slot}`（`forms.py:447-497`）；④ 必填全满 → 游标置 None → `ActiveLoop(None)` 退活（:684-717）。

关键证据——**开源版 required_slots 是静态列表，条件必填不在 domain 层**（`shared/core/domain.py:1829-1842`），条件只有两种实现：mapping `conditions`（`action.py:1107-1121`，active_loop + requested_slot 双条件，例如 `people` 槽只在问到 people 时才接受 `number` 实体）和 SDK 侧动态表单：

```python
# rasa-sdk/rasa_sdk/forms.py:315-329（节选）
missing_slots = (slot_name for slot_name in required_slots
                 if tracker.slots.get(slot_name) is None)
return SlotSet(REQUESTED_SLOT, next(missing_slots, None))
```

SDK `required_slots` 是可读 tracker 的 async 方法（文档「Dynamic Form Behavior」：`outdoor_seating is True` 时追加 `shade_or_sun`）。防重抽护栏：bot 已回话的轮次不再从旧消息抽槽（`action.py:1355-1362`）；同实体多槽消歧（unique entity mapping，`forms.py:123-178`）。

**与 gewu 对照**：gewu 无游标，每轮重算 `missingSlots` 取第一个缺失追问（`transaction.go:369-377`），`LastAsked` 只是离线解析提示。两边「每轮全量槽位抽取」同构；gewu 的「未收集才接受」（transaction.go:330-332）比 mapping conditions 更简单且天然防覆盖。**缺口是条件必填**：gewu 病假>3 天的证明提示只能塞在 confirm 摘要里（transaction.go:499-501），学 SDK「必填列表按已收集状态动态过滤」在 Go 里就是一个函数。unique-entity 消歧对应 gewu 双日期槽的「首个/末个」启发式（transaction.go:397-406），是已知薄弱点。

### 4.2 槽位校验

**机制**：core 只认挂载点（form 级 `validate_<form>`、全局 `action_validate_slot_mappings`），槽级 `validate_<slot>` 是 SDK 在 action server 进程里的反射分发（`rasa_sdk/forms.py:156-168`）。**默认放行语义**：

```python
# rasa/core/actions/action.py:1230-1235（节选）
# If the custom action doesn't return a SlotSet event for an extracted slot
# candidate we assume that it was valid. ... return a SlotSet(slot_name, None)
# event to mark a Slot as invalid.
```

校验失败返回 `{slot: None}` → 槽为 None → `_should_request_slot` 放行重问（`forms.py:557-560`）。「问过却没抽到」时 core 自动抛 `ActionExecutionRejection` 让位其他 policy（`forms.py:413-429`）——这是打断机制的入口。

**与 gewu 对照**：语义方向相反效果等价——Rasa 黑名单（默认有效，显式置 None 才无效），gewu 白名单（`normalizeSlot` 里 parser 接受才有效，transaction.go:471-481）。对日期/场馆这类有客观格式的槽，**gewu 的白名单更安全**。gewu 字段级失败恢复（业务层 `Field`+`Alternatives` → 只回退该槽并附当日可选项，transaction.go:594-616）信息量大于 Rasa 原生「置 None 重问」。建议：把「parser 拒绝=无效」写成显式契约注释，补跨字段前置校验（`end_date >= start_date` 目前靠业务层事后报错）。

### 4.3 打断与恢复（与 gewu 差距最大的一题）

**机制**四步：① form 让位（抛 `ActionExecutionRejection`，`processor.py:991-998` 记事件后继续预测，FAQ action 接管）；② RulePolicy 训练期构建 `RULES_FOR_LOOP_UNHAPPY_PATH` 查表，活跃 loop 中一般规则判 `action_listen` 时**覆写为 form 本身**自动续跑（`rule_policy.py:1062-1088`）；③ 预测附带 `LoopInterrupted(True)` 事件入 tracker（:1220-1232 → `trackers.py:344-351`）；④ form 回归后检查 `is_active_loop_interrupted` 为真则**不再校验那句话**（已被 FAQ 处理），用空槽调 validate 仅决定下一个问什么（`forms.py:540-543`）。

事件重放：tracker 无增量状态存储，**状态 = 事件列表顺序重放的纯函数**（`trackers.py:675-687`）。关键推论：**打断期间槽位不丢**——FAQ 轮只追加 UserUttered/BotUttered，form 的 SlotSet 原封不动。用户弃单才 `action_deactivate_loop`，且弃单也**不清业务槽位**只退 loop 清游标（`action.py:653-654`）——槽位复用是刻意设计。

**与 gewu 对照**：gewu 的续轮三分类里 **new_topic 和 cancel 都 `Sessions.Clear`，取消即丢**（pipeline.go:73-84、transaction.go:634-694）。gewu 的 `TxSession.Slots` 结构上已支持「槽位生命周期独立于流程」，只是 Clear 调早了。修法（十几行）：new_topic 时不清 session 改标记 `Interrupted=true` 保留 TTL，下次办理意图出现时问「继续上次的预约吗？」

### 4.4 确认流

**结论：开源版没有内建预提交确认**（forms.py/loops.py grep "confirm" 零命中；form 终止条件只有必填填满）。官方标准做法三条：① `utter_submit` 是事后播报不是闸门（官方 Submit 规则排在 `active_loop: null` 之后，docs/forms.mdx:120-135），防误写的实质是**分层**——form 只产 SlotSet 事件无副作用，写操作只能在 form 退活后由独立 action 执行；② 社区标准「确认槽」模式：`final_confirmation` 布尔槽作最后一个必填槽；③ two-stage fallback 的 affirmation 确认的是意图不是数据。

**与 gewu 对照**：gewu 的 `PhaseConfirm` 是一等公民阶段且强于开源 Rasa 默认形态：确认摘要含派生字段（请假天数→审批层级，transaction.go:492-503）、`pending_action` 结构化确认卡（:512）、确认阶段可直接说修改项（:534-557）。「form 只填槽无副作用」的分层 gewu 已满足（写操作唯一出口 execute→CallTool + 工具层权限拦截）。注意点：确认正则 `确认|确定|好的|可以|提交|是的|对`（transaction.go:522）较宽——纯「好的」紧跟二次摘要会直接提交，可接受但值得知晓。

### 4.5 Rasa Pro CALM：LLM 与确定性流程的分工

**机制**（公开文档）：LLM 侧 Command Generator 只做一件事——读完整对话上下文，输出**结构化命令序列**（`StartFlow("transfer_money")`/`SetSlot("amount",100)`/`CancelFlow`）；确定性侧 FlowPolicy 是「a state machine that **deterministically** executes the business logic defined in your flows」。安全边界（CALM 论文 arXiv:2402.12234）："the commands the LLM generates **do not directly manipulate the dialogue stack**"。流程原语 `collect`（问槽，`rejections` 校验失败自动重问）/`action`/`link`/`set_slots`；常用偏差内建为 patterns：`pattern_cancel_flow`（取消前确认）、`pattern_correct_slot`（槽位修正确认）、`pattern_clarification` 等，并有 `ask_before_continue` 续流前询问。

**与 gewu 对照**：gewu 已经是 CALM 范式的手工微缩版——LLM temperature 0 + JSON mode 出结构化命令（transaction.go:290-309/439-468/641-648），Go 状态机独占 phase 迁移与槽位写入。差异：CALM command generator 看完整历史+全部流定义，gewu 只看当前流程字段+已收集槽（对槽填充够用）；CALM 有流栈支持嵌套，gewu 单活跃流程。**gewu 没必要引入 CALM 本体（商业许可 + Python 全家桶），但 patterns 目录是值得照抄的 UX 清单。**

### 4.6 可借鉴 / 应避免

**可借鉴**：① 打断不即丢（最有价值、成本最低）；② 动态必填槽函数；③ 显式校验契约 + 跨字段前置校验；④ 会话内 append-only 轮次事件日志（轻量版 tracker，评测失败定位哪轮抽错）；⑤ 冲突重问拼 Alternatives 推广为「带上下文追问」通道；⑥ CALM patterns 清单（取消前确认/修正后二次确认/续流前询问）。

**应避免**：policy/RulePolicy/TED 训练（9 个工具上 ML 对话管理是纯负担）；mapping conditions YAML DSL（代码项目直接写 Go）；action server 远程校验往返；event sourcing 全量持久化 tracker（30 分钟 TTL 内存 map 足够）；slot 特征化体系；照搬「utter_submit 在退活后」形态（gewu 的强制确认更强，勿倒退）；CALM autonomous/ReAct step（beta 且与「LLM 只找表述」原则相悖）；多流程栈。

---

## 5. GPT Researcher（P1）

仓库 `assafelovic/gpt-researcher` @ `5cdad9cb`（2026-06-23）。核心在仓库根 `gpt_researcher/`（`backend/` 下是 FastAPI server 与 report_type 编排层）。

**TL;DR**：默认模式是**无迭代一轮流水线**（LLM 拆 3 条子查询 → 并发搜索/抓取/embedding 压缩 → 单次写报告），「深度」来自 DeepResearchSkill 递归扇出（breadth÷2、depth−1）；引用完全靠提示词写 markdown 超链接，**代码层无编号无校验无防编造**；评测是 SimpleQA 式 LLM grader（100 题 accuracy 0.929）。对 gewu 最有价值：分层信号量并发、小内容跳过压缩的快速路径、三档判分提示词。

### 5.1 研究循环

**机制**：标准模式 `conduct_research()` 一轮即止；`MAX_ITERATIONS=3` 名为迭代实为「生成几条子查询」（`prompts.py:248`："Write {max_iterations} search queries..."）。每条子查询：多 retriever 搜索（各 top5）→ `visited_urls` 去重 → 并发 scrape → embedding 相似度压缩取 top10（`skills/context_manager.py:61-63`）→ 汇总单次 `write_report()`。Deep 模式递归：`depth>1` 时 `new_breadth = max(2, breadth//2); new_depth = depth-1`（`skills/deep_research.py:324-344`），每叶节点**新建完整子 GPTResearcher 实例**（:246-261），默认 breadth=3/depth=2/concurrency=4 即 9 个子研究（`config/variables/default.py:36-38`）；入口先生成澄清问题并**自动作答** "Automatically proceeding"（:378-384）。**无任何「信息是否充分」的质量判停——所有终止都是计数/深度驱动。**

**与 gewu 对照**：gewu 一轮式与 gptr 默认模式同构；gptr 的 `parent_query - question` 拼接与 gewu 指代消解同思路。若加深度，`breadth÷2 + depth−1` 是最简加档方式，但成本指数级，gewu 不必上。

### 5.2 引用机制

**机制**：来源 = `visited_urls` set；正文引用纯提示词约定（`prompts.py:309` 要求 markdown 超链接 `([in-text citation](url))`）；`add_references()` 只做全量追加无映射（`actions/markdown_processing.py:94-108`），且该函数在主 HTTP/websocket 流程中**未被调用**。**言行不一点名**：README 宣称 "research reports with citations"——代码不校验内联 URL ∈ visited_urls，正文引用与来源列表可以完全脱节。

**与 gewu 对照**：gewu 的结构化 citations 事件 + 强制 `[n]` 严格优于 gptr。反向启示：gewu 检索自本地语料，**天然可校验**——评测可加「引用 [n] 必须落在证据集内」的代码断言。

### 5.3 上下文管理

**机制**：无持久向量库（"Memory" 类名不副实，只是 embedding provider 工厂）；每子查询临时构建 chunk(1000/overlap100) → EmbeddingsFilter → top10 管线。小内容快速路径跳过 embedding（`context/compression.py:158-171`，总字符 <8000 且条数不超限则直通）。长任务压缩 = 硬词数截断 `MAX_CONTEXT_WORDS=25000` 保留最新条目（`deep_research.py:15,23-37`）。**言行不一点名**：`default.py:6` 定义 `SIMILARITY_THRESHOLD=0.42`，`compression.py:119` 实际 `os.environ.get(..., 0.35)`——配置没走单一注入路径。

**与 gewu 对照**：gewu 条数上限（≤12）对长 chunk 不设防——gptr 的**总词数上限**值得抄；「小内容跳过处理」的快速路径同理。

### 5.4 并发

**机制**：三层两信号量——子查询层无限制 `asyncio.gather`；scrape 层 `ThreadPoolExecutor + Semaphore`（默认 15 worker）+ **全局单例 rate limiter**（`utils/workers.py:24-44`）；deep 层 `Semaphore(4)`。多 retriever 搜索反而串行（`researcher.py:773-785`）。

**与 gewu 对照**：Go 主场——`errgroup.WithContext + SetLimit(n)` 一行等价；gewu 子问题并行检索是最廉价的提速点（本地检索无风险，`SetLimit(4)` 起步防 SQLite 读锁竞争）。

### 5.5 evals

**机制**：simple_evals：100 题串行跑全流程，gpt-4-turbo 温度 0 按 **CORRECT/INCORRECT/NOT_ATTEMPTED 三档**判分（`simpleqa_eval.py:14-93`），历史留档 accuracy 0.929（logs 入 git）。hallucination_eval 仅 2 题样本。**言行不一点名**：README 称数据集本地维护，`simpleqa_eval.py:107` 实际从 openaipublic 远端拉 CSV，本地文件零引用。

**与 gewu 对照**：gewu 断言式评测维度上更贴业务且可复现；可抄三档判分措辞（若加 LLM 抽检）、**成本/题与耗时/题统计**、评测日志入 git 留基线。

### 5.6 可借鉴 / 应避免

**可借鉴**：① 小内容跳过处理快速路径；② 总词数/token 上限兜底；③ errgroup.SetLimit 分层并发；④ 成本/题与耗时/题进评测报告；⑤ 三档判分提示词模板；⑥ 评测结果入 git 留基线。

**应避免**：提示词即引用无校验（gewu 应反向加断言）；"Memory" 名不副实的临时向量管线；配置定义与使用脱节（单一注入路径）；递归深研指数成本；评测数据集言行不一（钉死本地）；visited_urls 全量追加 References（citations 只含真正被引证据）。

---

## 6. STORM（P1）

仓库 `stanford-oval/storm` @ `fb951af`（2025-09-30 后无新提交；镜像克隆，hash 交叉验证）。代码 `knowledge_storm/storm_wiki/`，Python + dspy。

**TL;DR**：STORM = persona 多路并行 × 每路多轮追问的对话式调研，再走 outline→逐节写作→润色长文管线；引用靠「局部编号→URL 全局映射→按出现顺序重排」的**三层确定性后处理**（含越界引用删除、未引用证据剔除）。与 gewu 一次拆解一轮综合差一个量级；对 gewu 最有价值的是引用后处理三件套与两级证据结构，最不可搬的是 persona 对话成本。

### 6.1 多视角提问

**机制**：persona 三步生成——LLM 从 topic 找相关 Wikipedia 文章（`FindRelatedTopic`）→ 抓页面提取 Title+TOC → `GenPersona` 产出编号的 Wikipedia 编辑角色列表（`modules/persona_generator.py:56-65`）；**硬编码默认 persona "Basic fact writer" 无条件排第一**保底基础事实（:151-153）。使用侧每 persona 并行跑多轮对话（默认 3 persona × 3 轮）：WikiWriter 带身份提问（`AskQuestionWithPersona`，knowledge_curation.py:139-151）→ TopicExpert 转搜索 query → 检索作答，下轮可看前几轮追问；超过 4 轮的旧答案直接以 "Omit the answer here due to space limit" 丢弃省 token（:102-113）。

**言行不一点名**：`--disable-perspective` 是死参数（engine.py:150-153 定义，:224 硬编码 `disable_perspective=False`）；论文的 evaluate 源可靠性步骤已被简化为「每结果只取第 1 条 snippet」（knowledge_curation.py:218 注释自认）。

**与 gewu 对照**：gewu 是「静态平面拆解」，STORM 是 persona×turn 二维扩展（视角=广度、追问=深度），代价是默认配置 20+ 次 LLM 调用/主题。低成本借鉴：拆解提示词里让 LLM 隐式多立场提问（「学生/教务/技术视角各提一问」），一次调用拿到视角多样性。

### 6.2 检索与证据组织、写作

**机制**：四阶段（engine.py:341-441，各自可独立运行并从磁盘恢复上游产物）：research（persona 对话即调研，检索结果逐轮存 DialogueTurn）→ curate：`construct_url_to_info` 按 URL 聚合所有对话检索结果、snippets `set()` 去重（`storm_dataclass.py:65-80`）→ outline 两步（参数知识草稿 + 全部对话历史增强改写）→ 逐节并发写作：每节 query = 该节及子节标题列表，对 **snippet 向量索引**（写作前临时建 SentenceTransformer 索引）余弦检索 top-k，证据局部编号喂给 WriteSection（`article_generation.py:104-152`）→ polish（lead 生成 + 可选去重）。

**与 gewu 对照**：gewu 混合检索（BM25+向量+RRF）比 STORM 的朴素并集检索完整（STORM 无 RRF）；STORM 的「证据持久累积结构 + 写作时按节按需检索」是 gewu 答案变长时的平移路径（对已收集 chunk 做余弦重排即可，无新依赖）。outline/长文管线对几百字问答是纯开销。

### 6.3 引用与去重

**机制**：三层——① 写作时局部编号 + prompt 强制行内引用（`article_generation.py:166-168`）；② 节完成后 `update_section`：**越界引用号删除**（引用号 > 该节证据数则抹掉）、**未引用证据剔除**、`_merge_new_info_to_references` 按 URL 映射全局编号（同 URL 永远同号，`storm_dataclass.py:267-288,193-204`），用 PLACEHOLDER 两阶段替换防链式覆盖（`utils.py:541-550`）；③ 全文完成后按引用出现顺序重排全局编号并删未引用来源（:374-412）。另有 `[1, 2]→[1][2]` 归一化、组内去重、截断到最后一个带引用完整句（utils.py:382-425）。

**与 gewu 对照**：同方向（编号+prompt 强制+后处理），gewu 单次生成编号天然全局一致，但 LLM 幻觉出 `[9]`（证据只有 8 条）或引用从未支撑正文的证据时无兜底。**三件套是纯正则+map，Go 几十行可复刻，是 gewu 引用可信度最便宜的加固点。**

### 6.4 可借鉴 / 应避免

**可借鉴**：① 引用后处理三件套 + PLACEHOLDER 两阶段替换 + 引用归一化（全套照抄）；② 证据两级结构（doc 级共享引用号 + chunk 级召回，减少参考文献重复条目）；③ 分阶段可重入 + 中间产物落盘（SQLite 存拆解/证据/草稿三张表，可回放可审计）；④ 「证据不足就明说」的专家提示词（gewu 已有，STORM 佐证是社区收敛实践）；⑤ 多模型成本分级思想。

**应避免**：persona 多轮对话（20+ 调用/主题）；Wikipedia TOC 外部依赖（且其抓取无 timeout 无重试）；`httpx.Client(verify=False)` 禁证书校验（安全反模式）；dspy+SentenceTransformer+Qdrant 重运行时栈；正则解析 LLM 列表输出（坚持结构化 JSON）；死参数不接线（配置要有接线测试）；无 RRF 的朴素并集融合。

---

## 7. AgentTOD / DIMF（P1：论文研读）

**证据基础声明**：AgentTOD（TOIS 2025, 10.1145/3745021）正文在付费墙后未读到；以其**同作者同方法线的 ACL 2024 会议版前身 AutoTOD 官方仓库**（github.com/DaDaMrX/AutoTOD，逐文件研读）为代码证据；AgentTOD 无 arXiv 版、无官方代码仓库（`DaDaMrX/AgentTOD` 404，已验证）。DIMF 读到 arXiv HTML 全文含附录 prompt（arxiv.org/html/2505.14299v1）；**DIMF 无官方开源实现**（论文无链接，GitHub 检索无果）。

**TL;DR**：两文代表两个极端——AgentTOD/AutoTOD 用**单 agent 一次 LLM 调用 + ReAct 文本协议**直连业务 API（彻底删掉意图分类/DST）；DIMF 反其道用**三个领域无关 agent（意图/槽位/回复）三次调用分步**，消融证明小模型下分步远胜单次（Combined 58.9→97.7）。评测上 AutoTOD 最严谨：**成败由独立预订 SQLite 的约束查询断言**。gewu 的「路由/抽取两次调用 + 库状态断言」与两文最佳实践同构。

### 7.1 槽位填充 agent 设计

**AutoTOD**：单 agent ReAct，LLM 输出 `Thought/Tool Name/Tool Input(JSON)` 文本协议；查询工具是 LangChain SQLDatabaseChain **让 LLM 生成 SQL 直查业务库**（agent.py 带 `clean_sql` 补丁，暴露脆弱性）；预订工具是确定性 Python 函数（booking.py `make_booking_db`：校验必填/类型/实体存在→写独立预订库→返回 8 位 reference number，失败原因文本回传 LLM），但参数用正则 `key: value, ...` 解析（`extract_book_info`）。

**DIMF**：解耦 = 「固定三 agent 流水线 + 领域知识只经 prompt 注入」。Slot Filling Agent（论文 §3.2 + 附录 Table 6）每轮输出 `Question / Action / Parameters(JSON) / Information(list)` 四段，显式区分 **Tool Parameters**（有值参数槽→填 API）与 **Tool Information**（无值信息槽→答给用户）；DB 检索由**规则代码**执行（非 LLM 生成 SQL）。

**与 gewu 对照**：DIMF 槽位 agent 与 gewu 抽取几乎同构，且两者都把业务查询留在确定性代码；gewu 优于 AutoTOD 的激进路线（LLM 直拼业务 SQL 在有权限矩阵的场景绝不可行）。

### 7.2 意图分类与槽位填充分工

**AutoTOD**：一次调用做全部，无意图分类无 DST（「意图」被吸收进 API schema 选择）。**DIMF**：三步三次独立调用；关键设计是 Intent Classification Agent **只看当前用户问题、不看对话历史**（§6.5 案例明确），prompt 含各域描述 + 'other' 结束意图 + 基于上一轮意图的任务逻辑规则（附录 Table 5）。消融（Table 3）：单 agent Combined 58.9 → 双 agent 85.7 → 三 agent 97.7。

**与 gewu 对照**：gewu 路由+抽取两次独立调用正是 DIMF 结构简化版；refusal ≈ DIMF 'other'。**DIMF Table 3 是 gewu「flash 小模型 + 分步调用」设计的直接文献支撑**；其「意图分类去历史」值得 gewu 做路由瘦身实验。

### 7.3 任务完成评测

**AutoTOD**（AgentTOD 沿用）：MultiWOZ 2.1 测试 1000 对话，GPT-4 用户模拟器驱动；指标 Inform/Success/Combined/Book rate。断言是「LLM 抽取 + 确定性库断言」混合：judge 只负责从对话抽事实（venue 名、reference number）输出 JSON；成败全在代码——`query_booking_by_refer_num` 用 LLM 抽出的单号**反查独立预订库**并逐约束匹配（evaluate.py `evaluate_by_domain`）；纯离线路径更有**双向断言**：`success_match = set(match.keys()) == set(book_result.keys())`（metric.py——该订的域全订了且没多订）。

**DIMF**：MultiWOZ 2.2 全量 10437 对话，官方 Inform/Success/BLEU **文本匹配**评测，无业务库断言。

**与 gewu 对照**：gewu「驱动完整对话→断言业务库真实状态」与 AutoTOD 同源且更彻底（免除「LLM 从文本抽单号再反查」环节，直接断言库内记录）；gewu 规模小两个量级但断言维度（冲突恢复/权限拦截/审批层级）是两文均未覆盖的。**可抄：AutoTOD 的「期望单据集合 == 库中实际产生单据集合」双向断言。**

### 7.4 可借鉴 / 应避免

**可借鉴**：① DIMF 参数槽/信息槽二分（schema 加 `kind` 字段，零运行时成本）；② DIMF 意图分类去历史（路由输入裁剪实验）；③ AutoTOD 双向单据断言；④ TrajsTOD 最小标注思想——评测运行时把每次 LLM 输出+API 调用落 JSONL 轨迹，零成本积累回归集（AgentTOD 证明此格式足以训练 7B 模型）；⑤ DIMF Table 3 消融写进设计文档作选型依据。

**应避免**：LLM 生成 SQL 直查业务库；ReAct 文本协议+正则解析（值含逗号即碎）；单次调用全做路线（小模型上崩塌）；MultiWOZ 式 BLEU 文本匹配评测；GPT-4 用户模拟器入主断言集；无库 mock 业务计入完成率。

---

## 8. Eino（P2：Go 编排抽象对照）

模块 `github.com/cloudwego/eino v0.9.19`（2026-09-01 tag；commit hash 因网络不可得）。编排抽象在 `flow/`（compose/chain·graph·workflow·agent·adk）与 `components/`；本体不含模型客户端（在 eino-ext）。

**TL;DR**：Eino 是「编译期类型检查的泛型图编排 + 四态流式 Runnable + checkpoint/中断恢复 + 分层 Agent」框架，能力明显超出 gewu 的「路由+两级管线」需求。**结论：不整体迁移**——gewu 的 SSE 事件契约（前端+评测双重契约）与零重依赖立场都不支持；但 `schema.StreamReader` 的背压取消协议、带类型的 token 回调负载值得记下，未来转 LLM 原生 tool-calling 时可整件引入 `flow/agent/react`。

### 8.1 Chain/Graph/Workflow 抽象

Chain 是 Graph 的链式语法糖（`compose/chain.go:37-44`，仅持 `gg *Graph`）；Graph 泛型有向图支持 Pregel（允许环，`WithMaxRunSteps` 防死循环）与 DAG（编译期检环）两种模式（`graph.go:45-50,1128-1129`）；Workflow 是「依赖+字段映射」换皮（`workflow.go:43-50`，`MapFields("user.name","displayName")` 做字段路径映射）。编译产物为四模式 `Runnable[I,O]`（Invoke/Stream/Collect/Transform，`runnable.go:32-37`）。**checkpoint 完整存在**：`CheckPointStore` 接口（`internal/core/interrupt.go:27-30`）经 `WithCheckPointStore` 注入，checkpoint 含图通道、节点输入、State、子图与中断点状态（`checkpoint.go:108-119`）；自定义类型须 `schema.RegisterName[T]` 注册序列化。配套 `compose.Interrupt/ExtractInterruptInfo` + resume。

**与 gewu 对照**：gewu RunChat 是手写线性分发（pipeline.go:60-142），transaction 是业务状态机而非编排图。Eino 的 Pregel 环/字段映射/checkpoint gewu 一个都用不上——与 architecture.md:53 声明的演进边界一致。checkpoint 只在「人工确认节点 + 跨轮恢复」需求真出现时才兑现，届时也应先评估「SQLite 直存 TxSession」的轻方案。

### 8.2 流式与插桩

`schema.Pipe` 创建 channel 式 StreamReader/Writer（一对一，`stream.go:99-102`）；**取消不依赖 ctx**——消费端 `Close()` → `close(s.closed)`，阻塞中的生产者 select 立刻拿到 `closed=true` 退出（stream.go:410-441）；fan-out `Copy(n)`（:261-275, 792-821）。Graph 主循环每步检查 `ctx.Done()`（`graph_run.go:250-261`）。插桩靠 callbacks 5 钩子（`internal/callbacks/interface.go:26-48`），流式观测用**流的 Copy 分身**不侵入原始流（`inject.go:143-161`）；模型组件有带类型的回调负载（TokenUsage 等，`components/model/callback_extra.go`）。

**与 gewu 对照**：gewu 的 emitFn 同时扮演流通道+插桩两个角色，「emit 返回错误即中止」等价于 Close 反向通知（借 ctx），单消费者场景下不需要 Copy/Convert/Merge。可记两点：未来「一个流两个消费者」（边 SSE 边落库/评测旁路）直接抄 Copy(n) 的 once+链表广播；budget 计量可对齐 `model.CallbackInput/Output` 的形状。

### 8.3 Agent 抽象与迁移判断

组件层 `ToolCallingChatModel.WithTools`（不可变并发安全；BindTools 因原地改被标 Deprecated）+ `InvokableTool`（参数 JSON 字符串）；`flow/agent/react` 是纯 Graph 搭的 ReAct（model→流式分支→tools 成环，`react.go:329-378`，`WithMaxRunSteps` 默认 12 防失控）；ToolsNode 默认**并行**执行多条 tool call（`tool_node.go:208-210`）；adk 层 TypedAgent 返回 AsyncIterator[AgentEvent]，多 agent 推荐「agent 当工具」（`NewAgentTool`）；官方自己标注 transfer 式多 agent "has not proven to be more effective empirically"（`interface.go:469-473`）。

**迁移判断**：不值得整体迁移（事件契约不同构、图能力用不上、依赖足迹 sonic/gonja 等与单二进制立场冲突、eino 本体不含 OpenAI 客户端）。**条件性引入一件**：gewu 若从「正则识别工具+确定性槽位」转向 LLM 原生 tool calling，`react.NewAgent` 一个构造函数给全并行工具执行/MaxStep/流式 tool-call 判定（注意 `StreamToolCallChecker` 首 chunk 判定对 OpenAI 兼容流基本可用，Claude 类需自定义）。

---

## 9. Dify / FastGPT（P2：简短综述）

Dify @ `65b4091`（2026-09-03，workflow 引擎已拆为外部包 graphon==0.7.0）；FastGPT @ `308a5cd`（2026-08-31）。

**Dify**：Python/Flask + PostgreSQL 多服务重平台。节点 = 字符串类型 + Pydantic 配置 + 版本化注册表（`api/core/workflow/node_factory.py:127`，importlib 懒加载）；DSL 是 JSON（nodes+edges+sourceHandle），draft/version 两态落库。意图分类节点 = 类别 id 当分支 handle（`question_classifier_node.py:447`）。混合检索：每数据集 embedding/全文两路并行→去重→**加权分数（`vector_weight*cosine + keyword_weight*关键词分`）或 rerank 模型融合**——**当前 main 无 RRF**（全仓 grep reciprocal 零命中，1.x 的实现已删）。引用经 `RunRetrieverResourceEvent` 事件流回传 SSE。多租户：全模型 tenant_id 贯穿 + 检索层 `WHERE tenant_id==... AND id IN (...)`（`dataset_retrieval.py:2048-2058`）。

**FastGPT**：TypeScript monorepo + MongoDB。节点 40+ 种，运行时集中分派表 `callbackMap`（`dispatch/constants.ts:40-58`）；意图分类用**跳过法**路由（返回未选中分支的 skipHandleId，遍历器跳过，`classifyQuestion.ts:88-92`）；发布走 app_versions 版本快照可回滚。检索是**加权 RRF（k=60）**：`score = weight * (1/(60+rank))`（`dataset/search/utils.ts:19-21`），双路召回（embedding 80 + full-text 60 条）→ 去重→阈值→token 预算；引用用 `[id](CITE)` 内联标记（`AIChat.ts:17-19`）。权限亮点：**检索节点 authTmbId 按当前对话用户过滤数据集**，无权库静默剔除（`dataset/utils.ts:128-153`）。另有 `datasetDeepSearch` 有界深搜循环（与 gewu Deep Research 同型）。

**对 gewu 的产品化启发**：① **「配置驱动 + 固定执行器」**是泛化为「任意学校可配置」的最省力路径——路由抽 `classes:[{id,name,instruction}]`、办理流程抽「槽位 schema+确认点+回执模板」声明式配置，Go 只写一次执行器，不需要可视化编辑器；② **配置版本化快照**（SQLite 存配置+hash，评测绑定配置版本，失败可定位语料 vs 配置）；③ FastGPT 加权 RRF 一行核心，过滤顺序（去重→阈值→token 预算）值得照抄；④ 引用回传标准化（稳定 id + 内联标记 + SSE 附来源列表）；⑤ 权限对称扩展：工具层已有，私有语料出现时在检索出口加一道即可；⑥ Dify 的 LLM 生成 workflow（`core/workflow/generator/`）启发「办事指南 Markdown → 办理流程配置 + 人工审核」的 ingest 升级。

**应避免**：完整多租户（每校一套部署的私署模式不需要）；可视化编排前端（ReactFlow 画布+节点 UI 元数据是最大维护负担）；插件生态与多 provider 抽象；为多租户高并发设计的检索线程模型。

---

## 10. 横向对比表

| 项目 | 路由 | 混合检索 | 研究链路 | 槽位与确认 | 权限 | 评测 | 流式与并发 |
|---|---|---|---|---|---|---|---|
| **gewu** | LLM 五分类 JSON + 启发式降级 | BM25 字符二元语法 + 向量等权 RRF k=60 | 固定拆 2~4 子问题、一轮即止、≤12 条去重 | LLM 抽取 + 确定性 parser + 确认相位状态机 + 字段级回退 | 工具层单一出口（角色矩阵） | 26 题断言业务库状态 + 引用召回 | emitFn 逐事件 SSE，ctx 取消传播，串行 |
| **Onyx** | 无路由，用户手动开 DR | 索引内归一化加权 + 跨查询加权 RRF k=50 | orchestrator×subagent 8×8 循环 + 总时钟强制收敛 | 无（通用问答） | chunk 冗余 ACL + 索引内 weightedSet 过滤 | Braintrust + 手工回归 + prompt 路径触发 CI（非阻塞） | 多查询并行；流式引用正则改写（含全角括号） |
| **ODR** | 无（单一任务入口） | 无本地检索（web 搜索工具） | supervisor 循环 + 并行子研究员 + 边界保真压缩 + 三层超限退化 | interrupt 仅 legacy；现行 END+重入（相位制） | 无 | LangSmith 6 个 LLM judge + DRB RACE/FACT | asyncio.gather ≤5；编排行为精确断言 |
| **Rasa/CALM** | pipeline 意图分类 / CALM LLM 命令生成 | 无（core 不管 RAG） | 无 | requested_slot 游标状态机 + validate 重问 + 打断恢复（槽位独立于 loop） | 无内建 | 无内建 eval | 单轮同步事件；状态=事件重放 |
| **GPT Researcher** | 无（LLM 直接拆 3 子查询） | 无本地（检索后 embedding 过滤压缩） | 一轮流水线 + deep 递归 breadth÷2/depth−1 | 无 | 无 | SimpleQA 式 LLM grader 三档判分 + 成本/题 | 三层两信号量（查询/抓取 15/deep 4）+ 全局限速 |
| **STORM** | 无 | 写作前 snippet 向量重排（无 RRF 朴素并集） | persona×多轮对话→outline→逐节→润色，阶段可重入 | 无 | 无 | 无系统内 eval | persona 线程池并行；引用三层后处理 |
| **AgentTOD/DIMF** | DIMF 意图 agent（去历史）三步分步 / AutoTOD 单次全做 | 无 | 无 | ReAct 文本协议→API / 三 agent JSON 分步 | 无 | AutoTOD：预订库约束断言（含双向集合断言）；DIMF：MultiWOZ 文本匹配 | 逐轮串行 |
| **Eino** | —（框架层） | —（组件接口） | react/adk 图 + MaxStep 防失控 | adk checkpoint + Interrupt/Resume 原语 | — | — | StreamReader 背压取消 + Copy 分身 + callbacks 5 钩子 |
| **Dify/FastGPT** | question-classifier / classifyQuestion 节点（类别=分支/跳过法） | 每库两路并行→加权分数或 rerank（Dify 无 RRF）/ FastGPT 加权 RRF k=60 + token 预算 | datasetDeepSearch 有界循环 | human-input / userSelect 节点（暂停-恢复状态） | 租户检索层过滤 / authTmbId 按对话用户过滤 | 平台内置标注评测 | ReactFlow DSL + 节点分派表，SSE 事件 |

---

## 11. 给 gewu 的行动建议（Top 10，按价值/成本排序）

1. **打断不丢流程**（Rasa 槽位独立于 loop 生命周期 + CALM ask_before_continue）→ `internal/agent/pipeline.go:82` default 分支不再 `Sessions.Clear`，改 `sess.Interrupted=true` 保留 TTL；`transaction.go` StartFlow 入口检测中断会话并问「继续上次的 X 吗？」→ **小**（~30 行 + 1 评测用例）
2. **引用后处理三件套**（STORM `storm_dataclass.py:267-288,374-412` + Onyx `citation_processor.py:204-213`）→ direct.go / research.go 生成答案后：删越界 `[n]`、剔除未被引用证据、归一化 `【n】`/`[1,2]`；citations 事件只含被引证据 → **小**（~50 行纯正则，`internal/agent/citations.go` 新文件）
3. **研究/办理链路总时钟强制收敛**（Onyx `dr_loop.py:85,441-451`）→ RunResearch / StartFlow 顶层 `context.WithTimeout`，超时强制走「基于已有证据直接作答」分支，防 LLM 循环失控 → **小**（~20 行）
4. **动态必填槽**（Rasa SDK `required_slots` override）→ `transaction.go` 的 `missingSlots` 升级为 `func requiredSlots(flow, slots) []string`：病假>3 天追加证明编号槽、请假类型=事假追加课程信息槽 → **小**
5. **prompt 路径触发的非阻塞评测 CI**（Onyx `pr-connector-filter-eval.yml` 三原则）→ GitHub Actions：`internal/agent/prompts.go|router.go` 变更触发跑 `eval/run_eval.py`（零 key 模式 26 题），`continue-on-error: true`，附 per-case 重试 → **小**
6. **证据总量兜底**（GPT Researcher `deep_research.py:15,23-37` 词数上限 + ODR 超限退化）→ `research.go` 聚合在 ≤12 条之外加总字符上限（如 24k chars），超限按子问题轮转丢弃并重试生成一次 → **小**
7. **子问题并行检索**（GPT Researcher/ODR 并行模式 → Go `errgroup.SetLimit(4)`）→ `research.go` 每子问题 Search 并发化，SSE step 事件按完成序发出 → **小**
8. **评测双向断言 + JSONL 轨迹落盘**（AutoTOD `metric.py` 集合相等 + TrajsTOD 最小标注）→ eval 增加「期望单据集合 == 业务库实际产生单据集合」（防该订未订/不该订乱订）；RunChat 侧每次 LLM/工具调用落 `data/traces/*.jsonl` 供失败定位与未来回归集积累 → **中**
9. **路由瘦身实验**（DIMF 意图 agent 去历史，Table 3 消融支撑）→ `router.go` 路由调用输入裁剪为「当前问题 + 上一轮路由结果」，26 题集 A/B 对比引用召回与路由准确率 → **小**
10. **配置版本化快照**（Dify workflows draft/version、FastGPT app_versions）→ 启动/ingest 时把语料集合 + 流程定义的 hash 写 SQLite；评测报告绑定配置 hash，失败可定位「语料问题还是配置变更」→ **中**

**明确不做**（同样重要）：不迁移 Eino/LangGraph（事件契约与依赖足迹都不支持；LLM tool-calling 需求出现时再局部引入 `flow/agent/react`）；不上多租户与可视化编排（私署 + YAML 配置即 DSL 足够）；不把 LLM-as-judge 当主评测口径（断言业务库状态更硬更便宜）；不学 Onyx「空结果交 LLM 发挥」（确定性拒答是办事场景的正确哲学）；迭代式深研（Onyx 8×8 / gptr 递归 / STORM persona）在校园问答的延迟预算内不适用——若未来加档，优先选 gptr 的 breadth÷2+depth−1 一次性参数化形态并配总时钟。
