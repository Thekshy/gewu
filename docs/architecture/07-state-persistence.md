# 07 · 状态与持久化

系统里有三类生命周期不同的状态，各有明确的存放地与失效语义：

| 状态 | 存放 | 生命周期 | 失效 |
| --- | --- | --- | --- |
| 会话与办理流程（ChatState） | **PG checkpoints**（PostgresSaver） | 跨请求、跨进程重启 | thread 级；办理完成/取消时显式清 |
| 业务数据（预约/请假单） | SQLite `business.db` | 永久（演示语义） | `/api/business/reset` 手动清 |
| 长期记忆（fact/episodic） | SQLite `memory.db` | 永久积累 | 同 key UPSERT 覆盖 |
| 每日 token 用量 | `data/usage.json` | 当天 | 跨天自动归零 |

## PostgresSaver（P14 Q4 原生机制）

主图以 `thread_id = session_id` 编译进 checkpointer（`gewu/api/app.py` 装配）：

- **每步落盘**：节点执行后的 state 增量写 PG（checkpoints/checkpoint_blobs/
  checkpoint_writes 表族，`cp.setup()` 幂等建表）；
- **interrupt 恢复**：tx_gate 暂停时状态完整落盘——**服务重启后用户发「确认」
  仍能续办**（P14-6 G3 真跑验证：办到确认门 → kill 服务 → 重启 → resume 执行
  成功落库）；
- **吸收了 Go 时代的 sessions.db**（P8-1 的会话持久化）：办理流程状态从
  自研「进程内 map + SQLite 镜像」变为框架原生能力，代码量近乎归零。
- 工程细节：官方要求 **autocommit psycopg 连接**构造（DSN 字符串会在 setup
  时 TypeError）；PG 不可用时回退 MemorySaver（进程内，重启即失）。

## 长期记忆（`gewu/memory.py`）

分层两库（SQLite 单连接+锁）：

- **fact**（用户级结构化事实：专业/年级/绩点…）：每轮会话结束后由 flash 异步
  抽取（「不在用户等待路径做 LLM」），同 `(user,kind,key)` UPSERT 覆盖；注入
  prompt 时取最近 20 条。
- **episodic**（会话原文 user/assistant 双条）：同步落库（毫秒级 SQLite 写），
  是下一轮**指代补全**（resolve_query）与「近期对话要点」注入的数据源——同步
  落库保证补全立刻能看到上一轮，不受异步抽取延迟影响。

注入顺序固定：system（作答准则）→ 长期记忆块 → 参考资料 + 问题（稳定内容
前置，利于 prompt cache；无记忆数据时与历史版本逐字一致）。

**评测口径注意**：记忆固化线程与下一轮请求的 L1 路由存在并发争用（偶发路由
降级漂移）——全量评测以 `MEMORY_CONSOLIDATE=off` 隔离（记忆固化不在 PARITY
契约内），运行态默认 on。归因过程见 [P14 任务书 §6](../runbooks/P14-langgraph-migration.md)。

## 预算（`gewu/budget.py`）

每日 token 预算（默认 200 万）：chat 入口 `ensure()` 超限 429；LLMService 三个
通道（chat / chat_with_tools / 流式迭代完成）统一把 `usage_metadata.total` 入账
到 `data/usage.json`（与 Go 版同格式，跨重启有效、跨天归零）。

## 相关文件

`gewu/api/app.py`（checkpointer 装配）、`gewu/memory.py`、`gewu/budget.py`、
`gewu/agent/state.py`（ChatState 字段全景）。
