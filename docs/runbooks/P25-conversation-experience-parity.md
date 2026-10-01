# P25 对话体验对齐：消息操作条 + 来源分组对话框 + 建议追问（任务书）

> **背景**：america.gov/chat（美国政府官方信息 AI 问答助手，与本产品定位同构
> ——官方来源、引用可溯、免费无广告）于 10-01 完成三界面调研（落地页/对话态/
> 来源对话框，截图与 DOM 结构留档本票 §6.5）。对标结论：它的「信息架构与交互
> 模式」值得抄——回答下方建议追问、消息操作条（来源/赞踩/复制）、来源按发文
> 方分组收进对话框、信任声明前置、skip-link 可及性；**视觉皮肤经用户复拍板
> 同样仿**（机构蓝官方风，Q1）——DESIGN.md 已改版 P25 版（navy 主色/冷调近白
> 画布/link 蓝行内链接/用户消息实底气泡），P20 的 Operate 工程纪律原样继承。
>
> 编号说明：P21~P24 已执行完毕（用户体系/会话记忆/管理后台/观测提速）；
> P25 是体验票——换肤（token 值替换）+ 后端两个小件（follow_ups 事件 +
> feedback 端点），其余全在前端对话页。P22 的会话地基（session 归属、
> 历史恢复）直接复用。

## 0. 拍板结论

| # | 问题 | 拍板 | 依据 |
|---|------|------|------|
| Q1 | 抄多少 | **结构与皮肤都仿**（用户 10-01 复拍板，推翻本票初稿「仿结构不仿皮肤」）：交互模式与信息层级照搬；视觉情绪层换为机构蓝官方风——navy 主色 #1a3a5c + 冷调近白画布 + link 蓝行内链接 + 用户消息实底气泡。**DESIGN.md 已改版 P25 版**（换肤契约与迁移注记见其「换肤迁移注记」节）；P20 的 craft 地板（无嵌套卡/熟悉感/density/七态）原样继承 | 「官方制度问答 ↔ 官方机构蓝」语义同构（america.gov 同款逻辑）；用户明示喜欢该风格；工程纪律不随皮肤漂移 |
| Q2 | 来源呈现 | **chips 保留 + 操作条「来源 N」按钮开分组 Dialog**：引用行（chips）维持现状不动，消息操作条新增按钮，Dialog 内按 `source`（发文部门）分组全量展示 | chips 是 RAG 展示签名（毕设答辩要看）；america.gov 的按域名分组 → 我们的按发文部门分组（语料 8 部门实测成立）；渐进呈现不丢信息 |
| Q3 | 追问生成 | **done 之后 SSE 追发 `follow_ups` 事件**：flash 小模型（`llm_small_model` 通道现成）生成 3 条，8s 超时静默降级（无事件=无 pills） | 不占用主路径延迟（P24 结论：串行模型调用是结构性成本）；不做静态 FAQ 派生（维护成本高于一次 flash 调用，且无法因题而变） |
| Q4 | 反馈落库 | **新表 `message_feedback`，upsert 唯一键 `("user", session_id, question)`**：改主意 good↔bad 覆盖；无评论框 | 一次点击一次信号，改主意不产生双行；P23 admin 台账/后续评测可直接 join |
| Q5 | 适用范围 | **仅 chat 主页面**；compare 双轨不加操作条与追问（保持对照实验变量干净）；追问仅在 `route ∈ {factual, research, hybrid}` 且 `reason=completed` 且无 HITL 悬停时生成 | compare 是实验台不是产品面；refusal 追问无意义、transaction 有确认门在悬停 |
| Q6 | 复制按钮 | **纯前端 clipboard**（`navigator.clipboard.writeText`），无后端 | 零成本；历史恢复的静态消息也可用 |

## 1. 目标 / 非目标

**目标**

- **换肤（P25-0，Q1）**：globals.css token 值替换为机构蓝色板（对照
  DESIGN.md P25 版 colors 块），新增 `--link` 一枚；user-message 改
  `bg-primary` 实底；answer prose 链接挂 link token。DESIGN.md 本体已由
  立项会话改版完毕，执行只做代码层。
- SSE 契约增 `follow_ups` 事件（`{type:"follow_ups", items:[str,…]}`）：
  在 `done` **之后**发射——主路径零延迟增量；生成失败/超时静默不发。
- `POST /api/feedback`（登录 + 会话归属校验）：`{session_id, question,
  rating}`，rating ∈ good|bad；upsert 覆盖语义（Q4）。
- 前端消息操作条 `MessageActions`（每条 assistant 消息底部，done 后出现）：
  「来源 N」（无引用则不出现）→ SourcesDialog、👍、👎、复制。
- SourcesDialog：`ui/dialog.tsx`（BaseUI，照 alert-dropdown 的 render 形态
  先例）+ 按 `source` 分组的 disclosure 列表（组头 = 部门名 + 计数徽章，
  组内 = 文档标题链接样式条目 + mono doc_id caption）。
- 追问 pills：回答下方 3 枚 suggestion-chip（复用现有契约样式），点击即
  `send(text)`；事件到达时 fade+y 6px 入场。
- 输入解锁时序：前端从「流关闭」改为「done 事件」解锁（follow_ups 晚到
  不阻塞下一问；aborted/error 路径 finally 兜底保留）。
- 空态信任行：BlurText 标语下加一行 muted 说明（对标 america.gov 信任
  声明）——「回答仅基于钱塘大学官方制度文档生成，全部引用可溯源到发文
  部门；演示语料与业务系统为虚构合成数据」。
- 可及性：skip 链接两枚——「跳到最新回答」+「跳到输入框」（对标 america.gov
  的三连 skip：主内容/最新回答/输入框；它另有常驻 Conversation 导航簇
  Return to message input，量级从简不做）；操作条 icon 按钮全部带 aria-label。
- 错误态重试：error Alert 内附「重试」outline 小钮（重发上一问）——对标
  america.gov 的 assertive alert + **Try again**（chrome-devtools 复核轮
  撞上其接口 418 时实测的错误态模式，本轮新增）。

**非目标**

- 不做 PDF 上传 / 语音输入 / 示例问题轮播（america.gov 有，产品线不同）。
- 不改用户消息样式（america.gov 是深蓝实心气泡；我们维持 surface-user
  安静色块，DESIGN.md user-message 契约）。
- 不动编排、检索、路由链路（纯事件层与展示层增量）。
- 追问与引用不进历史恢复（P22 拍板维持：历史是对话级纯文本；操作条在
  恢复消息上只出「复制」）。
- 不做反馈的 admin 可视化（P23 台账已有底盘，join 留给后续观察票）。

## 2. 设计与实现

**组件库够用性判断（立项时核实）**：现有 ui/ 17 件中，本票全部需求只缺
**dialog** 一件（P25-3 造，BaseUI render 形态）；其余零缺口——实底用户气泡/
navy 圆形发送钮/追问 pill = CSS 与现有 Button/chip 类；来源分组 = Collapsible
+ Badge；部门徽标位 = 圆形 surface-card 底 + lucide Landmark 图标（无部门
徽章资产，图标占位）；信任行 = 现有 Alert/muted 文案；skip-link = 裸 a。
**零新增 npm 依赖。**

### 2.0 换肤落地（P25-0）

- `globals.css`：`:root`/`.dark` 全量值替换（对照 DESIGN.md colors 块；
  shadcn 变量名与 Tailwind 类零改）；`@theme inline` 增 `--color-link:
  var(--link)`；`:root` 增 `--link: #1157d0`、`.dark` 增 `--link: #8ab0dd`。
- `app/page.tsx` user-message：`bg-secondary text-secondary-foreground` →
  `bg-primary text-primary-foreground`（一行）。
- `components/answer.tsx`：prose 链接色挂 link token（`prose-a:text-link
  prose-a:underline prose-a:underline-offset-2` 或 typography 定制入口）。
- 逐页目检亮暗双主题（chat/compare/console/memory/admin/login 六页）+
  design-lint 六页（**已核实 lint 无色值断言，换肤零门禁风险**）；
  对比度抽查：primary 白字（11.6）、link 对画布（6.2）、暗色冰蓝 on-primary
  （7.6）三组已在 DESIGN.md 标注计算值，执行时用浏览器 devtools 复核。
- 空态氛围光/品牌方印/路由徽章均 token 引用，自动变 navy，零代码改动。

### 2.1 后端：follow_ups 事件（P25-1）

- `gewu/agent/events.py` 增 `follow_ups_evt(items: list[str])`。
- `api/chat.py` `generate()`：`done` 发射后、记忆固化前，满足 Q5 条件
  （`trace["route"]` 在白名单、reason=completed、`snap.next` 为空）时：
  - 用 `request.app.state.llm` 的 **small 通道**（`invoke(..., small=True)`
    现成）生成；prompt 要点：角色=校园制度问答助手；输入=本轮问题 + 回答
    前 1200 字 + 引用文档标题列表；输出=恰好 3 条追问，每条 6~30 字、
    不重复原问题、口语化、JSON 数组裸输出。
  - **代码级守卫（GLM flash 无视否定指令的既定坑，三层兜底）**：
    ① JSON 解析失败/非数组 → 弃；② 逐条 `strip` 后过滤长度 6~30、
    与原问题相同、彼此重复（保序去重）；③ 剩余 <2 条 → 弃（不做残缺
    单条）。
  - 超时 8s（生成放 try/except，任何异常只 `print("[chat] follow_ups 生成
    失败（不影响主链路）：…")`）；成功则 `yield _sse(ev.follow_ups_evt(items))`。
  - 计费走 small 通道既有记账（usage 归属 contextvar 已在 generate 内
    re-set，天然覆盖）。
- 单测：mock LLM 返回（正常 / 脏 JSON / 超长条目 / 与原问重复）四路断言
  事件有无与条数；Q5 条件矩阵（refusal 不发、tx 悬停不发、error 不发）。

### 2.2 后端：feedback 端点（P25-2）

- 新表（挂 `gewu/session/store.py` 或 auth 域旁，遵守 lint-arch）：

```
message_feedback(
    id         BIGSERIAL PRIMARY KEY,
    "user"     TEXT NOT NULL,            -- email（保留字双引号）
    session_id TEXT NOT NULL,
    question   TEXT NOT NULL,            -- 轮次锚（≤500，与 chat 校验一致）
    rating     TEXT NOT NULL CHECK (rating IN ('good','bad')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE ("user", session_id, question)   -- upsert 覆盖键
);
```

- `POST /api/feedback`：require_user + session 归属校验（不属于本人 404，
  与 chat 同款防枚举）；成功 204。无 GET（admin 侧 P23 台账后续接）。
- 单测：good→bad 覆盖（行数 1）、未登录 401、他人会话 404、rating 非法
  422、question 超长 422。

### 2.3 前端：Dialog + 消息操作条 + 来源分组（P25-3）

- `components/ui/dialog.tsx`：BaseUI `render` 形态（**硬点**：P22 撞坑 #4
  先例——不是 shadcn 的 asChild；照 alert-dialog.tsx / dropdown-menu.tsx
  的现成范式写，portal + focus trap + Esc 关闭）。
- `components/message-parts.tsx` 增 `MessageActions`：

```
[ BookMarked 来源 3 ]  [ 👍 ]  [ 👎 ]  [ Copy ]
   ↑ outline 按钮           ↑ ghost icon 按钮（aria-label 必备，
                              已选态 disabled + primary 色）
```

  - 素描：无卡片无底色，hairline 上边距分隔，caption 尺寸——消息流的
    安静尾件，不与 DoneMeta 抢层（DoneMeta 在操作条下 8px）。
  - 「来源 N」按钮带来源预览：前两个 source 名（`教务处 +2`）。
- `SourcesDialog`：组头（部门名 600 字重 + `n 来源` 徽章）可折叠（复用
  Collapsible）；组内条目 = `[n] title` + doc_id（caption mono）+ 引用
  序号徽章；空 citations 的消息无此按钮。**Tooltip 不进 Dialog**（doc_id
  直接平铺，消除 portal 嵌套坑）。
- 赞踩交互：本地 state 置灰防重复；POST /api/feedback 失败静默回弹
  （toast 不引入，console.warn 留痕即可）。

### 2.4 前端：追问 pills + 解锁时序 + 信任行 + skip（P25-4）

- `Msg` 增 `followUps?: string[]`；事件分支 `case "follow_ups"`：
  `patchLast({ followUps: items })`（流已过 done，patchLast 仍指向最后
  一条 assistant，天然正确）。
- pills 渲染在 **MessageActions 之后**（america.gov 实序：回答 → 操作条 →
  建议追问；chrome-devtools 复核修正，初稿误写为「Answer 与操作条之间」），
  点击 `send(q)` 后本组 pills 置灰（防连点）；样式复用 suggestion-chip
  契约（pill + hairline + hover surface-strong），不发明第二种 chip。
- **解锁时序（硬点）**：`send()` 内 `case "done"` 时即 `setSending(false)`
  （finally 兜底保留，幂等）；SSE reader 循环自然继续消费 follow_ups。
  验证 aborted（中途断开无 done）：finally 兜底路径单测/手跑覆盖。
- 空态信任行：加在现有 muted 说明行下方（两行 muted，间距 4px），文案
  见 §1；不入 BlurText 动画。
- skip 链接：`app/page.tsx` 顶部 visually-hidden 两枚——「跳到最新回答」
  （锚 `#latest-msg`，bottomRef 容器加 id，历史恢复同样可用）与「跳到
  输入框」（锚 composer textarea id）；sr-only + focus:not-sr-only 惯例。
- 错误态重试：`msg.error` 的 Alert 内附「重试」小钮（组件内记上一问，
  重发走同一 `send()`；disabled=sending 防竞态）。
- compare 页零改动（Q5）。

### 2.5 门禁与文档（P25-5）

- 全绿：ruff / pytest / lint-arch / tsc / next build / design-lint **六页**
  （换肤后 chat/compare/console/memory/admin/login 逐页）。
- 真跑剧本（§6 留档）：登录 → factual 提问 → 回答完成 → pills ≤8s 内
  出现 → 点 pill 续问（自动发送）→ 「来源 N」开 Dialog 部门分组正确 →
  复制粘出全文一致 → 👍 → PG 断言 1 行 → 改 👎 断言仍 1 行（rating=bad）
  → research 提问同验 → tx 悬停轮确认无 pills → 刷新历史恢复（追问轮
  显示为普通问答，操作条只剩复制）→ 未登录 POST /api/feedback 401 →
  错误态（临时停后端再提问）出「重试」钮、重启后端重试成功。
  换肤目检并入首屏：用户消息 navy 实底 / prose 链接 link 蓝 / 亮暗双主题
  截图六页留档。
- 文档：PARITY §0.8（follow_ups 事件顺序 + feedback 契约）+ §3 事件表
  加一行；DESIGN.md components 回写三 pattern（message-actions /
  sources-dialog / follow-up-chip——契约本体已随 P25 版就位，执行后按实况
  微调）；roadmap 挂 P25；本任务书 §6。

## 3. 风险与守卫

| 风险 | 守卫 |
|---|---|
| flash 生成脏输出（无视否定指令既定坑） | 三层代码守卫（§2.1）+ 四路 mock 单测；残缺即弃不发事件 |
| follow_ups 拖长 SSE 连接（done 后挂 8s） | done 先发 + 前端 done 解锁（连接晚关不锁输入）；超时静默；GeneratorExit 早退路径已有 |
| 解锁时序改动破坏中止/错误语义 | finally 兜底 setSending(false) 保留；aborted（无 done）与 error（有 done）两路真跑覆盖 |
| Dialog 组件写成 shadcn asChild 形态 | 照 alert-dialog.tsx 范式（P22 撞坑 #4）；portal + Esc + focus trap 手验 |
| 操作条/追问挤窄屏（390px） | 操作条 flex-wrap + icon-only 收缩；pills 允许换行；design-lint + 移动端目检 |
| Tooltip 在 Dialog 内被裁剪/嵌 portal 坑 | Dialog 内 doc_id 平铺 caption，不进 Tooltip（DESIGN.md portal 契约的顺势简化） |
| 反馈接口刷量 | 登录 + 会话归属 404 + upsert 幂等；question 与 chat 校验同限 |
| compare 被顺手带上操作条（污染对照实验） | MessageActions 只在 chat 页渲染（组件不放 eventStream 共用层）；diff 手检 |
| pills 与底部 SUGGESTIONS 视觉混同 | follow-up pills 带「继续问」caption 前缀或 ChevronRight 图标，与空态建议区分 |
| 「重试」重发与 sending 状态竞态 | 重试钮 disabled=sending；重发走同一 send() 路径（写操作由确认门 interrupt 兜底幂等） |
| 换肤后 primary 消费位膨胀（用户气泡+路由徽章+发送钮同屏撞色） | DESIGN.md do 封顶六处已立契约；目检时重点看对话页三色同屏层次（实底气泡 right / 徽章 left / 发送钮底部，位置天然分离） |
| 暗色 navy 提亮后与 link 冰蓝同值难分 | 暗色下两者本就同值（DESIGN.md 既定）；link 恒带下划线消歧 |
| `--link` 新 token 漏挂导致 prose 链接仍走旧色 | tsc/build 后六页目检含链接抽查；grep prose-a 链接类落位 |

## 4. 验收门禁

- [x] pytest 全绿（follow_ups 四路 mock + Q5 条件矩阵 + feedback CRUD/越权/覆盖）——**240 passed**（225→240，+15）
- [x] ruff + lint-arch 全绿（feedback.py 入 api 禁 llm 清单）
- [x] tsc + next build + design-lint **六页**零 finding（换肤零改动即过——token 层收口兑现）
- [x] 换肤落地：token 对照 DESIGN.md P25 colors 逐值核对；对比度计算值随契约标注（11.6/6.2/7.6），浏览器视觉目检过
- [x] 真跑剧本（§2.5）全过，PG feedback 行断言留档 §6.3
- [x] PARITY §0.8 + §3 / DESIGN.md 三 pattern 回写 / roadmap / §6 更新

## 5. 遗留与后续

- 反馈的 admin 可视化与按 route 聚合分析：P23 台账 join，观察票。
- 追问的 A/B（有/无 pills 对追问率的影响）：毕设评测章节可选实验，不在本票。
- america.gov 的示例问题轮播 / 语音输入 / PDF 上传：产品线差异，不做。
- 落地页级营销叙事（29,000 websites into one 式特性区）：不适用（我们是
  登录后工具，不是公共服务门户）。

## 6. 执行记录

**执行日期：2026-10-01（当日立项当日执行完毕，立项与执行同会话）。**

### 6.1 交付清单（对照 §2 ticket）

- **P25-0 换肤**：`globals.css` `:root`/`.dark` 全量值替换（shadcn 变量名与
  Tailwind 类零改）+ `@theme inline` 新增 `--color-link`；`page.tsx`
  user-message `bg-secondary` → `bg-primary`（一行）；`answer.tsx` prose 链接
  挂 link token（text-link + underline + decoration-link/40）；`icon.svg`
  navy 底白字同步。六页全部随 token 自动换肤，无一处页面级改色。
- **P25-1 follow_ups**：`gewu/agent/followups.py`（should_generate 门 +
  guard_follow_ups 三层守卫 + generate_follow_ups 门面——独立线程 + join
  8s 超时静默，work() 首行显式 set contextvar 记账归属，_consolidate_async
  同款）；`events.py` 增 `follow_ups_evt`；`chat.py` generate() 收集
  citations 事件 + 终态读取 `snap.next`（HITL 悬停判定）+ done 之后按 Q5 门
  追发。
- **P25-2 feedback**：`gewu/session/store.py` 增 `FeedbackStore`
  （`message_feedback` 表，UNIQUE("user", session_id, question) +
  ON CONFLICT DO UPDATE 覆盖）+ `make_feedback_store` 探测式软降级（PG 不可
  达 → 端点 503 不拦主链路）；`gewu/api/feedback.py` POST /api/feedback
  （登录 + 归属 404 防枚举 + 422 校验，成功 204）；`app.py` 装配 +
  lint-arch 清单收编。
- **P25-3 前端组件**：`ui/dialog.tsx`（BaseUI `render` 形态，照 alert-dropdown
  先例——portal + backdrop-blur 遮罩 + rounded-xl + shadow-md 浮层档）；
  `components/sources-dialog.tsx`（按 source 分组保序、组头徽标位+计数+折叠、
  组内 [n]+标题 link 蓝+doc_id mono 平铺、触发钮带部门预览）；`message-parts.tsx`
  增 `MessageActions`（来源 N/👍/👎/复制，纯展示反馈走回调，已选态置灰，
  onlyCopy 判定=route 与 latency 皆空的恢复消息）。
- **P25-4 交互**：`page.tsx` follow_ups 事件分支 + patchAt/rate 助手函数 +
  done 即 setSending(false)（finally 兜底保留幂等）+ 追问 pills（MessageActions
  之后，点击发送后本组置灰，fade+y 6px 入场）+ 错误 Alert 内重试钮（重发上一问，
  disabled=sending）+ 空态信任行 + skip 链接两枚（#latest-msg/#composer-input
  锚）；`lib/api.ts` 增 `sendFeedback`。compare 页零改动（Q5）。

### 6.2 门禁结果（全绿）

| 门禁 | 结果 |
| --- | --- |
| pytest | **240 passed**（225→240，+15：followups 9 + feedback 5 + chat 事件序 2；26:44 为 PG 咨询锁串行环境常态） |
| ruff check + format | 全绿 |
| lint-arch | 全绿（feedback.py 入 api 禁 llm 清单） |
| tsc --noEmit | 全绿 |
| next build | 全绿（六页 + icon.svg） |
| design-lint **六页** | 全绿零 finding（换肤零改动即过——P25-0 立项时「lint 无色值断言」预判兑现） |

### 6.3 真跑剧本（§2.5 全过）

后端 curl（uvicorn :8000，账号 p25test@qtu.edu.cn）：

1. 邀请码注册登录 → POST /api/sessions 下发会话。
2. direct 真跑（图书馆开放时间）：citations（图书馆）→ done completed 9323ms
   → **follow_ups 3 条**（分馆时间/考试周/节假日——flash 真生成质量在线），
   事件序 done→follow_ups 正确。
3. tx 悬停轮（预约场馆，classic）：pending_action(book_venue) → done，
   **follow_ups 0 条**（Q5 门生效）。
4. feedback：good 204 → bad 204 → PG 断言 1 行 rating=bad（覆盖非双行）；
   未登录 401。

前端浏览器（next start :3200 + chrome-devtools，亮暗双主题）：

5. 登录 → 历史会话恢复（操作条仅复制——onlyCopy 判定正确）。
6. 新会话 auto 提问（转专业绩点）：research 30140ms 完成 → 操作条
   （来源 3 教务处/👍/👎/复制）+ 追问 pills ×3 → 来源 Dialog 部门分组
   正确（教务处 3 来源 + doc_id 平铺）→ 👍（PG good 落库 + 按钮置灰）→
   点追问 pill 自动续问（旧组置灰，新答 2042ms）。
7. **停后端 → 提问 → 错误 Alert + 重试钮 → 重启后端 → 重试成功**（直答完成，
   多部门预览「来源 4 教务处 +1」正确，追问 pills 再生成）。
8. 刷新页面：同会话恢复，追问轮显示为普通问答（事件级不恢复，符合拍板）。
9. 换肤目检：navy 主色/冷调近白画布/深蓝实底用户气泡/来源对话框规范
   （视觉模型确认无暖色残留）；暗色主题切换正常。

### 6.4 撞坑与观察（对后来者有值）

1. **3200 残留旧生产服 × rm .next 复发**（P21 坑①/P20 坑）：换肤后重建
   .next，旧 server 的 chunk 全断（ChunkLoadError）→ design-lint 六页
   Navigation timeout 假失败。`lsof -ti tcp:3200 | xargs kill` 后秒绿。
   排查顺序：先看 server 是不是旧的，再怀疑样式。
2. **ruff E501 对 CJK 长字符串**：中文字符按 1 计但仍易超 100——测试数据
   拼装改 `json.dumps(list)` 分行，或元组拆两行；noqa 不如拆行。
3. **守卫测试数据的边界巧合**：用作「超长过滤」样例的句子恰好 30 字
   （守卫上限含 30）→ 测试红。CJK 手数长度不可靠，构造越界数据要 +2 字余量。
4. **观察（非本票缺陷，留检索/语料线）**：追问轮「学分认定的申请截止时间」
   被 flash guard 判「寒暄」（已知路由漂移，agent 兜底回答正确）；校医院轮
   citations 带入 3 条不相关转专业文档（检索召回噪声）。
5. `analyze_image` 对 >400KB 截图持续 400（图片解析错误），缩视口/关键帧
   截图可解；DOM 快照（a11y 树）始终可靠，交互断言优先走快照。

### 6.5 对标调研留档（2026-10-01，立项会话两轮）

- **第一轮（browser-use IAB）**：落地页/对话态/来源对话框 DOM 快照与截图
  （会话 artifacts）；对话态关键结构——用户消息右对齐深蓝实底气泡、回答
  article 无气泡直铺、Message actions（View sources 带机构徽标预览 +
  Good/Bad + Copy）、Suggested follow-ups ×3、来源 Dialog 按域名分组
  （state.gov 7 / usembassy.gov 8 / …组头徽章+计数，组内页面标题链接）。
- **第二轮（chrome-devtools MCP 复核）**：三连 skip 链接（主内容/最新回答/
  输入框）+ 常驻 Conversation 导航簇确认；**错误态实测**（撞其接口故障）=
  assertive alert + Try again → 已补入 §1/§2.4；**追问实序修正**（回答 →
  操作条 → 追问）→ 已改 §2.4；落地页页脚补全（Submit feedback 钮/语言
  切换/GSA 署名——均不入本票范围）。
- **前端工程侧发现（仅留档，无意采用）**：页面加载 ONNX 模型
  `nationaldesignstudio/rampart`（transformers.js 浏览器端跑输入分类守卫）；
  其 chat 接口与我们同路径 `POST /api/chat`；Cloudflare 对
  `--enable-automation` 的 Chrome 拒答 **418**（IAB 昨日反而可过）——
  对该站自动化调研用 IAB 或手动浏览器，automation Chrome 会被 CF 拦。
