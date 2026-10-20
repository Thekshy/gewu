# gewu AGENTS.md

面向在本仓库工作的 AI 编码 agent。先读 [README.md](./README.md) 的「对比的方法」与
[docs/PARITY.md](./docs/PARITY.md)，本文件只补充一条主线之外不常被强调的约束：**选型上的少造轮子**。

## 选型阶梯：标准件优先，核心域例外

实现任何新能力前按顺序检查，停在第一个命中的层级：

1. 标准库 / 平台原生能做 → 用它；
2. 已安装依赖能做 → 用它（先 grep 本仓库，不重写已有的 helper）；
3. 健康的开源方案能做（见下方健康标准）→ 引入，经行为开关接入，缺省不改变现有路径；
4. 只有核心域，或前三层都不满足 → 才自研最小实现。

## 核心域 vs 支撑域

判断句式：**这块做砸了，本项目的主张是否还成立？** 成立 → 支撑域，用现成的；不成立 → 核心域，自己写并配套评测。

**核心域**（本项目的存在意义，不外包给通用方案，保持 clean-room 可对比）：

- agent 编排决策面（P31 起单循环形态）：意图由主模型在循环内自决 + 代码级
  守卫兜底（guard 关键词闸 / HITL 写确认门 / 槽位闸）+ route 观测标签——
  「级联路由 vs agent-first」的对比结论已落档（PARITY §0.9），现核心是守住
  单循环的决策语义不被通用方案稀释；
- PARITY.md 冻结的行为语义与评测集——评测集是考卷，考卷不能借用别人的；
- 行为开关 + 灰度门禁 + 可回退机制本身。

**支撑域**（所有系统都一样，无差异化价值，一律用标准件）：

- 编排运行时与 Web 框架（LangGraph、FastAPI、uvicorn）。注意：引入它们作为
  **实验变量**时走行为开关与对照报告留档——那是对比，不是默认替换；
- 存储（PostgreSQL、SQLite）、容器化、CI、日志、鉴权、通用解析。

## 开源方案健康标准（引入前三问）

1. **维护活跃度**：近半年有 commit、issue 有人回。三年不动的库，引入即接盘；
2. **许可证**：MIT / Apache-2.0 / BSD 直接用；GPL / AGPL 类传染许可必须记
   ADR 说明理由后再引入；
3. **契合与体量**：为一个小功能引入一个大框架不划算；关键路径上的依赖要经
   接口或行为开关隔离（参照 `RERANK_MODE` / `STREAM_ANSWER` / `IQS_API_KEY`
   空值关断的既有做法），保留将来替换的余地——开源项目会死、会转向、会改许可证。

三问不满足任一条 → 先记 [docs/ADR/](./docs/ADR/)，写清取舍，再动手。

## 与工作区规则的关系

工作区根目录的 AGENTS.md（Ponytail 懒惰阶梯）管代码级「少写」；本文件管生态级
「少造」。两者叠加生效：先问要不要造，决定造了再问怎么写得最少。

## Cursor Cloud specific instructions

- 服务端依赖在 `apps/server`：`uv sync --frozen`（Python 3.12）。前端在 `apps/web`：`npm ci`（Node 22）。门禁与 CI 相同：`uv run ruff check .`、`uv run ruff format --check .`、`uv run pytest -q`、`npm run build`。
- PostgreSQL 17 + pgvector 用 `docker compose up -d --wait pg`，宿主端口 `127.0.0.1:5433`。Cloud VM 里 Docker 需要 `fuse-overlayfs` 存储驱动和 iptables-legacy，由环境 `start` 拉起 dockerd。
- pytest 从 `PG_DSN`（库名必须是 `gewu`）推导测试库 `gewu_test`。未设置时会去连不存在的 `gewu_test_test`，PG 用例全部跳过。在仓库根 `.env` 写 `PG_DSN=postgres://gewu:gewu@127.0.0.1:5433/gewu?sslmode=disable`（与 CI 一致）。先 `CREATE DATABASE gewu_test OWNER gewu`。
- 没有 `EMBED_API_KEY` 时，在 `apps/server` 执行 `uv run python ingest_main.py --no-embed` 建表并灌入关键词索引。`/api/health`、注册登录、文档列表可用。向量检索会因缺向量索引失败；对话需要 `LLM_API_KEY`。
- API：`cd apps/server && uv run uvicorn main:app --host 127.0.0.1 --port 8000`。前端：`cd apps/web && npm run dev`（:3100，`/api` 同源代理到 8000）。
