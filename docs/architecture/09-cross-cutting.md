# 09 · 支撑域与横切面

不承载业务语义、但决定工程质量的部分：配置、成本防线、模型分层、依赖守护与 CI。这一篇是「工程侧」的汇总视图——每一项在其他各篇出现过的机制（限流、预算、checkpointer…）在这里从横切面角度收拢。

## 配置（`gewu/config.py`）

环境变量优先、仓库根 `.env` 兜底（从 cwd 向上逐级发现，进程内已设变量不覆盖）——与 Go 版 `config.Load` 同语义。相对路径（DATA_DIR）锚定 .env 所在目录（=仓库根），任意目录启动行为一致。

| 键 | 缺省 | 说明 |
| --- | --- | --- |
| `LLM_API_KEY` / `LLM_BASE_URL` | 空（OpenAI 兼容端点） | 主对话模型凭据；无 key 全链路降级可用（路由退启发式、检索退化 FTS、办理走离线抽取） |
| `LLM_MODEL` / `LLM_SMALL_MODEL` | glm-5.3 / glm-5.3-flash | 双模型分层（见下） |
| `LLM_DISABLE_THINKING` | false | 智谱 thinking 私有参数按开关注入 extra_body（OpenAI 等端点不识别会报错） |
| `EMBED_API_KEY` / `EMBED_BASE_URL` / `EMBED_MODEL` | embedding-3 | 向量模型 |
| `EMBED_MODE` | text | text（标准 /embeddings 批量）/ ark_multimodal（火山多模态，逐条并发） |
| `PG_DSN` | …:5433/gewu | PG 连接串（知识库 + checkpoints） |
| `DATA_DIR` | data | business.db / memory.db / usage.json 落点 |
| `DAILY_TOKEN_BUDGET` | 2,000,000 | 每日预算 |
| `RETRIEVAL_K` / `RERANK_MODE` / `REACT_MODE` / `QUERY_REWRITE` | 6 / on / off / on | 行为开关（见 [08](08-api-contract.md)） |
| `RATE_LIMIT_PER_MINUTE` | 600 | 限流 |

**历史教训**（P14-1 撞出）：配置回填链必须「进程环境变量 > .env 已读入值 > 缺省」三段齐全——只查进程变量会把 .env 值静默盖回缺省。

## 成本防线

三层，从外到内：

1. **限流**（`gewu/middleware.py`）：按 IP 固定窗口（缺省 600/分钟；`X-Forwarded-For` 首段为键，反代场景可用），超限 429；健康检查豁免（探活语义）。中间件顺序（外→内）：限流 → trace-id → CORS。
2. **每日 token 预算**（`gewu/budget.py`）：chat 入口闸（耗尽 429），LLMService 统一入账（见 [07](07-state-persistence.md)）。
3. **观测**：X-Trace-Id 中间件（uuid v4，入站头有则沿用，响应头透出）——跨实现迁移期间用它对齐三端日志。

## 模型分层

所有调用经 `LLMService`（`gewu/llm/service.py`）双模型缓存分发（模型惰性建一次，invoke 可并发）：

| 模型 | 承担的调用 | 特征 |
| --- | --- | --- |
| glm-5.3-flash（小） | 路由 L1、查询改写、精排、槽位抽取、工具识别兜底、续轮意图分类、记忆固化、指代补全、深研拆解 | 小输入小输出，单次约 1s |
| glm-5.3（主） | 最终答案（直答/深研综合）、路由 L2 复核、ReAct 主循环 | 只花在「值得花」的生成上 |

分层后直答链路延迟约 5s。`chat_stream` 返回可迭代的 `ChatStreamResult`：逐块产出文本增量，结束读 finish_reason/usage（P10 契约的 Python 等价）。换供应商改 `LLM_BASE_URL/LLM_MODEL/…` 即可（OpenAI 兼容）。

## 依赖守护（lint-arch）

`make lint-arch`（`scripts/lint-arch.sh`，零依赖 grep 断言）：编排域单向依赖、业务域反向禁止、支撑域不 import 业务域、main.py 只装配——规则见 [01](01-overview.md)。这是微服务退役后维持模块边界的机制：**没有网络边界，就靠 CI 检查 import 边界**：

```bash
check "rag ↛ agent（反向）"   "apps/server/gewu/rag"      "from gewu.agent\|import gewu.agent"
check "llm ↛ agent（反向）"   "apps/server/gewu/llm"      "from gewu.agent\|import gewu.agent"
check "business ↛ rag（横向）" "apps/server/gewu/business" "from gewu.rag\|import gewu.rag"
check "api 路由层禁止直接 import llm" \
      "apps/server/gewu/api/routes.py apps/server/gewu/api/chat.py" "from gewu.llm\|import gewu.llm"
# main.py 只做装配：import 面只允许 gewu.api / gewu.config
```

`app.py` 作为装配工厂（构造 LLMService 合法）享有豁免——测试以自定义依赖构建应用同样走它。

## CI（`.github/workflows/ci.yml`）

双 job：

| job | 内容 | 说明 |
| --- | --- | --- |
| **server** | uv sync → ruff check + format --check → pytest | 起 pgvector/pgvector:pg17 service，先建测试库 gewu_test（schema 由测试夹具 ensure 自建）——**PG 集成用例真跑**，不是 mock |
| **web** | npm install → next build | 前端可构建性门禁 |

本地对应 `make lint` / `make test`（前置 pg-up 起本地 PG）。评测（`make eval`）不在 CI——需要真 LLM key，按 runbook 手跑留档（见 [10](10-evaluation.md)）。

## 目录速查

```text
apps/server/
  main.py            # 装配入口（只 import gewu.api/gewu.config）
  gewu/api/          # 路由、SSE、resume 桥、装配工厂 app.py
  gewu/agent/        # graph/react/routing/tx/research/tools/prompts/events/state/emitter
  gewu/rag/          # retrieve/store/schema
  gewu/llm/          # chat（工厂与解析）/embed/service（门面）
  gewu/business/     # db.py（mock 业务全量）
  gewu/              # config/budget/memory/middleware/dates/jsonx
  tests/             # 103 例（单测 + PG 集成 + 契约）
eval/                # 数据集 + run_eval.py + reports/
```

## 相关文件

| 文件 | 职责 |
| --- | --- |
| `gewu/config.py` | Settings/load_dotenv/find_dotenv（.env 发现与锚定） |
| `gewu/middleware.py` | TraceID + 固定窗口限流 |
| `gewu/llm/service.py` / `chat.py` | 双模型门面、流式结果、usage/finish_reason 解析 |
| `scripts/lint-arch.sh` | 依赖规则守护（CI 内跑） |
| `.github/workflows/ci.yml` | 双 job 门禁 |

---

下一篇《10 · 评测体系》收尾：数据集、运行口径、flaky 判定与方差基线归因法。
