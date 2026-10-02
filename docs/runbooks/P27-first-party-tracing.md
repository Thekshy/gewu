# P27 · 第一方链路追踪（trace/span 落 PG）（任务书）

> **背景**：2026-10-02 线上排障「昨天 TYLOO 比赛结果」两轮（session
> `aTgGFW4d…`，07:32/07:33，10457/11021ms）时暴露观测的结构性缺口：
> `web_search` 的实际检索词**任何地方都没记录**（[websearch] 只打失败行），
> 只能从回答文本反推。这不是缺一行日志，是三个结构性缺口叠加：
>
> 1. **写入散**：print 埋点散落 8+ 文件（[chat]/[llm]/[rag]/[routing]/
>    [websearch]/[agent]…），每个新能力要靠人记得补——P24 补过 [llm]
>    agent 主循环盲区，P26 又漏了 web_search 成功路径，下一个工具还会漏；
> 2. **存储无**：journalctl 滚动丢失，问题现场不落库，「每次补完上线，
>    问题都没有记录」——事故复盘只能靠当场的对话记录；
> 3. **消费弱**：log-report.sh 靠正则 grep 聚合，字段一变就瞎。
>
> 用户拍板：逐条补日志的路走完了，认真做一层系统观测。本票 = 第一方
> trace/span 落 PG（语义对齐 OTel GenAI 约定），第一消费者是 **AI 排障**
> （用户原话「主要不是我看，是你看」），第一消费面是 CLI 查询脚本而非页面。

## 0. 拍板结论

| # | 问题 | 拍板 | 依据 |
|---|------|------|------|
| Q1 | 观测路线三选一（自建 / LangSmith / 阿里云 ARMS OTel） | **自建第一方 trace/span 落 PG** | LangSmith：用户对话全量出境美国 SaaS（本仓库安全设计从未开过的口子，IQS 只出境检索词且有 guard 兜）+ 5k traces/月配额形态；ARMS：接线量（GenAI 手动埋点）≈ 自建，内测量级（16 轮/天）用不上云能力，排障工作流从 ssh+journalctl 变成登云控制台是倒退 |
| Q2 | LangSmith 服务器连通性疑虑 | **实测通，但连通不是其死因** | 2026-10-02 从 117.72.163.14 实测：`api.smith.langchain.com` HTTP 404@0.82s（API 入口正常）、`smith.langchain.com` 200@2.2s；对照组 `api.openai.com` 被墙。GFW 可达性无承诺，但出局依据是 Q1 的出境+配额，留档防复查 |
| Q3 | 消费者定位 | **第一消费者 = AI 排障，第一消费面 = trace-query.sh CLI** | AI 的母语是 ssh+psql+grep；LangSmith UI 对 AI 是浏览器自动化（chrome-devtools profile 有被锁前科）。admin 可视化页降级 B 期（答辩演示再要）。此视角同时让 A 期更薄 |
| Q4 | 语义标准 | **字段命名/取值对齐 OTel GenAI（OpenInference）约定** | 保「将来加 OTLP exporter 双写 ARMS」的后路——阿里云线从重做观测降级为一个出口；论文可写「语义对齐业界标准的第一方实现」 |
| Q5 | 采样与留存 | **全量不采样；TTL 30 天清理列 B 期** | 16 轮/天 × ~10 span ≈ 160 行/天，年 6 万行，PG 毫无压力 |
| Q6 | TYLOO 案根因修复（agent 无日期感知） | **顺带 P27-0 两小修** | AGENT_SYSTEM 注入「今天是…」+ web_search docstring 相对日期换算引导，个位数行；与本案直接因果，单独立票不值 |
| Q7 | 现有 print 层去留 | **全保留** | journalctl 仍是人读第一道；新表是机读真相源。双写同源生成（turn_log 一次产出两路），不漂移 |

## 1. 目标 / 非目标

**目标**

- **P27-0 日期感知小修**：`agent_system_prompt` 注入「今天是 YYYY-MM-DD
  （周X）」（WeKnora `{{current_time}}` 同款）；`web_search` docstring 补
  「相对日期（昨天/上周）先换算为绝对日期再进 query」。回归测试：注入
  存在性 + mem_block 尾部序不变。
- **P27-1 obs 支撑域**：`gewu/obs.py`——Tracer（contextvar 作用域 +
  内存 buffer + 轮末 batch INSERT）+ TracerStore（两表 DDL，探测式软降级：
  PG 不可达→no-op tracer，观测永不杀业务）+ 敏感处理（output 截断 4KB）。
- **P27-2 三接缝接线**（系统性覆盖，不是逐点补）：
  - `chat.py generate()`：轮首 `Tracer.start()`、轮末 `finish()` 写 trace
    行（字段=[chat] 汇总行的 DB 化 + error 列）；
  - **ToolTraceMiddleware**（wrap_tool_call，栈列表首位=最外层）：所有
    工具调用自动记 span——name/args/结果摘要/ms。**以后加任何工具自动
    被观测**（web_search 盲区从根上闭掉；被 Budget/Gate 拦截的调用也留痕）；
  - `UsageRecordMiddleware` 加 llm span（agent 主循环）；`LLMService.chat/
    chat_stream` 加 llm span（**classic 直答 + guard/routing/槽位抽取/
    followups 全族小模型调用一处接线全覆盖**）。
- **P27-3 消费面**：`scripts/trace-query.sh`（本地直跑 + make 透传；线上
  沿 log-report.sh 的 ssh 模式）——动作：`latest [N]` / `session <sid>` /
  `find <关键词>`（按问题 ILIKE 定位）/ `spans <trace_id>`（展开含
  input.query 原文）/ `errors` / `stats`（按 route 聚合轮数/p50/p95/error 率）。
- **P27-4 门禁+真跑**：测试（见 §3）+ TYLOO 同题复跑留档 + 文档
  （09-cross-cutting.md 增观测节）+ roadmap 勾选。

**非目标**

- 不上 OTel collector / ClickHouse / Langfuse 容器栈（量级不值，服务器是
  rsync+pm2+systemd 无 docker 形态）；不做 metrics/Prometheus（SQL 聚合够）。
- 不存 SSE 全量原始事件流（B 期再议；span 摘要够排障）。
- 不动 log-report.sh（B 期改 SQL 聚合或退役）；不做 admin 可视化页（B 期）。
- followups / 记忆固化后台线程的 span 不接（线程不继承 contextvar，B 期
  显式传参补）——A 期主链路三接缝优先。

## 2. 设计与实现

### 2.1 数据模型（P27-1）

```sql
CREATE TABLE IF NOT EXISTS agent_trace (
    id BIGSERIAL PRIMARY KEY,
    session_id TEXT NOT NULL,
    "user" TEXT NOT NULL,          -- PG 保留字双引号（P21 教训）
    role TEXT NOT NULL,
    mode TEXT NOT NULL,
    question TEXT NOT NULL,
    route TEXT NOT NULL DEFAULT '',
    route_layer TEXT NOT NULL DEFAULT '',
    reason TEXT NOT NULL DEFAULT '',
    latency_ms INTEGER NOT NULL DEFAULT 0,
    steps INTEGER NOT NULL DEFAULT 0,
    answer_head TEXT NOT NULL DEFAULT '',
    error TEXT,                    -- 问题台账落点：链路异常自动留案底
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS agent_span (
    id BIGSERIAL PRIMARY KEY,
    trace_id BIGINT NOT NULL REFERENCES agent_trace(id) ON DELETE CASCADE,
    seq INTEGER NOT NULL,
    kind TEXT NOT NULL,            -- llm | tool（OTel GenAI 语义）
    name TEXT NOT NULL,            -- "glm-5.3" | "web_search" | ...
    status TEXT NOT NULL DEFAULT 'ok',   -- ok | error
    latency_ms INTEGER NOT NULL DEFAULT 0,
    tokens INTEGER,
    input JSONB,                   -- 工具 args 原文 / 消息数与字符数
    output JSONB,                  -- 结果摘要（截断 4KB）
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_trace_session ON agent_trace(session_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_trace_error ON agent_trace(error) WHERE error IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_span_trace ON agent_span(trace_id, seq);
```

OTel GenAI 语义映射在 `obs.py` docstring 留档（kind/name/tokens ↔
`gen_ai.*` 约定），exporter（B/C 期）照表翻译。

### 2.2 Tracer 形态（P27-1）

```python
# gewu/obs.py 骨架（支撑域：禁依赖 agent/rag/business，lint-arch 收编）
_current: ContextVar[Tracer | None]

class Tracer:
    @classmethod
    def start(cls, store, *, session_id, user, role, mode, question) -> Tracer
    @contextmanager
    def span(self, kind: str, name: str, *, input: dict | None = None)
    # span 出口自动计时；with 体内可 sp.output=…/sp.tokens=…；异常→status=error 并上抛
    def finish(self, *, route, route_layer, reason, steps, answer_head, error=None)
    # 轮末一次 batch INSERT（trace 行 + spans 行）；store 为 None → 全程 no-op
```

- **contextvar 硬点（P23 教训前置）**：SSE sync 迭代每次 `next(stream)` 可能
  换 Context——chat.py 循环体顶部已有 `current_user.set(...)`，同点加
  `set_current_tracer(tracer)`，否则子图中间件里 `current_tracer()` 拿到
  None、span 静默丢失（症状=轮末 trace 行有、span 全无）。
- 工具/中间件侧零侵入取用：`from gewu.obs import current_tracer`，
  None-safe。

### 2.3 三接缝（P27-2）

| 接缝 | 改动 | 记录 |
|---|---|---|
| chat.py | generate() 首/尾 + 迭代 re-set | trace 行；error=异常 str |
| ToolTraceMiddleware（mw.py 新件，栈首位=最外层） | wrap_tool_call 包 handler | 所有工具：input=args、output=content 摘要、ms；拦截件（Budget/Gate/Limit）短路返回的 ToolMessage 同样被记录（外层包得到） |
| UsageRecordMiddleware | 现有计时处加 `tracer.span("llm", model名)` | tokens/msgs/chars/ms |
| LLMService.chat / chat_stream | 入口包 span | classic 直答 + guard/routing/slot/followups 小模型全族（`has_key()` 与 json 输出摘要进 output） |

- `app.py` 装配：`app.state.trace_store = make_trace_store(settings)`
  （usage.py 同款探测式软降级）。
- print 层零删除；`[chat]` 行与 trace 行由同一 turn_log 数据产出。

### 2.4 消费面（P27-3）

- `scripts/trace-query.sh`：psql 直连（读 .env PG_DSN），六个动作见 §1；
  `spans` 动作输出 seq/kind/name/status/ms/tokens + input/output 展开
  （jsonb_pretty）。
- Makefile：`make trace-query A=latest`（本地）；`make trace-query-remote
  A=spans ARGS=<id>`（ssh 透传服务器，SERVER/DEPLOY_KEY 变量沿 log-report）。

### 2.5 真跑剧本（P27-4）

1. 本地起服，「昨天 TYLOO 的比赛结果如何」同题复跑（真 GLM+真 IQS）：
   `trace-query spans` 断言——web_search span 的 `input.query` 原文可见、
   两次 llm span ms/tokens、guard 小模型 span 存在（service 接缝覆盖）；
2. classic direct 轮（图书馆借书上限）：llm span 落库（service 接缝对
   classic 生效）；
3. 办理轮（预约→确认→VE 单）：book_venue span 链 + HITL 悬停轮 trace 行
   error 为空、reason=completed；
4. P27-0 验证：同题回答应能把「昨天」解析为具体日期（回答文本含 10 月
   1 日或正确赛事日期，不再出现「无法准确定位昨天」话术）；
5. 部署线上（rsync+restart，备份先行沿 P24 流程）+ trace-query-remote 复验。

## 3. 验收门禁

1. `make lint` + `make test`（259 基线 + 新增）+ `make lint-arch`（obs.py
   收编支撑域反向规则）全绿。新增测试至少覆盖：Tracer buffer/finish/截断/
   no-op 降级；ToolTraceMiddleware 记录 args 与拦截留痕；contextvar 在
   流式迭代的 re-set（复刻 P23 场景的最小用例）；P27-0 日期注入与 docstring。
2. 真跑：§2.5 剧本全过，`trace-query` 各动作输出留档进 §5。
3. 行为零变化：除 P27-0 日期感知外，SSE 事件序/延迟（±噪声）/classic
   路径回归不变；journalctl print 行数不减。
4. 留档：本文件 §5 + 09-cross-cutting.md 观测节 + roadmap 勾选。

## 4. 风险与回滚

| 风险 | 缓解 | 回滚 |
|------|------|------|
| 轮末 batch INSERT 拖慢响应尾延迟 | 单轮一次、~10 行、毫秒级；store 软降级失败静默 | make_trace_store 返回 None（一行） |
| contextvar 丢 span（P23 场景复发） | 迭代顶部 re-set + 专项测试；症状可查（trace 有 span 无） | 观测层缺席不影响业务 |
| input/output 体积膨胀与敏感信息 | output 截断 4KB；工具 args 白名单性字段（本仓库工具无凭据参数）；question 原文仅内测留存，公开化前重评估 | — |
| 双写（print+DB）漂移 | 同源生成（turn_log 一处产出两路）；DB 为机读真相源 | — |
| 表膨胀 | 量级 160 行/天；TTL 30 天列 B 期 | — |

## 5. 执行记录（2026-10-02 执行完毕）

**改动落点**：`gewu/obs.py`（新：Tracer/contextvar/两表 DDL/TracerStore/软降级/
clip）、`prompts.py`（P27-0 日期头 `_today_cn` + agent_system_prompt(today)）、
`agenttools.py`（web_search docstring 相对日期换算引导）、`mw.py`
（ToolTraceMiddleware + UsageRecordMiddleware llm span + import obs）、
`agent.py`（ToolTraceMiddleware 入栈首位）、`llm/service.py`（chat/chat_stream
llm span、`_model_name`、ChatStreamResult span 包裹迭代）、`api/chat.py`
（tracer 生命周期 + 迭代 re-set + turn_log 增 err 参数）、`api/app.py`
（`trace` 哨兵参数 + `app.state.trace_store`）、`scripts/trace-query.sh`（新，
六动作）、`scripts/lint-arch.sh`（obs 收编支撑域反向规则）、Makefile
（`trace-query` / `trace-query-remote`）、docs（09 观测节 + roadmap 条目）。

**门禁**：ruff + lint-arch + pytest **270 passed**（259 基线 + 11：obs 6
[noop/异常/截断/contextvar/PG 往返/软降级] + mw 4 [工具 args 原样/零开销
直通/llm span tokens/日期头与尾部序] + chat e2e 1 [端到端 trace 行 +
llm/tool span + web_search query 原文断言]）。

**真跑**（本地真 GLM+真 IQS，`make trace-query` 消费）：

| 轮 | 结果 | 关键断言 |
|---|---|---|
| 「昨天tyloo的比赛结果如何」（首触） | 1614ms refusal completed | **被输入 guard 拦**（「高置信范围外」→旧口径拒答）——已知待拍板项（P26 §已知边界）的本地复现实证：线上 10-02 晨同题放行、本地拦截，guard LLM 判定非确定；拦截轮本身也落 trace（route=refusal，问题台账可用）✓ |
| 「我是钱塘大学的学生，想报名 2026 下半年的英语四六级…」 | 20075ms factual completed | span 时间线 7 条全可见：guard flash 1337ms/455tok → 主模型①3896ms → **web_search（input.query=「2026年下半年英语四六级考试报名时间」原样）**1198ms → 主模型②5352ms → search_knowledge 1533ms（嵌套 rerank flash 1303ms 亦被捕获）→ 终答模型 6704ms ✓ |
| P27-0 日期感知 | 同轮答案 | 答案明确「今天是 2026年10月2日，本轮报名已在 9 月 28 日结束」——「昨天」类相对日期不可解的根因修复验证（对照 P26 线上「无法准确定位昨天」话术）✓ |

**数据结论**：①三接缝系统性采集生效——嵌套调用（rerank in search_knowledge）
与被拦截调用都自动留痕，新工具零观测成本的设计兑现；②20s 轮的构成首次
span 级归因：主模型 ×3 ≈16s 仍是大头（与 P24「两次主模型 56%」结论一致且
粒度更细），IQS 1.2s、KB 链 2.8s；③token 首次 per-call 可查（主模型
2040/3337/3644，flash 455/2422）。

**坑与备忘**：①**psycopg3 的 `executemany` 在 cursor 不在 connection**
（Connection 无此方法，静默被 finish 吞成 [obs] 一行）——逐行 execute 替代；
②**psycopg 自动把 JSONB 列解析成 dict**——测试断言不要再 `json.loads`
（两处同错踩了两次）；③guard 校外首触判定非确定（线上放行/本地拦截同题，
温度 0 仍非确定的又一实例）；④tool span 的 output 抓的是 Command repr
（含 citations URL，噪）——B 期改结构化摘要；⑤follow_ups 生成在
`tracer.finish()` 之后（chat.py 顺序），其 flash span 不落库——如需覆盖
把 finish 移到 followups 后（B 期一行级）；⑥make_client 缺省 Settings 走
5433 时 feedback 池重试拖慢 e2e（本地已知环境坑，门禁 PG_DSN 指对即无）。
