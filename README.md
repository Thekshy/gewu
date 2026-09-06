# 格物 Gewu

> 高校场景的 Deep Research 智能问答与业务执行系统：**问题路由 × 混合检索 × 多步研究 × 业务办理 × 可评测**。
> 简单事实问题走 RAG 直答；复合政策问题进入深度研究链路（拆解 → 多路检索 → 交叉综合）；「帮我预约场馆 / 提交请假」走知行执行层（槽位收集 → 确认 → 执行 → 回执）；范围外问题礼貌拒答。

[![CI](https://github.com/Thekshy/gewu/actions/workflows/ci.yml/badge.svg)](./.github/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](./LICENSE)

**声明**：本项目为个人开源展示项目，采用 **clean-room** 方式独立实现；演示语料为完全虚构的「钱塘大学」合成数据，与任何真实高校、任何闭源商业项目无关。

## 架构：模块化单体（P8 起）

单二进制 `cmd/server`（:8000）+ SQLite，零外部依赖。曾经完成过单体 → 六微服务的完整迁移
与逐题等价证明（P0~P5，26/26 PARITY），学习目标达成后在 P8 **有序退役**：微服务独有的
能力缺口（办理会话持久化）先吸收进单体，再删除分布式管道（gRPC/Redis Streams/网关）。
决策与证据：[ADR-0009](./docs/ADR/0009-微服务退役与单体模块化.md)、
[docs/history/](./docs/history/)、git tag `pre-ms-removal`。

```mermaid
flowchart LR
    C[客户端 / 评测 / web] -->|HTTP/SSE :8000| API[cmd/server<br/>装配 + 路由 + 中间件<br/>限流·CORS·X-Trace-Id]

    subgraph internal
        AGENT[agent 编排域<br/>pipeline·triage/cascade 路由<br/>react·transaction·memory]
        RAG[rag 检索域<br/>hierarchical 父子块·BM25<br/>向量余弦·rerank]
        LLM[llm 模型访问域<br/>chat/stream/embed·双 provider]
        BIZ[business 业务域<br/>场馆预约·请假审批]
        SUP[支撑域<br/>config·budget·dates·middleware]
    end

    API --> AGENT
    AGENT -->|Retriever/LLMer/Tools 接口| RAG & LLM & BIZ
    RAG --> DB[(SQLite<br/>index.db)]
    AGENT --> DB2[(SQLite<br/>memory.db·sessions.db)]
    BIZ --> DB3[(SQLite<br/>business.db)]
    LLM --> EXT[[OpenAI 兼容端点]]
    SUP -.-> AGENT & RAG & LLM & BIZ
```

**依赖规则**（`make lint-arch` 零依赖守护）：`agent → rag/llm/business`（经
Retriever/LLMer/Tools 接口）；检索/模型/业务域反向禁止 import 编排域；业务系统只能经
`agent.Tools`（权限矩阵单一出口）触达；支撑域不 import 任何业务域；`cmd/server` 只做装配。

模块地图与设计决策详见 [docs/architecture.md](./docs/architecture.md)。

## 为什么用 Go 重写（原为 Python/FastAPI）

v1 用 Python（FastAPI）快速验证了产品形态：路由、混合检索、业务办理、评测全绿。
未上线、无历史包袱，于是在行为冻结（[PARITY.md](./docs/PARITY.md)）后整体重写为 Go：

- **并发模型**：SSE 每连接一 goroutine，`context` 取消可以一路传播到上游 LLM 流——
  客户端断开即刻停止烧 token（Python 版里 openai SDK 的阻塞调用感知不到 uvicorn 连接关闭）；
- **确定性**：BM25/RRF 平局次序、事件 JSON 键序在 Python 里依赖 dict/set 的实现细节，
  Go 版全部构造性保证（[go-notes §6](./docs/go-notes.md)）；
- **部署面**：单二进制（纯 Go SQLite，免 CGO），冷启动 ~50ms、空载内存 ~18MB（Python 版 ~1.2s / ~95MB）；
- **重构本身是验证**：以 PARITY.md 为唯一行为规格做 clean-room 对照，同数据集逐题对比
  （[对照报告](./eval/reports/rewrite-go-vs-python.md)），重写过程修掉 8 个原设计缺陷
  （[go-notes §10](./docs/go-notes.md)）。

评测客户端仍用 Python（`eval/run_eval.py`，纯标准库 HTTP 客户端）——评测与被测实现
跨语言隔离，契约靠 HTTP/SSE 而不是共享代码。

## 核心特性

| 特性 | 说明 |
| --- | --- |
| 三层级联路由 | L0 规则快路径（明确办理指令零 LLM）→ L1 小模型五分类 → L2 主模型复核低置信；`agent-first` 模式塌缩为三执行策略（refusal/direct/agent）+ ReAct 自主组合工具 |
| 混合检索 | BM25（中文字符二元语法，零分词依赖）+ 向量余弦，RRF 融合；**父子块**层级切分（子块命中回父块上下文）；LLM 精排 rerank 可开关 |
| 查询改写与上下文补全 | 多轮指代消解（"那第二条呢"）在路由前补全，贯通路由与检索两个环节 |
| Deep Research | 子问题拆解 → 多路检索 → 证据跨子问题去重 → 交叉综合，全程 trace 可视 |
| 知行执行层 | mock 业务系统（场馆预约/请假审批）：槽位收集、多轮澄清、写操作确认流、回执、冲突恢复；**办理会话 SQLite 持久化，重启可续办** |
| 长期记忆 | episodic（会话原文）+ fact（结构化事实抽取）双库，注入 prompt 参与补全与作答 |
| 权限矩阵 | 学生/辅导员角色，越权在工具层单一出口拦截 |
| 确定性日期解析 | 「明天 / 下周三 / 9月2日 / 请三天假」由代码换算，LLM 只负责找表述，杜绝日期算错 |
| 引用与拒答 | 每条回答标注 `[n]` 引用来源；证据不足时明确声明"未找到依据" |
| 离线评测 | 28 题主数据集 + 8 题 agent-first 数据集：单轮 + **多轮交易型**（断言业务库真实状态），一键产出 Markdown 指标报告 |
| 成本与观测 | 按 IP 令牌桶限流 + 每日 token 预算（持久化、跨重启、原子落盘）；X-Trace-Id 贯穿响应头/gin/agent 日志 |
| 模型分层 | 主答案 glm-5.3；路由/拆解/槽位抽取/查询改写/精排等辅助调用走 glm-5.3-flash |
| 模型无关 | 任意 OpenAI 兼容端点（智谱 / DeepSeek / OpenAI / vLLM），改环境变量即切换 |

行为开关（全绿门禁下灰度，旧实现保留可回退）：`ROUTER_MODE` / `CHUNK_MODE` / `RERANK_MODE` /
`REACT_MODE` / `QUERY_REWRITE` / `SESSION_STORE`，缺省值见 [`.env.example`](./.env.example)。

## 快速开始

前置：Go 1.25+、Node 18+（前端）。

```bash
# 1. 配置模型（P6 起强制有 key 启动）
cp .env.example .env   # 填入 LLM_API_KEY / EMBED_API_KEY 等

# 2. 建索引（BM25+向量混合）并启动 API :8000
make ingest
make run

# 3. 前端 :3100
make install-web && make dev-web
```

打开 http://localhost:3100 即可对话。

**检索调试**（不经模型直接看命中）：

```bash
curl -s localhost:8000/api/search -H 'Content-Type: application/json' \
  -d '{"query": "转专业绩点要求", "k": 3}' | python3 -m json.tool
```

## 评测

```bash
make run &                     # 先起服务（RATE_LIMIT_PER_MINUTE=600 建议值）
make eval                      # 28 题主数据集 → eval/reports/report-*.md
# agent-first 8 题（ReAct 自主编排链路）：
ROUTER_MODE=agent-first make run &
python3 eval/run_eval.py --dataset eval/dataset-agent.jsonl --tag agent-first
```

指标：通过率 / 关键词命中 / 引用召回 / 分类型延迟；交易型用例为多轮对话，断言业务库真实状态
（预约与请假单、审批层级、冲突恢复、权限拦截）。主数据集 28 题（factual 9 / multi_hop 8 /
refusal 3 / transaction 7 / hybrid 1），数据在 [eval/dataset.jsonl](./eval/dataset.jsonl)，欢迎扩充。

## 语料替换

`data/corpus/*.md` 为虚构「钱塘大学」的政策文档（带 `title/source/updated` frontmatter）。
把文件换成任意公开语料（如某校官网通知）后 `make ingest` 即完成知识库切换，其余部分零改动。

## API 一览

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/api/chat` | SSE 流式问答（`question` + `mode` + `session_id` + `role`；响应头 `X-Trace-Id`） |
| POST | `/api/search` | 调试：直接查看混合检索命中 |
| GET | `/api/docs` | 已入库文档列表 |
| GET | `/api/health` | 服务状态 / LLM 与向量开关 / 预算用量 |
| POST | `/api/business/reset` | 清空 mock 业务数据（演示/评测用） |
| GET | `/api/business/overview` | 调试：当前预约与请假单 |

SSE 事件契约（route / status / step / answer_delta / citations / slot_question /
pending_action / action_result / error / done）以 [PARITY.md §3](./docs/PARITY.md) 为准。

## 部署

```bash
make build && ./bin/gewu-api          # 宿主直跑（推荐）
# 或前端容器化：docker compose up -d --build web（API 单体宿主运行 :8000）
```

生产环境注意：Nginx 反代时关闭 SSE 缓冲（后端已下发 `X-Accel-Buffering: no`）；限流中间件取
`X-Forwarded-For` 首段作为客户端 IP，请确保代理层透传。

## License

MIT
