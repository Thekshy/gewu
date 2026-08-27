# 格物 Gewu

> 高校场景的 Deep Research 智能问答与业务执行系统：**问题路由 × 混合检索 × 多步研究 × 业务办理 × 可评测**。
> 简单事实问题走 RAG 直答；复合政策问题进入深度研究链路（拆解 → 多路检索 → 交叉综合）；「帮我预约场馆 / 提交请假」走知行执行层（槽位收集 → 确认 → 执行 → 回执）；范围外问题礼貌拒答。

[![CI](https://github.com/Thekshy/gewu/actions/workflows/ci.yml/badge.svg)](./.github/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](./LICENSE)

**声明**：本项目为个人开源展示项目，采用 **clean-room** 方式独立实现；演示语料为完全虚构的「钱塘大学」合成数据，与任何真实高校、任何闭源商业项目无关。

## 为什么做这个项目

校园政策问答是典型的"答案有对错"场景——保研条件、转专业门槛答错是有代价的。这迫使系统必须解决聊天 demo 不会遇到的问题：

- **多跳问题**：「我挂过一门课还能转专业吗？转完原课程绩点还算吗、影响保研吗？」需要跨多份文件综合，单轮 RAG 无能为力；
- **幻觉控制**：答案必须带引用，检索不到依据要明确说不知道，而不是编造政策；
- **成本与滥用防护**：公开服务需要限流和每日 token 预算。

## 核心特性

| 特性 | 说明 |
| --- | --- |
| 五分类问题路由 | factual / research / **transaction**（办理）/ **hybrid**（问答+办理）/ refusal；LLM 分类 + 无 key 启发式降级；咨询政策（"请假找谁批"）不会误判为办理 |
| 混合检索 | BM25（中文字符二元语法，零分词依赖）+ 向量余弦，RRF 融合排序 |
| Deep Research | 子问题拆解 → 多路检索 → 证据跨子问题去重 → 交叉综合，全程 trace 可视 |
| 知行执行层 | mock 业务系统（场馆预约/请假审批）：槽位收集、多轮澄清、写操作确认流、回执、冲突恢复 |
| 权限矩阵 | 学生/辅导员角色，越权在工具层单一出口拦截 |
| 确定性日期解析 | 「明天 / 下周三 / 9月2日 / 请三天假」由代码换算，LLM 只负责找表述，杜绝日期算错 |
| 引用与拒答 | 每条回答标注 `[n]` 引用来源；证据不足时明确声明"未找到依据" |
| 离线评测 | 26 题种子集：单轮（事实/多跳/拒答）+ **多轮交易型**（断言业务库真实状态），一键产出 Markdown 指标报告 |
| 成本防线 | 按 IP 令牌桶限流 + 每日 token 预算（持久化、跨重启） |
| 零依赖启动 | 无 API key 也能跑：知识问答走 BM25 检索演示，业务办理走完整确定性链路 |
| 模型无关 | 任意 OpenAI 兼容端点（智谱 / DeepSeek / OpenAI / vLLM），改环境变量即切换 |

## 架构

```mermaid
flowchart LR
    U[用户提问] --> R{问题路由<br/>LLM / 启发式}
    R -->|factual| D[RAG 直答]
    R -->|research| P[子问题拆解]
    P --> S1[检索 1] & S2[检索 2] & S3[检索 N]
    S1 & S2 & S3 --> X[证据聚合 · 去重]
    X --> C[交叉综合 + 引用]
    R -->|refusal| F[礼貌拒答]
    D --> A[答案 + 引用 + 延迟]
    C --> A

    subgraph 混合检索
        B[BM25<br/>字符二元语法] & V[向量余弦] --> RRF[RRF 融合]
    end
    D -.-> B & V
    S1 & S2 & S3 -.-> B & V
```

详见 [docs/architecture.md](./docs/architecture.md)（含设计决策问答）与 [docs/roadmap.md](./docs/roadmap.md)。

## 快速开始

前置：Python 3.10+、Node 18+。

```bash
# 1.（可选）配置模型：不配置则以检索演示模式运行
cp .env.example .env   # 填入 LLM_API_KEY 等

# 2. 安装并建索引
make install-api && make ingest

# 3. 启动后端（:8000）与前端（:3100）
make install-web
make dev-api   # 终端 1
make dev-web   # 终端 2
```

打开 http://localhost:3100 即可对话。

**检索调试**（不经模型直接看命中）：

```bash
curl -s localhost:8000/api/search -H 'Content-Type: application/json' \
  -d '{"query": "转专业绩点要求", "k": 3}' | python3 -m json.tool
```

## 评测

```bash
make ingest   # 先确保索引存在
make eval     # 跑 eval/dataset.jsonl，报告写入 eval/reports/
```

指标：通过率 / 关键词命中 / 引用召回 / 分类型延迟；交易型用例为多轮对话，断言业务库真实状态（预约与请假单、审批层级、冲突恢复、权限拦截）。种子集 26 题（factual 7 / multi_hop 8 / refusal 3 / transaction 7 / hybrid 1），数据在 [eval/dataset.jsonl](./eval/dataset.jsonl)，欢迎扩充。

## 语料替换

`data/corpus/*.md` 为虚构「钱塘大学」的政策文档（带 `title/source/updated` frontmatter）。把文件换成任意公开语料（如某校官网通知）后 `make ingest` 即完成知识库切换，其余部分零改动。

## API 一览

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/api/chat` | SSE 流式问答（`question` + `mode` + `session_id` + `role`） |
| POST | `/api/search` | 调试：直接查看混合检索命中 |
| GET | `/api/docs` | 已入库文档列表 |
| GET | `/api/health` | 服务状态 / LLM 与向量开关 / 预算用量 |
| POST | `/api/business/reset` | 清空 mock 业务数据（演示/评测用） |
| GET | `/api/business/overview` | 调试：当前预约与请假单 |

## 部署

```bash
docker compose up --build -d
```

生产环境注意：Nginx 反代时关闭 SSE 缓冲（后端已下发 `X-Accel-Buffering: no`）；限流中间件取 `X-Forwarded-For` 首段作为客户端 IP，请确保代理层透传。

## License

MIT
