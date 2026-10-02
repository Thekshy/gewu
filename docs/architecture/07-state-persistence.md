# 07 · 状态与持久化

系统里有四类生命周期不同的状态，各有明确的存放地与失效语义。会话与办理流程状态由 LangGraph 的 PostgresSaver 接管（P14 Q4 原生机制，吸收了 Go 时代的自研 sessions.db）；业务与记忆自 P21-2 起同库迁入 PG（SQLite 退役，凭证/用户见 [11](11-auth.md)）；每日用量是进程外一个 JSON 文件。本文给出每一类的装配方式、读写路径与失效行为。

## 状态总览

| 状态 | 存放 | 生命周期 | 失效 |
| --- | --- | --- | --- |
| 会话与办理流程（ChatState） | **PG checkpoints**（PostgresSaver） | 跨请求、跨进程重启 | thread 级；办理完成/取消时显式清；P22 起随会话删除连带清 |
| 会话登记（归属/标题/kind） | PG 表 `chat_sessions`（P22） | 跨请求、跨重启 | `DELETE /api/sessions/{id}` 三处连带之一 |
| 业务数据（预约/请假单） | PG 表 `bookings`/`leave_tickets` | 永久（演示语义） | `/api/business/reset` 手动清（P21 起 admin-only） |
| 长期记忆（fact/episodic） | PG 表 `memory_fact`/`memory_episodic` | 永久积累 | 同 key UPSERT 覆盖；user_id=P21 起为真实 email；P22 起 fact 用户可管（/memory 页）、episodic 随会话删除连带清 |
| 消息反馈（赞/踩） | PG 表 `message_feedback`（P25） | 永久积累 | 同 `(user,session,question)` UNIQUE upsert 覆盖（改主意不双行） |
| per-user token 用量 | PG 表 `token_usage`（P23） | 按 (user, day) 累加 | 次日自然新行（无清理任务） |
| 全局每日 token 用量 | `data/usage.json` | 当天 | 跨天自动归零 |

## 会话资源化（P22：`gewu/session/store.py`）

P22 起 session_id 不再是客户端自报的裸 uuid，而是 `chat_sessions` 表登记发放的
服务端资源（`secrets.token_urlsafe(16)` 主键、`"user"`=email 保留字双引号、
`kind ∈ chat|compare`、title 首问前 20 字条件回填——`UPDATE ... WHERE title=''`
语义防并发覆盖用户改名）。`/api/chat` 装配点一条 SELECT 做归属校验（本人外
统一 404 防枚举），`"default"` 缺省值废弃（未传 422 给指引文案）。

删除会话 = 三处连带（`gewu/api/sessions.py`）：先 `checkpointer.delete_thread`
（langgraph-checkpoint-postgres 3.x 原生 API，删 checkpoints/blobs/writes 三表
该 thread 全部行），再 `memory_episodic` 按 session_id 清，最后删 `chat_sessions`
行——顺序不反：跨连接非事务，业务行删了 cp 删失败只会留无害悬空检查点（无入口
可达），反过来会留「看似可用实则无历史」的会话。

历史恢复（`GET /api/sessions/{id}/messages`）：优先从 checkpointer state 的
`messages` 提取对话级序列（HumanMessage 文本 + AIMessage 非空 text；工具调用轮
与 ToolMessage 跳过、system 跳过）——覆盖 agent 链路（mode=auto/react）；
classic 链路（classic/direct/research）不写 messages，提取为空时退
`memory_episodic` 兜底（每轮 user/assistant 双条、链路无关）。对话级纯文本渲染，
事件级细节（citations/steps 卡片）不恢复。

## PostgresSaver（P14 Q4 原生机制）

主图以 `thread_id = session_id` 编译进 checkpointer（`gewu/api/app.py` 装配）。构造有一个官方文档没写清的坑——**必须 autocommit 连接**（checkpointer 内部自管事务），DSN 字符串会在 setup 时 TypeError：

```python
def _make_checkpointer(settings: Settings):
    try:
        conn = psycopg.connect(settings.pg_dsn, autocommit=True)   # 必须 autocommit
        cp = PostgresSaver(conn)
        cp.setup()          # 幂等建表
        return cp
    except Exception:
        traceback.print_exc()
        return MemorySaver()   # PG 不可用退内存版（重启即失）
```

装配后每一轮 invoke 都写两份语义：

- **每步落盘**：节点执行后的 state 增量写 PG（`checkpoints`/`checkpoint_blobs`/`checkpoint_writes` 表族，`cp.setup()` 幂等建表）；
- **interrupt 恢复**：tx_gate 暂停时状态完整落盘——**服务重启后用户发「确认」仍能续办**（P14-6 G3 真跑验证：办到确认门 → kill 服务 → 重启 → resume 执行成功落库）。

办理流程状态从 Go 时代的自研「进程内 map + SQLite 镜像」（P8-1 sessions.db）变为框架原生能力，代码量近乎归零。`entry_gate` 判定 `tx_phase=collect` 走续轮、`tx_phase=confirm` 由 resume 桥接管（见 [02](02-orchestration-graph.md)/[06](06-transaction.md)）——续办语义完全建立在「state 在 PG 里活着」这一事实之上。

## 长期记忆（`gewu/memory.py`）

分层两域表（psycopg ConnectionPool + 建表幂等 DDL，P21-2 自 SQLite 平移）：

| 表 | 内容 | 写入时机 | 读取用途 |
| --- | --- | --- | --- |
| `memory_fact` | 用户级结构化事实（kind: profile/preference/constraint + key/value） | 每轮会话结束后 flash **异步**抽取，同 `(user,kind,key)` UPSERT 覆盖 | 注入 prompt 最近 20 条（`MAX_FACTS_IN_CONTEXT`） |
| `memory_episodic` | 会话原文 user/assistant 双条 | 每轮**同步**落库（毫秒级 PG 写） | 指代补全（resolve_query）的近 6 条输入 + 记忆块的「近期对话要点」4 条 |

同步/异步的分工是有意的：episodic 同步落库保证下一轮指代补全**立刻**能看到上一轮原文，不受异步抽取延迟影响；fact 抽取不在用户等待路径做 LLM（固化失败只打日志，原文已留存）。

固化入口在 SSE 端点流结束后起守护线程（`_consolidate_async` → `consolidate()`）：先写双条 episodic，再让 flash 按 `CONSOLIDATE_PROMPT` 抽稳定事实（专业/年级/绩点/宿舍…，「只抽明确表达或可直接确定的信息，不要推测」，key 用英文标识如 major/gpa）。

注入顺序固定（`assemble_messages`）：system（作答准则）→ 长期记忆块 → 参考资料 + 问题——稳定内容前置，利于 prompt cache；无记忆数据时与历史版本逐字一致。

**评测口径注意**：记忆固化线程与下一轮请求的 L1 路由存在并发争用（偶发路由降级漂移）——全量评测以 `MEMORY_CONSOLIDATE=off` 隔离（记忆固化不在 PARITY 契约内），运行态默认 on。归因过程见 [P14 任务书 §6](../runbooks/P14-langgraph-migration.md)。

## 预算（双闸：`gewu/budget.py` + `gewu/usage.py`）

token 成本是两道闸、两本账：

- **全局闸**（`budget.py`，usage.json 文件制）：每日 token 预算（缺省 200 万，`DAILY_TOKEN_BUDGET`），chat 入口 `ensure()` 超限抛 `BudgetExhausted` → 429；`LLMService` 各通道统一把 `usage_metadata.total` 入账。持久化是整文件覆写 `{"date": "...", "tokens": n}`——与 Go 版同格式，跨重启有效、跨天归零；记账文件写失败不影响主链路。
- **个人闸**（P23，`usage.py`，PG `token_usage` 表）：按 `(user, day)` UPSERT 累加，chat 入口比对 `users.daily_token_limit ?? DAILY_USER_BUDGET(20 万)` 超限 429（文案与全局闸区分）；记账归属经 `usage.current_user` ContextVar 从 chat 入口传播到 LLMService 记账口**双写**（全局账 + 个人账）。装配 `make_usage_store` 探测式软降级——PG 不可达退 None 禁用个人功能，全局闸仍兜底。

ContextVar 传播的两个坑（P23 撞出、已在代码注释固化）：StreamingResponse 的 sync 迭代由线程池分派（每次 next 可能换线程），contextvar 必须在 `generate()` 迭代体开头 re-set；consolidate/follow_ups 异步线程不继承，work() 首行显式 set。

## 一轮会话触及的全部状态

```mermaid
sequenceDiagram
    autonumber
    participant U as 用户
    participant A as POST /api/chat
    participant G as 主图（checkpointer）
    participant PG as PG checkpoints
    participant M as PG memory_*
    participant B as PG business.*
    participant J as data/usage.json

    U->>A: 第 N 轮消息
    A->>J: budget.ensure()（读当日用量）
    A->>G: invoke(new_state, thread_id=sid)
    G->>PG: 读 checkpoint（恢复 tx_* 续办态）
    G->>G: resolve_query ← memory 近期对话（读 M）
    G->>B: 办理链路读/写业务（经 call_tool）
    G->>PG: 每节点后增量落盘
    A-->>U: SSE 流（done 单点）
    A->>A: 守护线程 consolidate：M 写 episodic（同步语义）+ flash 抽 fact UPSERT
    A->>J: LLMService 各通道陆续入账 usage
```

## 相关文件

| 文件 | 职责 |
| --- | --- |
| `gewu/api/app.py` | checkpointer 装配（autocommit 坑在此）与各存储域注入 |
| `gewu/agent/state.py` | ChatState 字段全景（哪些进 checkpoint） |
| `gewu/memory.py` | 双表 schema、memory_block 装配、consolidate 固化 |
| `gewu/budget.py` / `gewu/usage.py` | 全局预算闸（usage.json）与 per-user 用量账（PG） |
| `gewu/session/store.py` | chat_sessions 登记与 message_feedback 表 |
| `gewu/api/chat.py` | 流后异步固化入口 |

---

下一篇《08 · 接口契约》对齐外部视角：六端点、SSE 十类事件与 interrupt/resume 桥的字段级约定。
