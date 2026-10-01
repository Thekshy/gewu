# P16 前端 UI 现代化：shadcn/ui + Base UI（任务书 + 执行记录）

> **背景**：apps/web 为 Next.js 15 App Router + React 19，三页（对话 / 对比实验 /
> 控制台）+ 911 行手写 globals.css，零 UI 依赖，观感朴素。毕设答辩与求职展示都
> 需要更现代的演示观感。2026 年组件库选型调研后拍板 shadcn/ui（Base UI 底座）。
> 本任务只换**呈现层**：SSE 事件流契约、状态机逻辑、后端零改动。

## 0. 拍板结论（AskUser 确认）

| # | 问题 | 拍板 | 依据 |
|---|------|------|------|
| Q1 | 组件库 | **shadcn/ui + Base UI 底座**（2026-07 起 init 默认即 Base UI，无需 flag） | 国际 AI 产品标准审美；组件源码 copy 进仓库、MIT，符合「免费无阉割」；暗色模式白送 |
| Q2 | 范围 | 三页全迁 + 暗色模式 + 回答 markdown 渲染 | 演示观感最大化；markdown 渲染对 LLM 输出观感提升不亚于换组件库 |
| Q3 | 逻辑层 | **零改动**：`lib/api.ts`、`lib/labels.ts`、各页事件处理 switch、会话/双轨隔离状态机原样保留 | SSE 状态机是自研学习成果与面试素材；UI = f(state)，本任务只换 f 的呈现半边 |
| Q4 | 备选处置 | antd X / assistant-ui 不采用 | assistant-ui 绑 Vercel AI SDK 数据流会替掉自研状态机；antd X 审美路线不同 |
| Q5 | 编号 | P16 | P15（RAG 对齐 WeKnora）进行中，互不阻塞 |

## 1. 目标 / 非目标

**目标**
- shadcn/ui 落地：`npx shadcn@latest init`（CLI v4），Base UI 底座组件 copy 进
  `components/ui/`（仓库内源码，非 npm 黑盒）；Tailwind CSS v4（CSS-first `@theme`）。
- 三页逐页迁移到 Tailwind + shadcn 组件（映射见 §3），迁移完成即删对应旧 CSS。
- 回答区 markdown 渲染：`react-markdown` + `remark-gfm` + `@tailwindcss/typography`
  （`prose prose-sm dark:prose-invert`），聊天页与 compare 两轨答案共用。
- 暗色模式：`next-themes`（class 策略 + Tailwind v4 `@custom-variant dark`），
  nav 右侧 Sun/Moon 切换按钮。
- 图标全量换 `lucide-react`：🔧→Wrench、✔/✖→CheckCircle2/XCircle、时间线
  占位符（·？▢→）→Loader2/Search/SquarePen/ArrowRight 等。
- 真跑验收：三条回答路线 + 办理确认流 + ReAct 工具 chip + compare 同题双发 +
  console 四面板 + 亮/暗两主题全走查。

**非目标**
- 不动 `lib/api.ts` SSE 契约与后端（git diff apps/server 必须为空）。
- 不动会话逻辑（sessionId refs、patchLast、双轨 session 隔离）。
- 不升级 Next（15.3.3 官方支持 Tailwind v4 + React 19，够用）。
- 不做移动端专项适配、不引 framer-motion 动效（列为后续可选加餐）。

## 2. 现状盘点（迁移对象）

| 文件 | 行数 | 内容 |
|---|---|---|
| `app/page.tsx` | 341 | 聊天主页：header/banner/气泡/trace/槽位卡/确认卡/回执/引用/composer |
| `app/compare/page.tsx` | 283 | A/B 双轨 + 差异表 |
| `app/console/page.tsx` | 304 | 健康/台账/检索调试/语料四面板 |
| `components/nav.tsx` | 28 | 顶部三视图导航 |
| `components/eventStream.tsx` | 169 | TrackPanel：时间线 + 答案 + 确认流（compare 专用） |
| `app/globals.css` | 911 | 手写设计系统（CSS 变量 + 全部组件样式） |

## 3. 组件映射表（现状 → shadcn）

**聊天主页**
| 现状 | 迁移目标 |
|---|---|
| `.header` / `.logo`「格」 | Tailwind 排版重写；logo 用 lucide（GraduationCap/Sparkles）+ 主色方块 |
| `.banner`（LLM 未配置/后端断连） | Alert（默认/警告 variant） |
| `.empty` 空态 + SUGGESTIONS chips | 卡片式空态 + `Button variant="outline" size="sm"` chips |
| `.badge.{route}` 路由徽章 | Badge + variant 映射（direct=secondary / research=default / react=outline / workflow=secondary） |
| `.trace`（details 研究过程） | Collapsible（defaultOpen={!done}），子问题列表排版保留 |
| `.status` 思考中 | Loader2 spin + muted 文本；长等待可 Skeleton |
| `.slot-card` 待补充 | Card + Badge「待补充」 |
| `.confirm-card` + dl + buttons | Card + CardHeader/CardTitle + dl（语义保留）+ Button（default「确认办理」/ outline「取消」） |
| `.receipt ok/fail` | Alert + CheckCircle2 / XCircle（success=default，fail=destructive） |
| `.citations` `.cite-chip` | Badge variant=secondary + Tooltip 悬浮显示 doc_id |
| `.answer` 纯文本 `<p>` | **Markdown 渲染**（prose 排版） |
| `.meta` latency/done-badge | muted 小字 + Badge（DONE_BADGE 映射沿用 labels.ts） |
| `.inputbar` composer | Textarea + Select（mode/role）+ Button（Send icon，sending 时 Loader2） |

**compare / console / nav**
| 现状 | 迁移目标 |
|---|---|
| `.tracks` 双轨 | grid 两张 Card；TrackPanel 内部样式 Tailwind 化，数据流不动 |
| 时间线 `.tl-item` / `.tool-chip` | 保留 ol/li 结构；KIND_ICON 换 lucide；工具 chip 换 Badge+Wrench |
| `.diff` 差异表 | Table 组件 |
| `.panel` 四面板 | Card + CardHeader/CardTitle/CardDescription |
| 台账/语料 table | Table 组件（两处业务表 + 语料表） |
| `.bar` 预算条 | Progress + 百分比文本 |
| `.dot ok/off` 健康点 | lucide 圆点（fill 色）或 Badge |
| 检索调试表单 | Input + Select（top-k）+ Button |
| 演示重置手写 confirming 二次确认 | AlertDialog（destructive 确认清空） |
| `.nav-item` | Link + active 样式 Tailwind 化；右侧挂主题切换（Button ghost + Sun/Moon） |

组件清单（一次 add）：`button card badge input select textarea table alert
alert-dialog tooltip separator collapsible scroll-area skeleton progress
dropdown-menu`（sonner 可选，暂无 toast 场景不强装）。

## 4. 任务分解

### P16-1 底座接入（半天）
1. `apps/web` 下 `npx shadcn@latest init`：选 Base UI 默认底座；生成
   `components.json` + 新 globals.css（design tokens + Tailwind v4）+ `cn` 包
   （2026-09 起 init 自带，不再手写 lib/utils.ts）。
2. **旧样式过渡**：旧 globals.css（911 行）改名 `app/legacy.css`，在新
   globals.css 末尾 `@import "./legacy.css"`——迁移期新旧共存，逐页迁完逐段删。
3. **preflight 冲突规避**：迁移期 Tailwind 只引
   `theme + utilities` 两层（`@import "tailwindcss/theme.css" layer(theme)` +
   `@import "tailwindcss/utilities.css" layer(utilities)`），不引 preflight
   reset，避免打击未迁移页面的既有样式；P16-4 删净 legacy 后恢复完整 import。
4. 装 `lucide-react`、`react-markdown`、`remark-gfm`、`@tailwindcss/typography`、
   `next-themes`；`ThemeProvider` 挂 layout（html 加 `suppressHydrationWarning`）。
5. 验证：`npm run build` 绿 + `next dev` 三页现状不破（清一次 `.next` 缓存）。

### P16-2 聊天主页（1 天）
按 §3 映射逐块替换 JSX；事件处理 switch / patchLast / sessionId 一行不动。
composer 交互细节保留：Enter 发送 / Shift+Enter 换行 / `isComposing` 中文输入法
守卫。回答区接 markdown 渲染（Answer 组件抽出，compare 复用）。

### P16-3 compare + console + nav（1 天）
- TrackPanel 样式化 + 差异表换 Table；确认流按钮回调（onConfirm/onCancel）不动。
- console 四面板换 Card；「演示重置」换 AlertDialog（替换 confirming useState）。
- nav 加主题切换按钮；三视图 active 态重做。

### P16-4 收尾与验收（半天）
1. 删除 `app/legacy.css` 与 globals.css 中已迁移段落（目标余量 <100 行，只留
   CJK 排版微调等特殊自定义）；恢复完整 Tailwind import。
2. 亮/暗两主题全页走查（含 markdown prose 的 `dark:prose-invert`）。
3. 真跑验收（§5 清单）+ 全绿门禁：`npm run build`；`git diff apps/server` 为空。
4. 文档同步：docs/README.md 技术栈一句（前端 UI 栈），必要时更新
   docs/architecture/08-api-contract.md 中前端描述（仅描述，契约不变）。

## 5. 真跑验收清单

1. 起 :8000 API（有 LLM key）+ :3100 web。
2. 聊天页：direct 直答（引用 chips + Tooltip）；research 深研（Collapsible
   trace + 子问题）；办理流（预约/请假：槽位追问 → 确认卡 → 回执 Alert）；
   react 模式（工具 Badge 时间线）；建议 chips 点击即发。
3. compare：同题双发 → 两轨时间线/答案/引用 → A 轨确认不影响 B 轨 → 差异表。
4. console：健康/预算 Progress；台账两表 + AlertDialog 重置；检索调试命中列表；
   语料表。
5. 主题切换：亮/暗各走一遍 2~4；nav 切换不闪白（suppressHydrationWarning）。
6. 降级路径：无 LLM key 时检索演示模式 banner；后端断连 warn banner。

## 6. 风险与对策

| 风险 | 对策 |
|---|---|
| shadcn init 改写 globals.css | 执行前确保 P15 已 commit（或独立 commit 存底）；旧样式转 legacy.css，见 P16-1.2 |
| preflight reset 打击未迁移页 | 迁移期只引 theme+utilities 两层（P16-1.3），收尾再恢复 |
| Base UI 底座组件不全 | 所有用例均为基础组件（dialog/select/tooltip 等 Base UI 均有）；不够再单独补 Radix 版同名组件，互不冲突 |
| Next 15.3 + Tailwind v4 + React 19 | 官方支持矩阵内；装完清 `.next` 重 build |
| prose 中文排版（字号/行高） | typography 插件 + 少量 CSS 变量微调，落 globals.css 余量内 |
| CJK 输入法 Enter 误发 | isComposing 守卫原样保留并在验收清单复测 |

## 7. 执行记录（2026-10-01，四子任务一次会话完成）

### 7.1 实际落地

| 项 | 结果 |
|---|---|
| 底座 | shadcn CLI 4.21 init（`-b base -p nova`，style=base-nova），16 个 Base UI 底座组件落 `components/ui/`；`cn` 独立包（lib/utils.ts 仅 re-export） |
| 布局重构 | **app-shell 化**（任务书外的必要调整）：全局顶栏（品牌 + 三视图 pill 导航 + 主题切换）挂 layout，聊天页去自有大头部、身份选择并入 composer、语料量挪 footer；三页 h-full 填充 |
| 聊天主页 | 青色用户气泡 / assistant 卡片（Badge 路由徽章 + Collapsible 研究过程 + 槽位卡 + ConfirmCard + ReceiptAlert + 引用 Badge+Tooltip + DoneMeta）；回答走 react-markdown + typography prose |
| 共享积木 | `components/message-parts.tsx`（7 个纯展示件）+ `components/answer.tsx`，聊天页与 compare TrackPanel 复用 |
| compare | TrackPanel 换 Card + 时间线 lucide 图标（工具调用 → Wrench Badge chip）；差异表换 Table |
| console | 四面板换 Card；台账/语料换 Table；预算条换 Progress；演示重置换 AlertDialog；检索调试 Input+Select+Button |
| 主题 | next-themes class 策略 + ThemeToggle（挂载后再渲染图标防闪）；品牌主色 token 定为青（oklch 0.52/0.09 220，亮暗两套） |
| 清理 | legacy.css（原 911 行）过渡完成后删除，globals.css 只剩 shadcn tokens + typography 插件（133 行） |

### 7.2 偏离与补丁（真跑中发现）

| 问题 | 处置 |
|---|---|
| **Node 25 localStorage 残废 stub**：Next 15.3 dev overlay 的 `typeof localStorage !== "undefined"` 探测误判（Node 25 全局有对象但 getItem 为 undefined），dev 下全部页面 500；`next build` 无 overlay 所以绿 | dev script 固化 `NODE_OPTIONS=--localstorage-file=/tmp/gewu-web-ls.json`（给 stub 一个真后端）；生产不受影响 |
| **CSS 变量重名**：legacy 的 `--card/--border/--muted/--accent/--radius` 与 shadcn `:root` 相撞（后者覆盖导致 muted 文字变背景色不可见） | legacy.css 全量加 `--legacy-` 前缀（62 处）过渡期隔离；收尾随文件一起删除 |
| **Base UI Select.Value 显示原始值**（`student`/`auto` 而非中文标签） | 各使用点给 SelectValue 传显式 label children（本地 OPTIONS 数组查找） |
| **AlertDialogAction 无关闭语义**（本封装只是 Button；Close 语义在 Cancel） | 确认清空用 `AlertDialogCancel variant="destructive" onClick={reset}`（关闭 + 执行） |
| preflight 打击旧样式 | 未触发：legacy CSS 无 @layer（unlayered 优先于所有 layer），两任务书预案（只引 theme+utilities）实际不需要 |
| `next build` 与运行中的 dev server 共用 `.next` 打架（Cannot find module './897.js'） | build 前先停 dev；已恢复 |
| init 对无 Tailwind 入口的既有项目报「No configuration found」 | 先手写 `@import "tailwindcss"` 最小 globals.css 再跑 init |

### 7.3 真跑验收（对照 §5 清单）

| 清单项 | 结果 |
|---|---|
| 直答（引用 chips + Tooltip） | ✅ 转专业绩点题：路由徽章「直答」、markdown 加粗/列表渲染、3 条引用、11518 ms |
| 深研（Collapsible trace） | ✅ compare A 轨：3 个子问题时间线 + 来源、综合答案（同 ResearchTrace 组件） |
| 办理流（槽位→确认卡→回执） | ✅ 羽毛球馆预约：槽位齐 → ConfirmCard（dl 四槽位 + book_venue）→ 确认 → ReceiptAlert（**凭证号 VE-0235 真实落库**，console 台账可见） |
| ReAct 模式 | ✅ compare B 轨（4 事件，自主检索）；工具 chip 分支（TOOL_RE→Wrench Badge）经类型检查，本轮未命中展示 |
| compare 同题双发 + 差异表 | ✅ A 411 事件/25687 ms vs B 4 事件/22524 ms，路由/事件数/耗时/引用数四行差异表齐全 |
| console 四面板 | ✅ 健康（LLM/向量/预算 Progress 2.0%）/ 台账（含 VE-0235）/ 检索调试真跑（转专业绩点要求 → 混合检索命中卡片）/ 语料 15 篇表格 |
| 亮/暗双主题 | ✅ 三页各双主题截图走查（顶栏按钮真实切换 + localStorage 持久化；暗色主色自动切亮青） |
| 降级路径 | ⚠️ 未真跑（需要停后端模拟）：无 health → destructive Alert「连不上后端」、无 LLM key → info Alert，渲染路径与已验证的错误 Alert 同组件同 variant |
| 门禁 | ✅ `npm run build` 绿（/ 6.47kB、/compare 7.43kB、/console 9.85kB）；`tsc --noEmit` 绿；**apps/server 与 lib/api.ts、lib/labels.ts 零改动**（server 侧 11 个未提交文件为 P15 会话遗留，非本任务产物） |

### 7.4 遗留

- compare B 轨工具 chip（Wrench Badge）展示分支待下次 ReAct 带工具调用的真跑顺带确认。
- 降级 banner（停后端 / 无 key）待用户下次演示前重启环境时顺带确认。
- sonner toast 未装（暂无场景）；framer-motion 微动效、移动端专项为后续可选加餐。

## 8. 动效加餐：reactbits（2026-10-01 同日第二段）

> **背景**：用户看过 reactbits.dev，诉求是「好看的动画、页面更高级」——shadcn 骨架
> 之外的动效层。reactbits 与 shadcn 同理念（源码 copy、按组件取用，MIT+Commons
> Clause），官方支持 shadcn CLI registry 直装，与 P16 底座无缝叠加。

**落地**
- `components.json` 挂 `@react-bits` registry → `npx shadcn add @react-bits/<Name>-TS-TW`，
  五个组件源码入库：Aurora（WebGL 极光，ogl）、BlurText（逐字模糊入场，motion）、
  SpotlightCard（鼠标聚光）、ClickSpark（点击火花 canvas）、CountUp（数字滚动 spring）。
  新增依赖：motion（framer-motion 继任）、ogl。
- 接入点（克制原则——空态 hero 一处浓墨、其余微交互）：
  - 聊天空态 hero：Aurora 铺底（青-靛 colorStops、lightMode 随主题、径向 mask 收边
    不干扰正文）+ BlurText 标题（CJK 无空格 → `animateBy="letters"` delay=55ms）+ logo 弹入；
  - 消息行 motion 入场（fade+y 250ms）；发送按钮 ClickSpark 白色火花；
  - console：四 Panel → SpotlightCard（聚光色 `color-mix(--primary 16%)` 亮暗自适应，
    容器类对齐 ui/card 使 CardHeader/Content 直接可用）+ 健康数字 CountUp。
- 源码改造一处：SpotlightCard 原版硬编码深色底（bg-neutral-900）→ 主题 token 化，
  文件头注释注明改动来源。

**真跑验收**：发送链路经 ClickSpark 包裹全通（图书馆借书直答 7126ms）；聚光效果
程序化验证（dispatch mousemove → opacity 0.6 + radial-gradient at 鼠标位）；亮/暗
双主题 Aurora 目检（暗色光晕柔和不过亮）；tsc + next build 绿。

**偏离与坑**
- compare 页动效（TrackPanel 入场等）**暂缓**：P17 会话正并发重构该页（agent-first
  双轨语义 + api.ts ChatMode 契约变更在途），避免同文件冲突；待 P17 收口后补。
- **并发会话 git 教训**：P17 会话已 stage 的 apps/server 删除（react.py /
  test_react.py，agent-first 重构删除物）被本会话「整 index 快照」式 commit 卷入，
  首笔提交混入两个 P17 删除文件——已 soft reset 剔除重提交并还原其 staged 状态。
  并发会话共用一个 index 时，提交应使用 `git commit -- <paths>`（pathspec 限定）
  或提交前核对 `git status` 第一列。

### 8.1 高级感补强（同日第三段，用户反馈「还是偏传统」后）

诊断：对话区一旦开始聊就是纯白/纯黑平面（Aurora 只活在空态）、纯色发送按钮无光晕、
气泡与 composer 是标准描边盒、等待态是 spinner+文字——「传统感」来自这些平铺直叙。
补强（全部主题 token 驱动，亮暗自适应）：
- **全程环境光**：聊天页 main 顶部常驻两个渐变光斑（primary/indigo blur-3xl）+
  微点阵纹理（radial-gradient 1px 网格 + mask 顶部衰减）；
- **玻璃质感**：assistant 气泡 bg-card/85 + backdrop-blur + ring；composer 玻璃卡
  （bg-card/80 + shadow-lg + focus 光环升级为 primary/10）；
- **渐变主色**：用户气泡 cyan→teal 渐变 + 投影；发送按钮渐变 + glow shadow；
- **ConfirmCard 渐变描边**（p-px 渐变底 + 内层卡）——待确认卡成为消息流最亮一块；
- **TypingDots**：等待首包改三弹跳圆点（motion 循环）替代 spinner+文字；
- console：Progress 渐变填充、CountUp 数字 text-primary。
真跑验证：奖学金直答全链路（12405ms）、亮/暗双主题截图目检（暗色为完全体）、
tsc+build 绿。构建注意：P17 会话并发跑 next build 会与本会话撞 `.next`
（ENOENT pages-manifest），错峰重跑即可。
