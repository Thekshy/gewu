# go-notes —— Go 重构的设计决策与「为什么」

> 配套 [PARITY.md](../PARITY.md)（行为契约）阅读。本文只讲**决策与理由**，
> 每条都尽量回答面试官的下一句追问：「为什么这么做 / 不那么做？」
> 最后一节是重写过程中发现的 Python 版原设计问题清单（修复 = 行为改进，均有 PARITY §18 备案）。

## 1. goroutine 与 SSE 生命周期：回调取代生成器

Python 版用**同步生成器**产出事件流（`yield`），FastAPI/uvicorn 负责拉取并写连接。
Go 没有生成器，最初的两个候选：

- **channel + 专门 writer goroutine**：事件进 channel，writer 消费写连接；
- **同步回调**：`emit(ev any) error`，处理函数直接同步调用，返回 error 即中止。

选了**回调**，原因：

1. 整条管线本质是**单线程的顺序变换**（路由 → 检索 → 生成 → 事件），channel 引入了
   第二个执行流，却没有任何一步需要并行——channel 在这里只贡献了一次无意义的调度
   与泄漏风险（writer 先退、生产者阻塞在发送上）；
2. 回调的 `error` 返回值天然承载「消费者已断开，立即停止」语义，与 `context` 取消配合
   后，中断会**同步地**沿调用栈向上传播（直答 → LLM 流读取 → 上游 HTTP 连接关闭），
   不需要额外的 done channel 去收割 goroutine；
3. HTTP 层一个连接一个 handler goroutine（gin 自带），`RunChat` 在这个 goroutine 内
   同步跑完——**谁启动、谁结束，同栈同生命周期**，不存在需要 `WaitGroup` 的场景。

每个 SSE 事件写完立刻 `Flush()`。事件 JSON 用 `Encoder.SetEscapeHTML(false)` 序列化：
`encoding/json` 默认会转义 `<>&`，而前端拿到的 Python 版输出从不转义——这是逐字节
对齐契约的隐藏细节。

## 2. context：超时与取消的三层传播

取消链路：**客户端断开 → gin 的 `c.Request.Context()` → RunChat → llm.ChatStream →
上游 HTTP 请求**。`http.NewRequestWithContext` 让客户端断开在毫秒级杀死对智谱端点的
长连接——这在 Python 版是做不到的：openai SDK 的阻塞调用在生成完成前不会感知
uvicorn 的连接关闭，客户端走掉后模型仍在白白烧 token。

超时策略：

- LLM 请求统一 `context.WithTimeout(ctx, 10min)`（`requestTimeout`）。选 10 分钟是
  **被基线数据逼出来的**：Python 基线里 multi-006 单题 201.7s（端点偶发长尾），
  定 60s/120s 会把基线能过的题变成超时——「超时是 SLA 决策，先看真实分布再定数」。
- 检索/业务查询不额外加超时：本地 SQLite 毫秒级，加超时只会掩盖 bug。

预算检查（`budget.Ensure`）出现在 chat 入口和**每次** LLM/embedding 调用前，
与 Python 版一致——防线必须在「最贵的操作之前」，而不是入口一次了事
（一次深研最多 5+ 次 LLM 调用，入口检查挡不住中途超支）。

## 3. 数据竞争防护：哪里必须锁，哪里不需要

| 共享状态 | 防护 | 为什么 |
| --- | --- | --- |
| `rag.Store` 的 BM25/向量缓存 | `sync.RWMutex` | 读多写少；懒构建在**读锁**内完成（构建只读 DB、写缓存指针，幂等），并发首查可能重复构建一次，换取热路径零写锁 |
| `SessionStore`（办理流程状态） | `sync.Mutex` | 读写都轻（纳秒级 map 操作），RWMutex 的收益抵不过复杂度 |
| `TokenBudget` 计数+落盘 | `sync.Mutex` | 读改写必须原子；锁内完成 tmp+rename 原子写 |
| `RateLimiter` 桶表 | `sync.Mutex` | 每请求一次 O(1) 计算，争用可忽略 |
| SQLite（索引库/业务库） | `db.SetMaxOpenConns(1)` | modernc/sqlite 并发写会 SQLITE_BUSY；写入只发生在 ingest/办理，串行化代价可忽略，语义等价 Python 的 `check_same_thread=False` 单连接 |

**没有用 channel 做任何共享状态**：本项目的并发模型是「每请求一个 goroutine，各自
无共享地跑管线，共享只有上述五个点」，锁是最直白的表达。channel 的舞台是流水线
并行（如多子问题并行检索），那是行为差异（事件顺序变化），PARITY 不允许——
Python 版就是顺序检索，顺序就是契约。

单测跑 `go test -race`（CI 同）兜底以上判断。

## 4. 错误处理风格：哨兵 + 自定义 Is，而不是字符串匹配

- `budget.ErrBudgetExceeded` 是**哨兵值**；`ExceededError` 用自定义 `Is()` 方法
  匹配哨兵，同时 `Error()` 直接返回给用户看的中文消息——HTTP 429 的 body 和 SSE
  error 事件用的是**同一个错误对象**，不存在两处维护文案。
- 判定一律 `errors.Is/As`，绝不用字符串包含判断错误类型（字符串匹配会误伤
  「错误消息里恰好含有同类字样」的场合）。
- LLM 辅助调用的失败**全部就地降级**（路由→启发式、改写→原查询、抽槽→确定性解析、
  向量路→纯 BM25），降级不改变事件流形状，只是 `by_llm` 字段变 false——这是
  PARITY 的显式分支，不是「兜底 try-catch」。
- `llm.ErrNoKey` 区分「没配 key」与「调了但失败」：前者走确定性演示链路（功能），
  后者走降级链路（容错）。两者在 Python 里分别叫 `has_key()` 与 `except Exception`，
  Go 用类型把它们变成了编译期可检查的两个分支。

## 5. 隐式接口：函数值与最小依赖面

Go 惯例「**接受接口，返回结构体**」在本项目的形态：

- 槽位解析统一为 `func(d *Deps, text string) (string, bool)` 类型的**函数字段**
  （`slotMeta.Parse`）：日期、场馆、时段、单号……每个槽位自带解析器，
  添加槽位不改状态机代码——Python 用 `dict` 存 lambda，Go 用结构体存函数值，
  同一个思想；
- `emitFn func(any) error` 是管线对「消费方」的全部要求：HTTP SSE、进程内测试、
  未来的 WebSocket 都只是一个不同的 `emitFn`——管线不知道 SSE 的存在；
- `llm.Client` 故意**不定义接口**：只有一个实现（真端点），测试用 `httptest`
  假端点测真实现（连 SSE 解析一起测），mock 掉一个自研客户端等于只测 mock 自己。
  接口在「调用方需要可替换性」时才引入（如 `emitFn`），否则是仪式感。

`cmd/server` 装配 `agent.Deps`（结构体聚合依赖），internal 各包互相只 import
具体类型——依赖图是树不是网，`go vet` 之外不需要 wire/dig 这类注入框架。

## 6. 确定性：把 Python 的「碰巧稳定」变成「构造性稳定」

三处 Python 行为依赖了实现细节，Go 版换成构造性保证：

1. **分值平局的次序**：Python BM25 的查询 token 用 `set()` 去重，str 哈希带进程随机
   种子 → 平局 chunk 的相对顺序**每次重启都可能变**；RRF 倒是稳定（dict 插入序）。
   Go：BM25 平局按 chunk id 升序，RRF 平局按首次出现序（用 `firstSeen` 字段 +
   `sort.SliceStable` 显式实现 Python dict 的插入序语义）。
2. **JSON 对象键序**：`pending_action.args` 的展示顺序（场馆→日期→时段→用途）在
   Python 里靠 dict 插入序；`encoding/json` 对 map 按键排序会打乱它。自定义
   `orderedArgs.MarshalJSON` 保插入序——**顺序是前端契约的一部分**。
3. **时间**：全部日期计算锚定 UTC+8 固定偏移（无夏令时），与 Python 相同；
   预算按 UTC 日期滚动。

## 7. SQLite 兼容性：同一个库文件，两种语言读写

Python(numpy float32 `tobytes()`) 与 Go(`binary.LittleEndian.PutUint32`) 写出的
向量 blob **字节级一致**（都是 IEEE 754 小端），`data/index.db` 由两版实现互通
读写——重构期间 Go 服务直接挂载 Python 建好的索引跑评测，零迁移。
单测里放了 `TestVectorBlobNumpyCompatible`（断言 `1.0f32 → 00 00 80 3F`）钉死这个约定。

驱动选 `modernc.org/sqlite`（纯 Go，免 CGO）：镜像可以跑在 `scratch`/distroless、
交叉编译零配置。代价是纯 Go 实现比 C 版慢——本项目千级 chunk 的暴力余弦毫秒级完成，
瓶颈在 LLM 网络调用，这里省 CGO 毫无悬念。

## 8. LLM 客户端：为什么手写而不用 SDK

需求只有三个调用形状（chat / chat-stream / embed）+ 两个私有参数
（`response_format`、智谱 `thinking`）。openai-go 会带来整棵依赖树，却仍要
处理私有参数的「塞 JSON」问题。手写核心只有 ~200 行：

- SSE 解析（`scanSSE`）逐行读、容忍注释/心跳行、`[DONE]` 终止；
- 流式预算按 rune 数/2 保守估算（与 Python `len()` 语义对齐，中文一字一 rune）；
- 模型分层（主答案 glm-5.3 / 辅助 glm-5.3-flash）只是 `model(small bool)` 一个
  分支——「分层」是运营决策，不该长出框架。

附带收获：流式响应的取消传播（§2）在手写客户端里是一行 `NewRequestWithContext`，
SDK 里反而要翻文档找 `WithCtx`。

## 9. HTTP 层细节

- **校验语义对齐 pydantic**：`k` 字段用 `*int` 区分「未提供（缺省 5）」与
  「显式 0（422）」——值语义里 0 是合法缺省值会吞掉一个校验错误；
- 错误体统一 `{"detail": "中文原因"}`（PARITY §18 差异决定 #1：不逐字节复刻
  pydantic 的机器格式数组）;
- 限流取 `X-Forwarded-For` 首段——信任边界在反代，与 Python 相同（部署章节已注明）；
- CORS `*`：公开演示、无鉴权无 cookie，扫描器会告警，属**有意保留**的契约行为。

## 10. 重写中发现的 Python 版原设计问题（修复清单）

重写是最好的 code review——以下问题都在「逐行翻译」时暴露（Python 代码在
`tag: python-final` 可查证）：

| # | 问题 | 后果（Python 版） | Go 版处置 |
| --- | --- | --- | --- |
| 1 | `leave_status` 读工具的返回 dict **没有 `message` 键**，而 `start_flow` 直接 `result["message"]` | 用户查「我的请假单批了吗」→ KeyError → 整轮变 error 事件 | 结果结构统一含用户可读 message（PARITY §18 备案的行为修复） |
| 2 | `budget.used()` **每次读盘**（chat 入口 + 每次 LLM 调用前各一次），且 `write_text` 非原子 | 高频小文件 I/O；进程在写入中途崩溃会留下截断的 JSON（幸好有损坏兜底） | 内存计数 + 锁内 tmp/rename 原子落盘；损坏兜底保留 |
| 3 | BM25 查询 token `set()` 去重，str 哈希随机化 | 平局次序跨重启不确定（§6.1） | 确定性排序并加测试钉死 |
| 4 | 流式生成**中途断开不记账**：客户端取消/异常时 `budget.add` 不执行，已生成的半截答案 token 全部漏账 | 公开 demo 的免费额度被中断的长回答持续白嫖，预算防线低估 | 无论正常结束还是中断，`scanSSE` 返回后统一结算（Go 版修复，测试覆盖） |
| 5 | 限流桶 map **只增不减**，无过期清理 | 每个独立 IP 永久占一条（demo 量级无害） | 行为保持一致（不擅自改契约），已知限制记录于此 |
| 6 | `parse_slot` 的 `_HOUR_SLOT.get(h % 12 or 12) or _HOUR_SLOT.get(h)` 三重 or 真值判断 | `h=12`、`h=3` 等分支的实际行为难以推断（`0 or 12` 陷阱） | 显式两级查找 + 用例钉住 `14点/10:00/下午3点` 各自的预期 |
| 7 | 事件流里 `pending_action.args` 顺序依赖 dict 插入序 | 换语言/序列化器即破坏前端展示 | `orderedArgs` 显式保序（§6.2） |
| 8 | uvicorn 连接断开后，进行中的 openai SDK 调用不会被取消 | 客户端走掉仍烧完整个生成 | ctx 取消传播（§2） |

## 11. 数字：启动与内存（macOS arm64，2026-09-03 实测）

> 同机同日实测（进程启动 → /api/health 首次 200 的墙钟；RSS 为空载稳态）；
> 端到端评测延迟对照见 [rewrite-go-vs-python.md](../../eval/reports/rewrite-go-vs-python.md)。

| 指标 | Go（gin + 纯 Go SQLite） | Python（FastAPI + uvicorn） |
| --- | --- | --- |
| 冷启动到健康检查可用 | **21ms** | 1680ms |
| 空载常驻内存 RSS | **23MB** | 83MB |
| 交付物 | 单二进制 35MB（未裁剪；Docker 构建带 `-s -w` 更小） | venv + 依赖树（数百 MB） |

注：端到端延迟由 LLM 端点主导（单题数秒），服务自身开销（检索/编排/SSE）在两侧都是
毫秒级——重写买到的不是「回答更快」，而是上面这些部署性质与 §10 的正确性修复。
