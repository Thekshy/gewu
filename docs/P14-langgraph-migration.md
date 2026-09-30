# P14 LangGraph 迁移（SPEC + ticket + 验收）

> **背景**：gewu 升级为毕设项目（论文题《基于 RAG 与 LangGraph 的校园制度智能问答 Agent
> 研究与实现》），同时保留求职展示与 agent 学习项目的定位。现有 Go 单体（cmd/server +
> internal/）的编排层全量迁移为 **Python + LangGraph**，Go 后端按 P8 微服务退役的模式
> 留档后删除。
>
> 原则（沿用本仓库方法论）：
> 1. **PARITY.md 是唯一行为契约**——SSE 十类事件（route/status/step/answer_delta/
>    citations/slot_question/pending_action/action_result/error/done）与 done.reason
>    四值（completed/max_tokens/error/aborted）只增不改，前端 apps/web 三页面零改动。
> 2. **存储层零改动白嫖**——P12 已把读写收口 PG 存储函数（rag_fts_search /
>    rag_upsert_doc / halfvec HNSW），Python 侧只做调用方。
> 3. **同一 golden set 说话**——eval/ 三份数据集（28 题主集 + 8 题 agent 集 + 41 条
>    检索查询）跨语言复用，迁移前后指标对照留档 eval/reports/。
> 4. **真跑门禁**——每 ticket 一门禁，评测/单测/CI 全绿才过关，结论落 ADR 与执行记录。

## 0. Grilling 拍板结论

| # | 问题 | 拍板 | 依据 |
|---|------|------|------|
| Q1 | LangGraph 语言栈 | **Python** | 主生态（checkpointer/interrupt/预置 agent）、论文文献与评测工具链（ragas 等）均在 Python；eval/run_eval.py 已有 Python 基础 |
| Q2 | Go 编排链路命运 | **全量替换退役** | 毕设主线唯一化，避免双栈长期维护；对照实验改为「迁移前后评测报告对照」（历史基线已留档 eval/reports/），git tag 保底可回溯 |
| Q3 | 服务形态 | **Python 直接接管 :8000** | Go 删除后 Python 服务实现 PARITY 同款 6 端点，前端 API_BASE 缺省值不变即零改动；双轨期 Python 临时占 :8001 |
| Q4 | 移植策略 | **引入 LangGraph 原生机制** | StateGraph 显式建图、PostgresSaver checkpointer 承担会话持久化（吸收 P8-1 sessions.db）、interrupt() 承担写操作人工确认流、subgraph 建 ReAct——这是论文「为什么用 LangGraph」的正面回答 |
| Q5 | 编号 | **P14** | P13 已被 P12 §5.3 软预留（business/memory/sessions 迁 PG）；其中 sessions/memory 被 Q4 原生机制吸收，P13 预留届时改写为仅 business 评估 |
| Q6 | 路由策略移植范围 | **仅 cascade（缺省）** | classic 为历史形态、triage 为实验形态，结论已留档（walkthrough/02-routing、eval/reports/），随 Go 退役不移植；ROUTER_MODE 开关随之退役 |
| Q7 | business 存储 | **第一版 SQLite 行为对等** | 评测经 HTTP 断言业务状态而非直连库；PG 化仍留 P13 评估，减少本次变量 |
| Q8 | 分层守护 | **lint-arch 退役，Python 侧建等价规则** | api 只依赖 agent/rag/business/支撑域、agent 不被 rag/llm 反向依赖等规则改为 import 约定 + 脚本/CI 检查（pytest 或自写 import-linter 契约） |
| Q9 | 工具链 | **uv + pyproject + ruff + pytest** | 仓库已有 .ruff_cache 使用痕迹；uv 管依赖锁定，ruff 兼做 lint+format，对齐 CI 门禁习惯 |
| Q10 | 端口策略 | **双轨期 :8001 → 收口 :8000** | 双轨期 Go 仍占 8000，eval 以 BASE_URL 覆盖指向 8001；P14-8 删 Go 后 Python 缺省改 8000，前端零改动生效 |
| Q11 | walkthrough 系列 | **标注为 Go 时代历史，不重写** | 打 tag `go-final` 保底；走读文档头部加「对应 go-final」注记，毕设写作期再按 Python 版补新走读 |
| Q12 | 长期记忆 | **第一版行为对等（注入式）** | LangGraph Store（跨线程记忆）列为后续增强，正好接 roadmap「长期记忆消亡与用户可见性」开放项 |

## 1. 目标 / 非目标

**目标**
- Python + LangGraph 重写编排层：级联路由、直答、Deep Research、ReAct agent、
  交易流程（槽位/确认/执行）、拒答，全部落 StateGraph。
- PARITY 6 端点行为对等（health/docs/search/chat SSE/business reset/business overview）。
- 原生机制落位：PostgresSaver checkpointer（会话持久化 + interrupt 恢复）、
  interrupt() 写操作确认流、subgraph ReAct、custom stream writer 对接 SSE。
- P10 截断防御在图中等价保留：`finish_reason=length 且带 tool_calls` 时不执行工具、
  合成错误 observation 回填。
- 28 题主集 + 8 题 agent 集 + 41 条检索查询全量评测，与 P12/历史 Go 基线对照留档。
- Go 后端（internal/ cmd/ go.mod bin/ Go 版 Makefile/CI 轨）全量退役删除，文档收口
  （README/PARITY/architecture §127 反转叙事 + ADR-0010）。

**非目标**
- 不动 PG schema 与存储函数（P12 资产原样复用）。
- 不动 apps/web（SSE 契约冻结的红线在服务侧满足）。
- 不做 triage/classic 路由、不迁 business 库去 PG、不做 LangGraph Store 记忆
  （分别见 Q6/Q7/Q12）。
- 不做公网部署（M4 既有条目，另行立项）。

## 2. 设计要点

### 2.1 目录与分层（映射 Go 版接缝）

```
apps/server/                 # 与 apps/web 对称
  main.py                    # 只装配+启动（≈ cmd/server/main.go）
  config.py                  # 环境变量对齐（PG_DSN/EMBED_*/RERANK_MODE…，ROUTER_MODE 退役）
  api/                       # FastAPI 路由层（≈ internal/api）：6 端点 + SSE
  agent/                     # 编排域（≈ internal/agent）
    graph.py                 # 图装配（StateGraph 入口）
    state.py                 # 共享状态 TypedDict（messages/route/citations/slots/usage…）
    events.py                # PARITY 十类事件的 custom writer 发射器
    routing.py               # cascade L0/L1/L2（≈ internal/agent/routing）
    nodes/                   # 各节点：resolve/retrieve/answer/research/react/transaction/refuse
    tools.py                 # search_knowledge + 业务工具 + parse_date
  rag/                       # 检索域：store/retrieve/hierarchical（psycopg 调存储函数）
  llm/                       # ChatOpenAI 工厂（glm-5.3/flash 双模型）+ 自定义 Embeddings
  business/                  # mock 业务（sqlite3，行为对等）
  budget.py middleware.py    # 每日 token 预算 + 限流/trace-id（FastAPI middleware）
eval/                        # 原样复用（纯标准库 HTTP 客户端，对服务语言无感）
```

依赖规则沿用 lint-arch 精神：api 只 import agent/rag/business/支撑；agent 内 routing
只碰 llm；rag/llm/business 禁止反向 import agent（Q8，CI 检查）。

### 2.2 图结构（全链路一图 + ReAct subgraph）

```
START → resolve_query(指代补全) → router(cascade: L0 正则→L1 flash 五分类→L2 主模型复核)
 ├─ refusal      → refuse → done
 ├─ factual      → [rewrite?] → retrieve(BM25+向量+RRF+[rerank]+父子块) → answer_direct → done
 ├─ research     → plan → LOOP{retrieve → reflect(够不够/还缺什么)} → synthesize → done
 ├─ hybrid       → retrieve + business 查询合并应答 → done
 ├─ transaction  → slot_fill ⇄ clarify → confirm_node(interrupt) → execute → receipt → done
 └─ agent(react) → react_subgraph{llm → guard → ToolNode → llm…} → done
done：done.reason 单点收口（completed/max_tokens/error/aborted）+ usage 汇总
```

- ChatMode（auto/direct/research/react）作为图入口参数短路 router，语义与 PARITY §3 一致。
- research 迭代与 ReAct 轮次上限由 state 计数 + 条件边控制（对应 reactMaxTurns=8、
  指纹去重 reactRepeatLimit=2、observation 截断 1500 字）。

### 2.3 SSE 事件映射（前端零改动的关键）

- 主通道：节点内 `get_stream_writer()`（stream_mode="custom"）显式发射 PARITY 事件——
  与 Go 版「管线各点显式 emit」同构，事件时序可控。
- answer_delta 的 token 流：FastAPI 侧消费 astream_events 的 on_chat_model_stream，
  聚合成 answer_delta（对齐 Go 版 delta 粒度）。
- 前端手写 SSE 解析器不动，服务端响应头/分帧格式按 PARITY §3 逐字段对齐。

### 2.4 原生机制落位（Q4）

| Go 版自研件 | LangGraph 等价物 | 说明 |
|---|---|---|
| sessions.db 会话存储（P8-1） | **PostgresSaver checkpointer** | 独立 schema（如 `lg_checkpoints`），thread_id=session_id；重启续办真跑验证 |
| agentConfirmSession 写操作确认 | **interrupt() + Command(resume=…)** | confirm 节点触发中断，前端确认/取消经新请求 resume；与 pending_action/action_result 事件映射 |
| 自研 ReAct 循环 | **subgraph + ToolNode + 自定义 guard 节点** | guard 置于工具执行前，实现 P10 截断防御 |
| 长期记忆（注入式） | 第一版维持注入 | LangGraph Store 留后续（Q12） |

### 2.5 截断防御与 GLM 行为守卫的移植

- guard 节点在 ToolNode 前检查 `response_metadata.finish_reason == "length"` 且带
  tool_calls → 不执行、合成错误 observation 回填（P10-2 铁律等价实现）。
- flash 小模型无视否定指令的已知坑：L1 五分类的代码级守卫（非法类名兜底、refusal
  安全网正则）必须随迁，不依赖提示词约束。
- 温度 0 仍非确定：评测 flaky 用例重跑确认的惯例继续生效。

### 2.6 双轨与端口

P14-0 ~ P14-6 双轨期：Go 仍占 :8000（基线可随时重跑），Python :8001（eval 以
BASE_URL 覆盖）。P14-7 全量门禁过了之后，P14-8 删除 Go、Python 缺省切 :8000。

### 2.7 工具链与 CI

- uv 管依赖（pyproject + uv.lock），ruff lint+format，pytest 单测；PG 相关测试沿用
  「真库 + 串行」惯例（gewu_test 库，进程锁或 pytest -p no:randomly 串行化）。
- CI 双轨期加 Python job（ruff + pytest + 冒烟 import），P14-8 收口时撤 Go job
  （gofmt/vet/go test/build/lint-arch），保留 web build。

## 3. 风险清单

1. **SSE 契约逐事件对齐**是最大工程风险：十类事件的时序/载荷字段以 PARITY §3 为准，
   P14-2 起每阶段用 eval/run_eval.py 实测（它就是契约的消费方）。
2. GLM 经 langchain-openai 兼容层的差异：tool_calls 增量格式、finish_reason 位置
   （response_metadata）、流式末 chunk 形态——P14-1 冒烟先行摸底，必要时薄封装适配。
3. EMBED_MODE=ark_multimodal 是火山方舟特殊端点，langchain 无预置：自定义 Embeddings 类
   （「轮子白嫖、验收自写」叙事的一部分）。
4. interrupt() 的确认流跨请求恢复：SSE 断开/用户改口/切话题放弃流程（Go 版有「切话题
   自动放弃」语义）需在 resume 路径显式处理并留测试。
5. FastAPI async + sqlite3：business 库连接需串行化（单连接锁），对齐 Go 版单连接语义。
6. 中文确定性日期解析移植（internal/dates 有单测）：测试先行随迁。
7. 删除 Go 后 walkthrough/ 内 Go 代码引用断链：Q11 注记方案，不阻塞。
8. 评测非确定性：所有对照报告标注模型/温度/重跑次数，flaky 用例重跑确认后留档。

## 4. 验收标准（Given-When-Then）

- G1 检索层：Given 41 条检索查询 golden，When 跑 Python 侧 /api/search，
  Then 检索指标与 P12 对账基线一致（存储函数同源，理论上应逐条相等）。
- G2 端到端：Given 28 题主集 + 8 题 agent 集，When `make eval` 指向 Python 服务，
  Then 各断言指标 ≥ Go 版历史基线（差距项逐条留痕归因：模型行为差异/事件映射差异/已知 flaky）。
- G3 交易流：Given 多轮 transaction 用例，When 全流程执行，Then 业务库状态断言全过，
  且 interrupt 确认流在「服务重启后 resume」场景下续办成功（checkpointer 真跑）。
- G4 前端零改动：Given apps/web 不改一行，When API_BASE 指 Python :8000，
  Then 聊天/对比/控制台三页面全功能可用（人工清单留档执行记录）。
- G5 截断防御：Given max_tokens 压到极小的构造用例，When ReAct 触发 length+tool_calls，
  Then 工具不执行、observation 回填、done.reason=max_tokens（P10 行为等价单测）。
- G6 退役收口：Given P14-8 完成，When 全仓搜索，Then 无 Go 源码/构建产物/CI 遗留；
  tag `go-final` 存在；README/architecture §127 已反转为迁移叙事 + ADR-0010 落档。
- G7 门禁：CI = ruff + pytest + web build 全绿；Makefile 提供 run/test/eval/lint-arch
  （Python 等价）完整目标。

## 5. Ticket 拆分（一 ticket 一门禁）

| Ticket | 内容 | 门禁 |
|---|---|---|
| P14-0 | 留档与骨架：tag `go-final`；apps/server 骨架（pyproject/uv/ruff/pytest/config.py）；FastAPI health + docs + business overview（占位读真库）三端点；Makefile/CI 增 Python 轨 | 三端点 200；pytest/ruff 绿；CI 双轨绿 |
| P14-1 | 资产接线：psycopg 调 PG 存储函数（store/retrieve/hierarchical 全量移植）；/api/search；llm/ 接入（ChatOpenAI 工厂 + 双模型 + 自定义 Embeddings 两模式 + usage/finish_reason 解析单测）；跑 Go 版 41 条检索基线（或复用 P12 报告）后 Python 对照 | G1 检索对照报告达标；模型冒烟真跑一轮 chat |
| P14-2 | 图 v1 直答：state.py + graph.py 最小闭环（resolve→route 占位直通→retrieve→answer→done）；events.py custom writer 发射 route/status/answer_delta/citations/done；mode=direct | eval 直答子集绿；SSE 事件与 PARITY §3 逐字段比对通过 |
| P14-3 | 路由与拒答：cascade L0/L1/L2 + refusal 安全网 + flash 守卫移植；mode=auto 分发（factual/refusal）；查询改写挂入 | 路由相关用例绿（误路由安全网有专项用例） |
| P14-4 | ReAct agent：tools.py 三件（search_knowledge/业务/parse_date）+ react subgraph + guard 截断防御 + 轮次/指纹去重；mode=react | dataset-agent 8 题绿；G5 截断防御单测过 |
| P14-5 | research + hybrid：plan→retrieve→reflect 循环节点 + synthesize；hybrid 混合意图应答；mode=research | multi_hop/research/hybrid 用例绿 |
| P14-6 | transaction + 原生机制：business/ 移植（sqlite3 + 权限矩阵 + /api/business/* 全通）；dates 移植（测试先行）；slot_fill/clarify/confirm(interrupt)/execute/receipt；PostgresSaver checkpointer 接入 | G3 交易流全过（含重启续办真跑） |
| P14-7 | 全量门禁 + 支撑域：budget/限流/trace-id middleware；28+8 题全量 eval 对照报告落档 eval/reports/P14-*；前端三页面人工验收清单 | G2/G4/G7 达标 |
| P14-8 | Go 退役与文档收口：删 internal/ cmd/ go.mod bin/ + Go CI/Makefile 轨；端口收口 :8000；lint-arch 换 Python 等价；README/PARITY/architecture §127 反转；ADR-0010；roadmap/P12 §5.3/P13 预留改写；walkthrough 历史注记；P14 执行记录 | G6 退役收口过；全仓无 Go 遗留；CI 绿 |

**下发提示词模板**（每 ticket 执行时按此发）：
> 执行 docs/P14-langgraph-migration.md 的 P14-N。约束：遵守任务书 §2 设计要点与 §0
> 拍板结论；门禁见 §5 表格该行 + 相关 G 条目；真跑留档到 §6 执行记录；偏差与遗留
> 逐条写回 §6.2/§6.3；Mimosa 钩子下 SQL 带参写语句注意（PG 读写仍走存储函数调用）。

## 6. 执行记录

### 6.1 Ticket 执行

**P14-0（2026-09-30，完成）**
- 前置收口：P11 前端三视图 / walkthrough 10 篇 / README 定位重写 / schema.go is_parent
  字面量化（4 commit）先行入库，工作区清零后打 tag **`go-final`**（66edec2）——Go 时代
  终态锚点（含前端与文档）。
- apps/server 骨架：pyproject（hatchling 构建 + uv 锁定）+ `gewu/` 包（config / api /
  rag / business）+ main.py（只装配，对齐 cmd/server/main.go 约定）+ tests 11 例。
- 三端点真跑 **200**：`/api/health`（docs=15/chunks=60/budget used=4020，与 P12 基线
  及 data/usage.json 吻合）、`/api/docs`（15 篇真语料）、`/api/business/overview`
  （真库预约单 VE-0172+）；原始响应 UTF-8 中文原文（PARITY 非 ASCII 契约）已验证。
- 门禁：ruff check + format 全绿；pytest 11/11（存储以 DocStore 协议替身脱 PG，业务库
  临时 SQLite 真跑含单号/过滤语义断言）；CI 增 server job（ruff + pytest，与 api/web
  并行三轨）。
- Makefile 增 server-install / server-run（:8001）/ server-test / server-lint 四目标。

**P14-1（2026-09-30，完成）**
- rag 全量移植：schema.py（DDL 逐字对照 schema.go，Python 侧接管建库）+ store.py
  （rag_fts_search / halfvec 向量 / chunk_rows / parent_rows / doc_meta_map /
  rag_upsert_doc / rrf_fuse / wipe；查询向量 L2 归一 + float32 舍入后以文本
  `::halfvec` cast 传入——与 upsert 的 JSONB vec 契约同路径，零新依赖）+
  retrieve.py（改写器/精排器/混合检索/父子扩展，漏斗与提示词逐行对照）。
- llm 包：ChatOpenAI 工厂（主/小双模型 + thinking disabled extra_body）+
  自定义 GewuEmbeddings（text 批量 / ark_multimodal 逐条并发 4 首错即停）+
  finish_reason/usage 解析（P10 契约 Python 侧）。
- /api/search 契约接入：绑定错统一「请求体不是合法 JSON」（全局 exception
  handler 收口）、query/k 越界中文 detail、text 截 300 rune。
- 测试：44 例全绿——纯逻辑（RRF/分数解析/漏斗 Fake 全套）+ 契约（search 校验
  逐条对照 Go）+ LLM 解析 + **PG 集成 9 例真库真跑**（gewu_test + 会话级
  pg_advisory_lock 串行，沿用 Go 惯例；CI server job 补 postgres service）。
- 门禁真跑：
  - 模型冒烟全通道 OK（glm-5.3 主 / glm-5.3-flash 小模型 chat，finish_reason=stop、
    usage 三元组正常；ark_multimodal embed 2048 维）——**langchain-openai 接 GLM
    无兼容层坑**（tool_calls/finish_reason 风险点本 ticket 未暴露，ReAct 移植时续盯）。
  - G1 检索对照（41 条，RERANK off）：Go↔Python 序列一致 15/41、集合一致 25/41。
    **归因（方差基线法）**：Go↔Go 自身 15/41 与 21/41、Python↔Python 自身 21/41
    与 27/41——跨语言差异与 Go 自身 run-to-run 方差同量级；叠加改写 5 连测实验
    （同查询同参数 GLM flash 输出 2 种改写串），漂移由 **GLM 温度 0 改写非确定性
    主导**（[[glm-model-quirks]] 的已知坑在检索输入侧放大），非移植偏差。
    报告：eval/reports/P14-search-parity-{fts,rerank-off,go-self,py-self}.md。

### 6.2 与 SPEC 的偏差

**P14-0**：

1. **uv 包源固化清华镜像**：本机对 pypi.org TLS 持续阻断（非瞬时，curl 实测
   SSL_ERROR_SYSCALL），拍板在 pyproject `[[tool.uv.index]]` 显式声明清华源并随
   uv.lock 固化——镜像全量可用、GitHub Actions 可达；如需官方源可重锁。
2. **PG 接线测试推迟到 P14-1**：CI server job 暂不含 postgres services；P14-0 的
   存储访问以协议替身测试（真 PG 已由三端点真跑覆盖），P14-1 移植全量 store 时补
   真库测试（沿用 gewu_test + 串行锁惯例）。
3. **线程模型从简**：Store/Business 均为单连接 + 锁（操作全程持锁）；P14-1 换
   psycopg_pool 连接池时再演进。
4. starlette 1.7 对 TestClient(httpx) 有弃用提示（生态新方向 httpx2），本期忽略仅
   记录，不引入额外依赖。

**P14-1 增量**：

1. **撞出并修复 Go config bug**（commit 885604d）：Load 的 PG_DSN/CORPUS_DIR
   fallback 链只查进程环境变量，.env 读入的值被无条件盖回缺省——**.env 的
   PG_DSN=5432 从未生效**，本机 Go 服务恒连缺省 5433 失败。双轨对照的价值实证。
2. **G1 口径修正**：端到端 /api/search 对照受改写方差支配（Go 无法关闭检索侧
   改写、且 P6 起强制 LLM key 拒绝无 key 启动，「纯 BM25 隔离实验」不可行）；
   采信 **P12 同款存储函数级对账**（Python store 9 项真库测试）+ **方差基线法**
   （跨语言差异 ≤ 自身方差 → 等价）。
3. hierarchical **切分**（入库侧）随 ingest ticket 移植（本 ticket 交付检索侧
   父子扩展）；**入库 CLI 依赖 ingest**，P14-2 起若需重建索引再排期。
4. 对照脚本 eval/run_search_parity.py 绕过 macOS 系统代理（urllib 读系统代理
   劫持 127.0.0.1 回 502）；Token 用量记账（budget 写侧）随 P14-7。

### 6.3 遗留与后续
（待填）
