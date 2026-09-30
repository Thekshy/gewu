# P11 可演示前端 · 对比展示台（SPEC + ticket + 验收）

> 背景：项目 README 已重定位为「agent 方案实践对比学习项目」，但 `apps/web` 现有单页聊天
> 只能演示**产品形态**（问答 + 办理），演示不了**项目卖点**——方案对比与链路可解释。
> 本次按 AI Coding Playbook 流程（Grilling → SPEC → 拆 ticket → 逐 ticket 开发 →
> code review → 验收）构建可演示前端。执行窗口与规格窗口合一（单人单会话），
> ticket 纪律保留：一 ticket 一提交粒度、每 ticket 过独立门禁。
>
> 原则：
> 1. **零后端改动**：只用既有 6 个 API 与 SSE 契约（PARITY.md §3/§4）；
>    A/B 对照用 `mode=react` 按请求入口（pipeline.go:147），不碰全局 `ROUTER_MODE`；
> 2. **零新依赖**：CI web 门禁是 `npm install && npm run build`（ci.yml web job），
>    不引组件库/Tailwind，锁文件不动，构建时长不变；
> 3. **SSE 契约全事件有呈现**：含 P10 新增的 `slot_question` 专用卡片与
>    `done.reason` 徽标（前端此前未呈现）；
> 4. **会话隔离**：A/B 两轨必须独立 `session_id`，否则办理流程状态互串；
> 5. 门禁不降级：`go test ./...` 全绿（后端零改动应天然绿）+ `next build` 通过 +
>   真跑冒烟留档。

---

## 0. Grilling 拍板结论

10 问已出，用户未逐条答复（异步流程），**全部按推荐项缺省拍板**，在本文留痕；任何一条可推翻，推翻即重跑对应 ticket。

| # | 问题 | 拍板 | 依据 |
|---|---|---|---|
| Q1 | apps/web 已有可用聊天页，「缺可演示前端」指什么 | **升级为对比展示台**，保留聊天骨架 | 现状只能演示产品，演示不了对比卖点 |
| Q2 | 受众 | **面试 live demo 优先**，兼顾自解释录屏（roadmap M4） | 求职展示项目 |
| Q3 | 对比卖点怎么呈现 | **同题 A/B 对照**：`mode=auto`（级联 workflow）vs `mode=react`（ReAct agent）并发双流 | `mode=react` 是按请求入口，零后端改动 |
| Q4 | 技术栈 | **保持 Next.js 15 + 手写 CSS，零新依赖** | CI 门禁不变，锁文件不动 |
| Q5 | 页面范围 | 聊天页升级 + `/compare` 对比台 + `/console` 控制台（业务台账 / 检索调试 / 语料 / 健康） | 覆盖全部既有 API |
| Q6 | ReAct 轨迹深度 | 基于 `status` 事件（`调用工具 X…`）渲染**工具时间线**；不新增后端事件 | SSE 契约最小实现；`tool_trace` 结构化事件留待后续提案（非目标） |
| Q7 | 验收口径 | next build + go test 全绿 + **真跑冒烟三场景** + 浏览器实测 | 仓库全绿门禁惯例 |
| Q8 | 部署 | 本地 `make demo` 为主；docker web 服务兼容不动 | compose 已有 |
| Q9 | SSE 细节 | `slot_question` 槽位卡片；`done.reason` 四值徽标（completed/max_tokens/error/aborted） | P10 契约只增不改，前端补呈现 |
| Q10 | 流程载体 | 本文档（P11 编号未被占用；上下文压缩仍为候选未下发） | 仓库 P 系列惯例 |

## 1. 目标 / 非目标

**目标**

1. 聊天页 30 秒讲清一条链路：路由徽标（含决策理由）→ 进度 → 答案 → 引用 → 延迟/结束原因；
2. `/compare`：一次提问并发打两条链路，事件流并排流式呈现，轨道间会话隔离、确认流各自可续，事后可看两轨差异（路由决策 / 事件数 / 延迟 / 引用）；
3. `/console`：业务台账证明交易真实落库（含演示重置）、检索调试台直达 `/api/search`、语料列表、健康与预算；
4. 前端呈现面覆盖 PARITY §3 全部 10 类事件。

**非目标**

- 不改 Go 后端（任何后端诉求另立提案）；
- 不引前端依赖、不做组件库迁移；
- 不做移动端专项设计（桌面优先，窄屏可用的响应式兜底）；
- 不做 Playwright E2E（门禁维持 `next build`；E2E 另立 ticket）；
- 不做公网部署与录屏（M4 另行）。

## 2. 设计要点

### 2.1 页面架构（Next.js App Router，零新依赖）

```
apps/web/
  app/
    layout.tsx          # 加顶部导航（对话 / 对比实验 / 控制台）
    page.tsx            # 聊天页（升级，见 2.3）
    compare/page.tsx    # 对比实验台（新）
    console/page.tsx    # 演示控制台（新）
    globals.css         # 追加对比台/控制台样式，沿用既有 token
  components/
    eventStream.tsx     # 事件流时间线渲染（compare 复用；聊天页维持气泡形态）
    nav.tsx             # 顶部导航（含后端连通状态点）
  lib/
    api.ts              # 契约层：补 slot_question/done.reason 类型 + 4 个端点封装
```

### 2.2 A/B 对照的关键约束（/compare）

- **双流并发**：同一 question、同一 role，两个独立 `streamChat`：A 轨 `mode=auto`，
  B 轨 `mode=react`；
- **会话隔离**：两轨固定独立 session（`compare-a-<uuid>` / `compare-b-<uuid>`，跨轮复用
  保持各自多轮上下文），B 轨 ReAct 的写操作确认（pending_action → 「确认」）发到 B 轨
  自己的 session，不污染 A 轨；
- **事件流形态**：每轨按事件到达顺序渲染时间线（route / status / step / slot_question /
  pending_action / action_result / answer / citations / done），status 的
  `调用工具 X…` 渲染为工具 chip；两轨各自 done 后显示延迟与 reason；
- **差异摘要**：两轨均 done 后展示对照行——路由决策（route+reason）、事件计数、延迟、
  引用数；不做自动评判（评测是 eval/ 的事，前端只呈现事实）。

### 2.3 聊天页升级（不改信息架构）

- `slot_question`：渲染为槽位追问卡片（slot 标签 + 问题），与确认卡片视觉同级；
- `done.reason`：completed 显示延迟徽标；`max_tokens` 加「已截断」、`error` 加「出错」、
  `aborted` 加「已中断」警示徽标；
- 回答模式选择器加 `react`（ReAct 自主编排，直连 mode=react 入口）；
- 头部加导航；其余（角色切换、建议、确认流、引用）维持现状。

### 2.4 控制台（/console）

- 业务台账：`GET /api/business/overview`（预约 + 请假单表格化）+ `POST /api/business/reset`
  （演示重置，二次确认）——「写操作真实落库」的现场证据；
- 检索调试：`POST /api/search`（query + k），表格展示命中块与分数，演示混合检索不经 LLM；
- 语料与健康：`GET /api/docs` 列表 + `GET /api/health`（含预算用量）。

## 3. 验收标准（Given-When-Then）

| # | 场景 | 标准 |
|---|---|---|
| A1 | CI 门禁 | `npm run build`（apps/web）与 `go test ./...` 全绿；`npm ls` 无新增依赖 |
| A2 | 聊天-直答 | 问「转专业绩点要求」→ 路由徽标 factual、流式答案、引用非空、done 显示延迟 |
| A3 | 聊天-办理确认流 | 「帮我预约明天晚上羽毛球馆」→ slot_question 卡片追问 → pending_action 确认卡 → 确认后 action_result 回执；/console 台账出现该预约 |
| A4 | 聊天-react 模式 | 选 react 模式重发 → status 呈现「调用工具 X…」序列后收敛答案 |
| A5 | 对比台 | 任一问题 → 两轨并发流式、互不串流；两轨均 done 后差异摘要正确（延迟、事件数、路由理由） |
| A6 | 对比台确认隔离 | B 轨（react）触发 pending_action 并确认 → 只有 B 轨收到 action_result，A 轨无感知 |
| A7 | 控制台 | 台账/检索/语料/健康四卡数据与 curl 直连一致；reset 后台账清空 |
| A8 | 结束原因 | done.reason 渲染函数覆盖 completed/max_tokens/error/aborted 四值（构建期类型 + 手测） |

## 4. Ticket 拆分（一 ticket 一门禁）

| # | 内容 | 门禁 |
|---|---|---|
| T1 | `lib/api.ts` 契约层扩展（slot_question / done.reason 类型 + overview/docs/search/reset 封装），现有页零行为变化适配 | next build |
| T2 | 聊天页升级：slot 卡片、reason 徽标、react 模式、导航 + layout | next build + 手测 A2/A3/A4 |
| T3 | `/compare` 对比实验台（双流并发、会话隔离、时间线、差异摘要） | next build + 手测 A5/A6 |
| T4 | `/console` 控制台（台账/检索/语料/健康） | next build + 手测 A7 |
| T5 | 收口：README/roadmap 勾稽、A1 全绿、验收留档（本文 §5） | 全部 |

## 5. 执行记录

> 2026-09-14 执行（单窗口顺序执行 ticket，每 ticket 过独立门禁）。环境：Node v25.9.0 /
> Go 1.25 / API 以 `make run` 真跑（LLM+向量均启用，预算用量见控制台卡）。

### 5.1 Ticket 执行

| # | 结果 | 证据 |
|---|---|---|
| T1 契约层 | ✅ next build 通过，锁文件零变动 | `lib/api.ts`：ChatMode 加 react、DoneReason、4 端点封装 |
| T2 聊天页升级 | ✅ build + 真跑 | slot 卡片（`待补充·日期`）、react 模式选项、导航；见 5.2 |
| T3 对比台 | ✅ build + 真跑双流 | 见 5.2 A5/A6 |
| T4 控制台 | ✅ build + 真跑（数据卡全渲染） | 见 5.2 A7 |
| T5 收口 | ✅ 全绿门禁 + 本节留档 | 见 5.3 |

### 5.2 验收判定（对照 §3）

| # | 判定 | 证据 |
|---|---|---|
| A1 门禁 | ✅ | `go test ./...` 全绿；`next build` 通过（/, /compare, /console 三路由）；`gofmt`/`go vet`/`make lint-arch` 通过；`package.json` 零新增依赖 |
| A2 直答/研究 | ✅ | UI 真跑：路由徽标（深度研究，reason tooltip）、3 子问题轨迹+来源、流式答案、3 条引用、59189 ms；直答链路另有 curl 冒烟（factual/L1-llm/11759ms/citations×3） |
| A3 办理确认流 | ✅ | UI 真跑：L0 规则快路径徽标 → 确认卡（场馆/日期/时段/用途中文标签）→「确认办理」→ 回执 ✔ VE-0163；`slot_question` 卡片真跑呈现（`待补充·日期` + 追问文本，slot=date）；台账出现该单 |
| A4 react 模式 | ✅ | UI 真跑：模式切换 → 答案+引用+20395ms；工具调用序列的可视化在对比台 B 轨持久呈现（🔧 parse_date + 「已整理办理信息，等待确认…」）；curl 冒烟另见 4×search_knowledge 工具循环 |
| A5 对比台双流 | ✅ | UI 真跑：同题双发并发（A 7870ms/4 事件，B 10482ms/6 事件），双轨时间线流式呈现，差异摘要表（路由判定/事件数/耗时/引用数）正确 |
| A6 确认隔离 | ✅ | UI 真跑：仅点 B 轨「确认办理（本轨）」→ B 轨收到 action_result（✖ 每人每天最多预约 2 个时段——当天已有 VE-0162/0163 两单，业务规则真实生效），A 轨无任何变化 |
| A7 控制台 | ◐ 数据全通、两按钮环境受阻 | 台账 3 单与 curl 直连一致、健康卡（46,084/2,000,000 tokens 2.3%）、语料 15 篇全列；「检索」「演示重置」按钮点击被测试环境三重锁死（详见 5.4），按钮→fetch→渲染的模式已由其余页面 13 次 POST /api/chat 证明，`/api/search` 响应由 curl 验证 |
| A8 结束原因 | ✅（类型级）| DONE_BADGE 覆盖 completed/max_tokens/error/aborted 四值，completed 无徽标（所有真跑轮次均 completed，运行时未触发其余三值——截断依赖 max_tokens 撞线，属小概率事件） |

### 5.3 门禁与附带修复

- **附带修复（超出 P11 范围但为全绿门禁所必需）**：`TestReActWriteToolGoesConfirmFlow`
  写死日期 `2026-09-08`，于 2026-09-14 过期触发「不能预约过去的日期」（预先存在的时间
  炸弹，与前端改动无关——Go 生产代码零改动）。修复：改为 `time.Now().AddDate(0,0,2)`
  相对日期（仅测试文件）。
- Node v25 下 `next dev` 按需 SSR 因 webstorage 全局怪癖 500（`localStorage.getItem is
  not a function`）；`next build` + `next start`（静态预渲染）不受影响。**演示/CI 均走
  build 路径，无影响**；本机 `make dev-web` 若遇此问题可用 Node 22（CI 同版本）。

### 5.4 测试环境备注（诚实记录）

- 内嵌浏览器（IAB）的合成层缺陷：截图 capture 失败、滚动手势超时、指针级点击命中测试
  超时；节点路径（dom_cua）点击在对话/对比页可用、在控制台页不可用。系统级原生点击
  分发被宿主 app 窗口锁死（连 Chrome 全屏独立空间也打不进）。受此影响，A7 的两个按钮
  点击未能闭环；其余全部测试点以 DOM 快照 + API 日志双证据通过。
- 上述均为**测试环境缺陷，非产品缺陷**：同一会话中 13 次浏览器侧 `POST /api/chat`
  全部成功（含 59s 研究链路、对比台双发、确认流），web 事件绑定与 fetch 层端到端工作。
- 已知 UX 细节（非缺陷）：对比台「本轮差异」取两轨各自最新一轮，B 轨单独确认后两轨
  轮次不对称（A 停在原轮，B 进入确认轮），摘要数字含义随之变化。

### 5.5 遗留与后续

- [ ] 控制台「检索」「演示重置」按钮：本地浏览器一次点击即可复核（curl 层已验证）
- [ ] `tool_trace` 结构化事件提案（ReAct 工具循环目前借道 status 文本，Q6 拍板的后续项）
- [ ] roadmap M4「5 分钟演示录屏」：三页面就绪后可开录（建议脚本：对话页直答→办理确认
      →控制台台账佐证→对比台同题双发收尾）
- [ ] Playwright E2E（Q7 拍板的非目标，另行立项）

