# P7 代码审查修复提示词（真实跑通后定位，逐条复制给编程助手）

> 背景：P0–P5 改造后已 `build/vet/test` 全绿，并真实重建索引、起服务打通过 chat/search。
> 真实流量下定位到 1 个 P0 误路由、1 组 P1 并发健壮性问题、1 个 P1 父子块退化问题，外加若干 P2 整洁项。
> **执行顺序：提示词 1 → 2 → 3 →（可选 4）→ 末尾端到端验证。每改完一条先跑对应包测试再进下一条。**

全局门禁（每条都要满足）：

- 只改本提示词点名的文件与行为，不重构无关代码、不改对外事件结构与 API。
- 每条完成后必须 `go build ./... && go vet ./... && go test ./...` 全绿；新增行为同步补单测。
- 不引入新第三方依赖；不回退到"无 key 静默降级"。

---

## 提示词 1（P0）：收紧办理工具识别，修掉"预约心理咨询 → 场馆预约"的误路由

**目标文件**：`internal/agent/transaction.go`（主）、`internal/agent/agent_test.go` 或 `cascade_memory_react_test.go`（测试）。

**现状（已定位，勿改错地方）**：
- `toolPatterns` 中 `book_venue` 的正则是 `` `预约|预订|订.*(馆|场|间)` ``，裸 `预约` 会命中一切"预约X"。
- 于是"我想预约一次心理咨询"：L0（`cascade_router.go` 的 exactTxRe）正确判为 transaction 大类，但 `StartFlow`→`DetectTool` 被这条兜底正则误选成 `book_venue`，直接进入"选哪个场馆（羽毛球馆/篮球场…）"流程。
- **不要改 `cascade_router.go` 的 L0**：L0 判的是"办理意图"这个大类，本就该是 transaction；错在具体工具选择。`StartFlow`（约 246 行）已有"`d.Tools[tool]` 不存在 → `fallbackKnowledge` 转 RAG"的降级链，只要 `DetectTool`/LLM 不再误选，心理咨询就会正确落到知识库（0015-medical）。

**改法**：
1. 把 `book_venue` 正则收紧为"办理动词 + 场馆类宾语共现"，例如：
   `` `(预约|预订|订).*(馆|场|间|场地|羽毛球|篮球|游泳|乒乓|网球|健身|研讨|教室|场地)` ``
   保证"帮我预约明天晚上的羽毛球馆""订个研讨间301"仍命中；"预约心理咨询/预约挂号"不再命中。注意保持 `toolPatterns` **按序首命中**的既有契约，`submit_leave`（请假）仍排在 `book_venue` 之前。
2. 加一层负向双保险：定义 `nonVenueRe = 心理咨询|心理辅导|心理咨询室|挂号|看医生|校医|咨询老师|辅导员`，在 `DetectTool` 中：当候选命中 `book_venue`、但原句同时命中 `nonVenueRe` 时跳过该候选（继续往后，最终返回 ""）。
3. 修改 `llmExtractTool` 的 system prompt：明确"可选工具中没有语义匹配的工具时，必须返回 `{"tool":""}`，禁止挑选最相近的工具强行办理"。解析侧对空/未知工具名维持"返回空 → 走 fallbackKnowledge"。
4. 补单测（表驱动，加进现有工具识别/路由测试）：
   - "我想预约一次心理咨询" → `DetectTool` 返回 ""，且 `StartFlow` 最终走 `fallbackKnowledge`（断言发出的事件里不出现 book_venue 的"想预约哪个场馆"追问，出现转知识库/answer/citations）。
   - 回归："帮我预约明天晚上的羽毛球馆"仍 `DetectTool=="book_venue"`；"帮我提交明天的事假"仍 `submit_leave`；"现在有哪些场馆可以预约"仍 `query_venues`。

**验收**：`go test ./internal/agent/` 全绿；新增 4 条断言通过。

---

## 提示词 2（P1）：embedding 并发健壮性（首错即停 / worker pool / 空向量校验 / 独立超时）

**目标文件**：`internal/llm/client.go`（主）、对应 `client_test.go`（测试）。

**现状（已定位）**：
- `embedMultimodal`（约 334–379 行）：for 循环对每条文本**一次性全部 `go func`**，信号量 `sem` 在 goroutine 内部才获取（千级 chunk 会瞬间建上千个阻塞协程）；任一条失败只在 mutex 里记 `firstErr`，**不取消其余在途请求，会继续烧完整批配额**。
- `embedText`（约 311–330 行）按 `data[].index` 归位，但供应商漏回某个 index 时 `out[i]` 静默为 nil（`embedMultimodal` 末尾有空向量校验，text 通道没有，两通道不一致）。
- `postJSONWithKey`（约 398 行）统一用全局 `requestTimeout = 10 * time.Minute`；chat 长流式需要它，但单条 embedding 小请求被卡时也要等 10 分钟。

**改法**：
1. `embedMultimodal` 改为**固定 worker pool**：`ctx, cancel := context.WithCancel(ctx); defer cancel()`，用 `jobs chan`（或"主 goroutine 先 `sem<-struct{}{}` 占坑、再 `go`，结束后释放"）把在飞 goroutine 数严格限制为 `embedConcurrency`(=4)，而不是 len(texts)。
2. 任一条请求返回 error 时：记录 `firstErr` 后**立即 `cancel()`**，让其余在途请求因 ctx 取消快速退出；`wg.Wait()` 后返回 firstErr。保留"按输入下标保序回填、整体失败不产出半截结果"的现有语义。
3. `embedText` 归位后补与 multimodal 一致的逐条非空校验：任一 `out[i]` 为 nil/长度 0 → 返回明确 error（指出第几条缺失），不静默。
4. 新增 `const embedTimeout = 45 * time.Second`，给 embedding 的两个通道（embedText / embedMultimodal 内单条请求）使用独立超时；**chat 通道仍沿用 `requestTimeout` 10min 不变**。实现上可给 `postJSONWithKey` 增加一个带 timeout 参数的内部版本（或新增 `postJSONWithKeyTimeout`），由调用方决定超时，避免影响 chat。
5. 补单测（用 `httptest.Server` 模拟）：①第 2 条返回 500 时整体报错且其余请求被取消（可用一个"收到取消才返回"的 handler 断言 ctx canceled）；②响应漏回某个 index 时 embedText 报错；③并发在飞请求数不超过 4（计数器峰值断言）。

**验收**：`go test ./internal/llm/` 全绿；`make ingest REBUILD=1` 仍能正常出 2048 维向量。

---

## 提示词 3（P1，方向 A）：父子块做到真正 1:N 的 small-to-big，并消除父块重复标题

**目标文件**：`internal/rag/hierarchical.go`（主）、`ingest.go`（如需调常量）、`hierarchical_test.go`。

**现状（实测数据）**：校规每节都很短——子块平均 88 字、父块平均 109 字，每个 section 都短于 `childLimit=260`，导致**每个父块只切出 1 个子块、父子正文几乎相同**（严格 1:1）。small-to-big 里的"big"没比"small"大，chunk 数翻倍但检索粒度没变，只赚到 breadcrumb。另外 `ParseMarkdownTree` 的 `bodyStart=m[1]` 让 `node.Body` 以 `# 标题` 行开头，`withPath` 又叠一遍【路径】，标题重复。

**改法（方向 A：粗父细子）**：
1. 利用 `Node.Level` 做两级聚合：新增"父边界层级"概念（常量 `parentHeadingLevel = 2`，即按 H1/H2 聚合，可按需调）。遍历 `ParseMarkdownTree` 的节点：遇到 `Level<=parentHeadingLevel`（或 Level=0 无标题文档）开启一个新父块，把它**及其后所有更深层级节点的正文**归并到该父，直到遇到下一个 `Level<=parentHeadingLevel`。父块聚合文本上限维持 `parentLimit`（建议 600–900 rune，超限再滑切）。
2. 子块在**父块聚合文本范围内**按段落聚合切到 `childLimit`（建议 180–220 rune，overlap 维持 40）；归并进来的每个子节保留其小标题文本作为上下文（让单个子块也知道自己属于哪个小节）。目标：多数父块对应 **≥2 个子块**，父块平均长度明显大于子块。
3. 修标题重复：`ParseMarkdownTree` 生成 `Body` 时跳过标题行本身（从标题行结束、吃掉紧随的换行之后开始取正文），使 `node.Body` 不再以 `# 标题` 开头；breadcrumb 只由 `withPath` 的【路径】承担一次。
4. 保持既有输出契约不变：父块 `IsParent=true/ParentIdx=-1`、子块紧跟其父并指向父下标；空 section 仍不产出父块；无标题文档仍能切出（退化为单父多子）。`HierarchicalChunks` 未使用的 `docID` 形参一并去掉（同步改 `ingest.go:215` 调用处）。
5. 补/改单测：构造一篇含 1 个 H1、其下 3~4 个 H2 短小节的文档，断言：父块数 < 子块数（真 1:N）、每个子块 `ParentIdx` 指向正确父块、父块文本不含重复的 `#` 标题行、输出顺序"父-子-子…-父-子"。

**验收**：`go test ./internal/rag/` 全绿；`make ingest REBUILD=1` 后用下面 SQL 看到父块平均字符数明显大于子块、且父:子不再是 1:1：

```bash
sqlite3 data/index.db "SELECT is_parent, COUNT(), CAST(AVG(LENGTH(text)) AS INT) FROM chunks GROUP BY is_parent;"
sqlite3 data/index.db "SELECT (SELECT COUNT() FROM chunks WHERE is_parent=1) 父, (SELECT COUNT() FROM chunks WHERE is_parent=0) 子;"
```

---

## 提示词 4（P2，可选收尾，一次打包）

**目标文件**：`internal/agent/react.go`、`internal/agent/memory.go`。

1. **ReAct 到顶兜底不留裸错误**：定位 `reactMaxTurns` 达到上限后"再强制收敛一次"的分支——若这次收尾 LLM 调用也报错，当前会直接 `return err`、丢掉已获得的工具结果。改为：优先用已累积的观察结果组织一段部分结论/或给出"暂未完全办成、已查到 X"的兜底答复；只有在完全没有任何可用中间结果时才返回 error。补一条到顶且收尾调用失败的单测。
2. **长期记忆 Facts 加上限**：`memoryBlock` 当前全量注入 Facts，长期会膨胀。改为按固化时间倒序取 Top-N（常量，如 `maxFactsInContext = 20`）注入，超出的保留在库但不进 prompt；RecentEpisodes 维持现状。补"超过 20 条只注入最近 20 条"的单测。

**验收**：`go test ./internal/agent/` 全绿。

---

## 全部改完后的端到端真实验证（务必跑，别只靠单测）

```bash
# 1) 全量门禁
go build ./... && go vet ./... && go test ./...

# 2) 真实重建索引（火山 vision 逐条出 2048 维，看父子比例不再 1:1）
make ingest REBUILD=1
sqlite3 data/index.db "SELECT is_parent,COUNT(),CAST(AVG(LENGTH(text)) AS INT) FROM chunks GROUP BY is_parent;"

# 3) 起服务
go build -o bin/gewu-api ./cmd/server && bin/gewu-api   # 另开一个终端

# 4) health：llm/embeddings 都为 true
curl -s localhost:8000/api/health

# 5) P0 对照（关键）
# 5a 应转知识库 RAG，不再问"选哪个场馆"
curl -sN localhost:8000/api/chat -H 'Content-Type: application/json' -d '{"question":"我想预约一次心理咨询"}'
# 5b 回归：仍正确进入 book_venue 办理流
curl -sN localhost:8000/api/chat -H 'Content-Type: application/json' -d '{"question":"帮我预约明天晚上的羽毛球馆"}'
# 5c 知识问答：route=L1、流式、尾部 citations.items 非空
curl -sN localhost:8000/api/chat -H 'Content-Type: application/json' -d '{"question":"国家奖学金的评定标准是什么"}'
```

预期：5a 不再出现场馆选项、走 RAG 答出医疗/心理相关内容；5b 正常进入场馆预约收集槽位；5c 答案带引用、`citations` 事件 `items` 长度 = RetrievalK(6)。

---

## 诚实边界（面试口径，别写歪）

- 这些是 clean-room 学习项目的工程加固，讲的时候说"真实起服务压测发现规则误匹配/并发浪费/切分退化，分别如何定位与修复"，不要包装成线上大规模数据。
- 父子块"粗父细子 1:N"是可被追问的亮点：要能说清父块只入库不建索引、子块命中后按 parent_id 回取父块上下文的漏斗，以及为什么短文档会 1:1 退化、如何按标题层级聚合解决。
