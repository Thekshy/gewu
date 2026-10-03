# 09 · 支撑域与横切面

不承载业务语义、但决定工程质量的部分：配置、成本防线、模型分层、观测日志、依赖守护与 CI。这一篇是「工程侧」的汇总视图——每一项在其他各篇出现过的机制（限流、双闸预算、checkpointer…）在这里从横切面角度收拢。

## 配置（`gewu/config.py`）

环境变量优先、仓库根 `.env` 兜底（从 cwd 向上逐级发现，进程内已设变量不覆盖）——与 Go 版 `config.Load` 同语义。相对路径（DATA_DIR）锚定 .env 所在目录（=仓库根），任意目录启动行为一致。

| 键 | 缺省 | 说明 |
| --- | --- | --- |
| `LLM_API_KEY` / `LLM_BASE_URL` | 空（OpenAI 兼容端点） | 对话模型凭据；无 key 全链路降级可用（路由退启发式、检索退化 FTS、办理走离线抽取） |
| `LLM_MODEL` / `LLM_SMALL_MODEL` | glm-5.3 / glm-5.3-flash | 双模型分层（见下） |
| `LLM_DISABLE_THINKING` | false | 智谱 thinking 私有参数按开关注入 extra_body（OpenAI 等端点不识别会报错） |
| `EMBED_API_KEY` / `EMBED_BASE_URL` / `EMBED_MODEL` / `EMBED_MODE` | embedding-3 / text | 向量模型与双模式（text 批量 / ark_multimodal 逐条并发） |
| `PG_DSN` | …:5433/gewu | PG 连接串（全部存储域共用） |
| `DATA_DIR` | data | usage.json 与 archive/ 落点 |
| `DAILY_TOKEN_BUDGET` / `DAILY_USER_BUDGET` | 200 万 / 20 万 | 全局闸 / 个人闸（P23） |
| `RETRIEVAL_K` / `RETRIEVAL_POOL_N` | 6 / 20 | 检索返回数 / 双路候选池（P15 进 Settings） |
| `RRF_K` / `RRF_VECTOR_WEIGHT` / `RRF_KEYWORD_WEIGHT` | 60 / 0.7 / 0.3 | 加权 RRF（P15） |
| `RERANK_MODE` / `RERANK_THRESHOLD` | on / 2.0 | LLM 精排与阈值（全滤空自动退化） |
| `CHUNK_STRATEGY` / `CHUNK_PARENT_LIMIT` / `CHUNK_CHILD_LIMIT` / `CHUNK_OVERLAP` | auto / 800 / 200 / 40 | 入库切片策略链（[04](04-rag-retrieval.md)） |
| `RATE_LIMIT_PER_MINUTE` | 600 | 限流 |
| `COOKIE_SECURE` / `CORS_ORIGINS` | false / 空 | https 部署开启 cookie Secure；跨域白名单（空=仅同源，P21 收紧） |
| `IQS_API_KEY` / `WEB_SEARCH_DAILY_LIMIT` | 空 / 200 | 联网检索（P26）：key 空=整链关闭；按次计费配每日上限闸 |
| `STREAM_ANSWER` | 1 | agent 主循环答案流式（P30；=0 紧急回退单帧全文） |
| `MEMORY_CONSOLIDATE` | on | 记忆固化后台抽取（评测时 off 隔离，[10](10-evaluation.md)） |
| `API_DOCS` | false | FastAPI 框架文档面 `/docs` 等（P36：缺省关闭收敛暴露面，本地调试开） |

**历史教训**（P14-1 撞出）：配置回填链必须「进程环境变量 > .env 已读入值 > 缺省」三段齐全——只查进程变量会把 .env 值静默盖回缺省。

## 成本防线

四道，从外到内：

1. **限流**（`gewu/middleware.py`）：按 IP 固定窗口（缺省 600/分钟；`X-Forwarded-For` **末段**为键——追加式反代拓扑下即真实来源，首段可被客户端伪造，P36 修正），超限 429；健康检查豁免（探活语义）。中间件顺序（外→内）：限流 → trace-id → CORS 白名单。另：login 端点叠加账号/IP 双键限速（10/30 次每分，P36 防凭证暴力破解）。
2. **全局 token 预算**（`gewu/budget.py`）：chat 入口全局闸（耗尽 429，usage.json 文件制，跨重启有效），LLMService 统一入账（见 [07](07-state-persistence.md)）。
3. **per-user token 预算**（P23，`gewu/usage.py`）：按用户逐日落 PG（`token_usage` 表）+ chat 入口个人闸（`users.daily_token_limit ?? DAILY_USER_BUDGET`，文案与全局闸区分）。记账归属经 `usage.current_user` ContextVar 从 chat 入口传播到 LLMService 记账口（双写：全局闸 + 个人账）；装配用 `make_usage_store` 探测式软降级——PG 不可达退 None 禁用个人功能，全局闸仍兜底。admin 可在 /admin 页按用户调限额（[11](11-auth.md)）。
4. **观测**：X-Trace-Id 中间件（uuid v4，入站头有则沿用，响应头透出）+ 四层排障日志（见下节）。

## 模型分层

所有调用经 `LLMService`（`gewu/llm/service.py`）双模型缓存分发（模型惰性建一次，invoke 可并发）；agent 主循环另经 `llm.agent_model()` 工厂（温度/max_tokens 固化在实例，供 create_agent 注入 fake 测试替身）：

| 模型 | 承担的调用 | 特征 |
| --- | --- | --- |
| glm-5.3-flash（小） | guard 判定、精排、深研拆解、上下文压缩摘要、追问生成（follow_ups）、记忆固化、续轮意图分类 | 小输入小输出，单次约 1s（P31 起路由/槽位抽取已随级联退役，意图由主循环自决） |
| glm-5.3（主） | agent 主循环（工具决策与最终答案生成） | 只花在「值得花」的生成上 |

换供应商改 `LLM_BASE_URL/LLM_MODEL/…` 即可（OpenAI 兼容）。

## 观测：四层排障日志（538b9cf + P24-1 补盲）

纯 print、零逻辑改动的四层日志，让一轮线上对话「不重放也能回溯」：

| 层 | 格式 | 内容 |
| --- | --- | --- |
| chat | `[chat]` JSON 一行 | 整轮汇总：session/mode/问题前 60 字/route/步数/工具数/answer 概要/耗时/结局（completed/max_tokens/aborted/error 四出口都打，`turn_log` 单点；route 为观测标签非路由决策） |
| llm | `[llm]` 一行 | 上下文概况（条数/字符量/角色分布）；P24-1 起 agent 主循环 per-call 埋点（ms + ctx_profile，由 UsageRecordMiddleware 兼任——补 create_agent 内部 model.invoke 不经 LLMService 封装的盲区，格式含 `chars=` 使 `make log-report` 的聚合自动吃到主循环数据） |
| rag | `[rag]` 一行 | 命中清单（doc_id#seq 或空命中提示）、改写/降级/硬防线触发记录 |

线上日志离线聚合走 `make log-report`（scripts 内正则解析）。

## 观测：第一方链路追踪（P27，trace/span 落 PG）

四层 print 解决「人读回溯」，P27 补上「机读真相源」——起因是 P26 联网
上线后首例排障发现 web_search 检索词不落任何日志，逐条补埋点的路走不完
（写入散/存储无/消费弱三缺口，任务书 §背景）。设计要点（任务书
[runbooks/P27-first-party-tracing.md](../runbooks/P27-first-party-tracing.md)）：

- **数据模型**：`agent_trace`（一轮一行，=[chat] 行的 DB 化 + error 列=问题
  台账落点）+ `agent_span`（轮内每步：kind=llm|tool/name/status/ms/tokens/
  input JSONB=工具 args 原样/output JSONB 截 4KB）；字段命名对齐 OTel
  GenAI/OpenInference 语义（exporter 映射留档 obs.py），保「将来 OTLP 双写
  ARMS」后路。
- **三接缝系统性采集**（新工具零观测成本）：chat.py 轮首 `Tracer.start` /
  轮末 finish（print 行与 DB 行同源产出）；**ToolTraceMiddleware 放栈最外层**
  ——所有工具含被拦截调用自动落 span；UsageRecordMiddleware 与
  LLMService.chat/chat_stream 双接缝记 llm span（agent 主循环 + guard/
  followups/压缩/固化小模型全族）。
- **写入纪律**：contextvar 作用域（SSE 流式迭代每 next 前 re-set，P23
  教训）；轮末一次 batch INSERT；`make_trace_store` 探测式软降级（PG 不可达
  →no-op，观测永不杀业务）。
- **消费面**：`make trace-query A=latest|session|find|spans|errors|stats`
  （本地）/ `make trace-query-remote`（线上 ssh 透传）——第一消费者是 AI
  排障（psql 母语），admin 可视化页列 B 期。

## 依赖守护（lint-arch）

`make lint-arch`（`scripts/lint-arch.sh`，零依赖 grep 断言）：编排域单向依赖、业务域反向禁止、支撑域不 import 业务域、main.py 只装配——规则见 [01](01-overview.md)。这是微服务退役后维持模块边界的机制：**没有网络边界，就靠 CI 检查 import 边界**：

```bash
check "api 路由层禁止直接 import llm" \
      "apps/server/gewu/api/{routes,chat,sessions,memory,admin,feedback}.py" "from gewu.llm\|import gewu.llm"
check "rag ↛ agent（反向）"   "apps/server/gewu/rag"      "from gewu.agent\|import gewu.agent"
check "llm ↛ agent（反向）"   "apps/server/gewu/llm"      "from gewu.agent\|import gewu.agent"
check "business ↛ agent/rag"  "apps/server/gewu/business" "from gewu.\(agent\|rag\)"
check "memory/budget/middleware/auth/session/usage ↛ 业务域" \
      "…（六支撑域文件）…" "from gewu.\(agent\|rag\|business\)"
# main.py 只做装配：import 面只允许 gewu.api / gewu.config
# web：apiFetch 调用点不得自带 ${API_BASE}（双拼事故根因，挡回归）
```

`app.py` 作为装配工厂（构造 LLMService 合法）享有豁免——测试以自定义依赖构建应用同样走它。

## CI 与本地门禁（`.github/workflows/ci.yml`）

CI 双 job：

| job | 内容 | 说明 |
| --- | --- | --- |
| **server** | uv sync → ruff check + format --check → pytest | 起 pgvector/pgvector:pg17 service，先建测试库 gewu_test（schema 由测试夹具 ensure 自建）——**PG 集成用例真跑**，不是 mock |
| **web** | npm install → next build | 前端可构建性门禁 |

本地门禁（CI 之外）：`make lint` / `make test`（前置 pg-up）、`make lint-arch`、**`make design-lint`**（P20 起：生产构建后 impeccable detect 扫三页+衬线域/冷色 grep，DESIGN.md 契约的机器门禁）。评测（`make eval` / `make retrieval-eval`）不在 CI——需要真 LLM key，按 runbook 手跑留档（见 [10](10-evaluation.md)）。

## 目录速查

```text
apps/server/
  main.py            # 装配入口（只 import gewu.api/gewu.config）
  ingest_main.py     # 入库 CLI（make ingest）
  gewu/api/          # routes/chat/auth/sessions/memory/admin/feedback + 装配工厂 app.py
  gewu/agent/        # agent/mw/guardrails/agenttools/resume/followups
                     # + txmeta/research/tools/prompts/events/emitter
  gewu/rag/          # retrieve/store/schema/chunker/ingest
  gewu/llm/          # chat（工厂与解析）/embed/service（门面 + agent_model）
  gewu/business/     # db.py（mock 业务全量）
  gewu/auth/         # store.py（三表 + argon2）
  gewu/session/      # store.py（chat_sessions + message_feedback）
  gewu/              # config/budget/usage/memory/middleware/dates/jsonx
  scripts/           # smoke_chat.py（冒烟）/ auth_tool.py（make invite/admin CLI）
  tests/             # 289 例（单测 + PG 集成 + 契约）
eval/                # 数据集×5 + run_eval/run_retrieval_eval/run_search_parity + reports/
```

## 相关文件

| 文件 | 职责 |
| --- | --- |
| `gewu/config.py` | Settings/load_dotenv/find_dotenv（.env 发现与锚定） |
| `gewu/middleware.py` | TraceID + 固定窗口限流 |
| `gewu/budget.py` / `gewu/usage.py` | 全局预算闸 / per-user 用量账（双写记账） |
| `gewu/llm/service.py` / `chat.py` | 双模型门面、agent_model 工厂、流式结果、记账口 |
| `scripts/lint-arch.sh` | 依赖规则守护（server import 边界 + web apiFetch 规则） |
| `.github/workflows/ci.yml` | 双 job 门禁 |

---

下一篇《10 · 评测体系》收尾：数据集、双轨口径、检索层独立评测、flaky 判定与方差基线归因法。
