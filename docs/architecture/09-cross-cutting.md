# 09 · 支撑域与横切面

不承载业务语义、但决定工程质量的部分：配置、成本防线、模型分层、依赖守护与 CI。

## 配置（`gewu/config.py`）

环境变量优先、仓库根 `.env` 兜底（向上查找发现，进程内已设变量不覆盖）——与
Go 版 `config.Load` 同语义。键名清单见 `.env.example`；相对路径（DATA_DIR）锚定
.env 所在目录（=仓库根），任意目录启动行为一致。**历史教训**（P14-1 撞出）：
配置回填链必须「进程环境变量 > .env 已读入值 > 缺省」三段齐全——只查进程变量
会把 .env 值静默盖回缺省。

## 成本防线

三层：

1. **限流**（`gewu/middleware.py`）：按 IP 固定窗口（缺省 600/分钟；X-Forwarded-For
   首段为键，反代场景可用），超限 429；健康检查豁免（探活语义）。
2. **每日 token 预算**（`gewu/budget.py`）：chat 入口闸（耗尽 429），LLMService
   统一入账（见 [07](07-state-persistence.md)）。
3. **观测**：X-Trace-Id 中间件（uuid v4，入站头有则沿用，响应头透出）。

## 模型分层

所有调用经 `LLMService`（`gewu/llm/service.py`）双模型缓存分发：**小模型**
（glm-5.3-flash）承担路由 L1、查询改写、精排、槽位抽取、续轮意图、记忆固化、
工具识别兜底——小输入小输出，单次约 1s；**主模型**（glm-5.3）只承担最终答案、
深研综合、路由 L2 复核与 ReAct 主循环。分层后直答链路延迟约 5s。换供应商改
`LLM_BASE_URL/LLM_MODEL/…` 即可（OpenAI 兼容）；智谱 thinking 私有参数按
`LLM_DISABLE_THINKING` 开关注入 extra_body。

## 依赖守护（lint-arch）

`make lint-arch`（`scripts/lint-arch.sh`，grep 断言零依赖）：编排域单向依赖、
业务域反向禁止、main.py 只装配——规则见 [01](01-overview.md)。这是微服务退役
后维持模块边界的机制：**没有网络边界，就靠 CI 检查 import 边界**。

## CI（`.github/workflows/ci.yml`）

双 job：**server**（uv sync → ruff check+format → pytest；pgvector/pgvector:pg17
service + 建测试库 gewu_test，PG 集成用例真跑）与 **web**（npm install →
next build）。本地对应 `make lint` / `make test`（前置 pg-up）。评测（`make eval`）
不在 CI——需要真 LLM key，按 runbook 手跑留档。

## 目录速查

```
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
