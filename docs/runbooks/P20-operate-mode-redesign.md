# P20 前端 Operate 化重构：内容衬线折中（任务书 + 执行记录）

> **背景**：P18 暖编辑风落地后用户仍不满意。克隆两份设计方法论参考仓库到
> `/Users/mrpwn/Project/mine/references/`（gewu 仓库外）：`Leonxlnx/taste-skill`
> （anti-slop skill 文集：三拨盘 / AI Tells / redesign 审计协议）与
> `pbakaus/impeccable`（24 命令 playbook + 61 条无 LLM 确定性检测规则 + Operate
> mode 深度指南）。用 impeccable 检测器对三页做基线扫描，**29 条发现**，其中
> `cream-palette`（米白画布 = 当前最泛滥的"AI 默认高级感"底色）与衬线标题两条
> 与 taste-skill 的禁用清单独立撞车——P18 的出版向语言与工具型产品 UI 的错位
> 被客观证实为"不满意"的根因候选。用户拍板 **C·内容衬线折中**：工具的骨架 +
> 文献的灵魂。本任务只动**呈现层与设计契约**：SSE 契约、状态机、`lib/api.ts`、
> 后端零改动。

## 0. 拍板结论（用户确认）

| # | 问题 | 拍板 | 依据 |
|---|------|------|------|
| Q1 | 设计方向 | **C·内容衬线折中**（备选 A·Operate 全反转 / B·保留暖编辑只修硬伤） | 双仓库+检测器独立收敛：米白/衬线=AI 默认高级感，但"政策条文引用"的文献感是产品语义资产，保留在真正有文献语义的时刻 |
| Q2 | mode 定位 | 聊天 / compare / console 三页全部 **Operate mode**（用户在任务中）；拨盘 baseline：VARIANCE 4 / MOTION 4 / DENSITY 5 | impeccable operate.md：Operate=可扫读、一致性、原生预期优先，"工具消失在任务里"；familiarity 是特性不是缺陷 |
| Q3 | 衬线使用域 | UI chrome（导航/按钮/标签/表格/页面 h1）全 sans；衬线只留**三个内容时刻**：空态大标语、政策条文/引用块、回答内文档标题。品牌方印/「格物」字标作为品牌资产保留衬线（第四处，记入 DESIGN.md） | operate.md「display 字体不上 UI 控件」；taste-skill「creative brief = serif 是最常被抓的 AI 痕迹」 |
| Q4 | 主色 | 陶土保留，亮色 `#bc5b3c` → **`#b5532f`**（白字对比 4.47 → **4.95:1**，同色相微加深）；暗色 `#d98a63` + on-primary `#2b1a10` = 6.17:1 已达标不动 | impeccable detect `low-contrast` 全站多处（WCAG AA 4.5:1 差一线）；按压色 `#9c4630` 6.30:1 不动 |
| Q5 | 画布 | 亮色米白 `#faf8f3` → **中性纸感**（候选 `#f7f7f5` / `#f6f6f4`，暖度大降但保留纸感非纯白）；暗色暖黑 `#191713` 微降暖度（候选 `#181816`），以复扫脱离 `cream-palette` 为准 | detect `cream-palette` 三页全中；taste-skill 禁用色板清单 #faf7f1 一族精确命中 #faf8f3 |
| Q6 | 字体 | 拉丁保留 Geist（operate.md 明文 permission："system fonts and familiar sans defaults"是产品 UI 的权利），检测器 `overused-font` 白名单化并记录理由；中文正文系统黑体栈不动；字阶改**固定 rem 紧比率**（1.125–1.2，operate.md：产品 UI 不用 fluid clamp） | detect `overused-font` geist 95%；operate.md typography 节 |
| Q7 | 门禁 | 新增 `make design-lint`：impeccable detect（dev server 起后三页 URL 扫）+ grep 级规则（衬线域/冷色/`h-screen`/em-dash）；剩余命中逐条在 DESIGN.md 白名单化+理由 | 与既有全绿门禁工作流咬合；craft-floor 机械检查固化 |

## 1. 目标 / 非目标

**目标**
- DESIGN.md v2：mode + 拨盘声明、token 契约机器可读化（参考 impeccable
  DESIGN.md 的 YAML frontmatter 格式）、衬线使用域四条、kit 消费规则
  （"先找 kit 再发明新 class"）。
- 基线 29 条清零或白名单化：contrast / nested-cards ×6 /
  clipped-overflow-container ×3 / skipped-heading / buried-raster /
  radial-spotlight-glow / cream-palette / overused-font。
- 衬线域收缩：`--font-display` 从"全 UI 标题"收缩为三个内容时刻 + 品牌资产。
- 特效再降权：噪点层（3.2% feTurbulence，检测器判"永远到不了屏幕"）撤除；
  console 环境光斑撤除——craft-floor「one authored moment」，页面动效收敛到
  流式光标 + 骨架屏 + TypingDots 三件状态语义件。
- 三页 chrome 工具化 pass（operate.md 清单）：交互件状态矩阵盘点
  （hover/focus/active/disabled/loading/error/empty）、数据列 `tabular-nums`、
  overlay 逃出裁剪容器、一致组件词汇。
- 真跑验收：亮/暗双主题 × 三页截图走查 + 真实问答一轮 + detect 复扫。

**非目标**
- 不动 `lib/api.ts` SSE 契约、各页事件 switch、会话隔离状态机、后端。
- 不换组件库（shadcn/BaseUI 保留）；不做移动端专项；不新增页面/功能。
- 不做营销页（gewu 无 landing；taste-skill 的 landing 向规则仅参考不套用）。
- apps/server 有未提交改动（routing/chat/llm service/retrieve 四文件），
  本任务 commit **严格 pathspec 限定 apps/web 与 docs**。

## 2. 改动盘点

| 文件 | 动作 |
|---|---|
| `DESIGN.md` | v2 重写：mode/dials + token YAML 化 + 衬线域 + kit 规则 + 白名单区 |
| `app/globals.css` | 画布/primary 换值、字阶紧比率化、噪点层删除、`--font-display` 语义注释更新 |
| `app/layout.tsx` | 品牌印与全局 chrome 类名随衬线域调整 |
| `app/page.tsx` | 空态标语保留衬线；消息流/composer/导航 sans 化；nested-cards 拍平 |
| `components/answer.tsx` | 引用块/政策条文衬线化（内容时刻）；prose-headings 改 sans |
| `app/compare/page.tsx` | h1 sans 化、nested-cards ×2 拍平、tabular-nums |
| `app/console/page.tsx` | h1 sans 化、光斑撤除、h1→h3 跳级修复、overflow-hidden 裁剪 ×3 修、tabular-nums |
| `components/message-parts.tsx` | ConfirmCard/徽标/chip 类名随衬线域与对比度 |
| `Makefile`（或 make 入口） | 新增 `design-lint` 目标 |
| `DESIGN.md` 白名单区 | `overused-font(geist)` 等剩余项逐条+理由 |

## 3. 任务分解

1. **P20-1 契约 + token**：DESIGN.md v2 重写；globals.css 画布/primary/
   字阶换值；噪点层删除；`::selection` 随主色微调。
2. **P20-2 衬线域收缩**：layout/三页 h1/answer prose-headings/导航按钮标签
   sans 化；空态标语、引用块（citation 与政策条文渲染处）、回答内文档标题、
   品牌方印衬线保留。
3. **P20-3 基线清零 + chrome 工具化**：nested-cards ×6 拍平、console 裁剪
   容器 ×3 修复（Tooltip 逃逸：去 overflow-hidden 或 portal）、skipped-heading
   修复、console 光斑撤除、tabular-nums、状态矩阵盘点补缺。
4. **P20-4 门禁 + 验收留档**：`make design-lint`（detect 三页 + grep 规则）、
   tsc/build 绿、亮暗×三页截图、真跑一轮、detect 复扫对照基线、本文 §6
   执行记录。

## 4. 验收门禁

- `tsc --noEmit` + `next build` 绿。
- `make design-lint` 绿：impeccable detect 三页 0 primary findings，或每条
  剩余项在 DESIGN.md 白名单区有记录+理由（预期剩余：`overused-font` geist）。
- grep 门禁：衬线类名（`font-display`）仅出现在空态标语/引用块/回答内文档
  标题/品牌印四处；`h-screen` 零命中；em-dash（—）用户可见文案零命中。
- 亮/暗双主题 × 三页截图目检（chrome-devtools MCP）。
- 真跑一轮问答（注意 LLM key 5h 限额，避开 429 时段；错误态与成功态都要走）。
- §6 执行记录留档，commit 全部 pathspec 限定。

## 5. 参考资料

- `/Users/mrpwn/Project/mine/references/impeccable/`：`reference/operate.md`
  （Operate 深度）、`reference/craft-floor.md`（质量底线+禁令）、`DESIGN.md`
  （token 契约格式范本）、`docs/STYLE.md`（文案 denylist，可后续借鉴）。
- `/Users/mrpwn/Project/mine/references/taste-skill/`：`skills/redesign-skill/
  SKILL.md`（审计→诊断→修复 + Fix Priority）、`skills/taste-skill/SKILL.md`
  §9 AI Tells（本任务 grep 门禁的规则来源）。
- 基线报告（2026-10-01，detect @ localhost:3100）：chat 5 / compare 7 /
  console 17，明细见 §6 执行记录开头。

## 6. 执行记录

### 基线报告（2026-10-01 立项时采集，detect @ http://localhost:3100）

**chat（/）— 5 条**
- low-contrast ×2：white on `#bc5b3c` 4.47:1（route-badge / 发送钮）
- buried-raster ×1：噪点层 opacity 0.032
- nested-cards ×1
- cream-palette ×1：`rgb(250, 248, 243)` = #faf8f3

**compare（/compare）— 7 条**
- low-contrast ×3、buried-raster ×1、nested-cards ×2、cream-palette ×1

**console（/console）— 17 条**
- clipped-overflow-container ×3：`div.group/card...overflow-hidden` 裁剪定位子元素（Tooltip 逃逸）
- radial-spotlight-glow ×3：`#bc5b3c` 16% radial 光斑（pointer-events-none）
- low-contrast ×1、buried-raster ×1、cream-palette ×1、nested-cards ×3
- overused-font ×1：Geist 95%
- skipped-heading ×1：h1「演示控制台」→ h3「场馆预约（1）」缺 h2

**立项时已算定的对比度锚点**：white on `#b5532f` = 4.95 / `#9c4630` = 6.30 /
暗色 `#2b1a10` on `#d98a63` = 6.17（达标不动）/ ink on `#f7f7f5` = 16.19。

### P20-1 ~ P20-3 执行明细（2026-10-01）

- **DESIGN.md v2**：全量重写——frontmatter 加 `mode: operate` + `dials {variance:4, motion:4, density:5}`；
  衬线域四处收敛成清单（品牌印/空态标语/引用块/回答内文档标题）；新增 kit-规则（先找 kit 再发明、
  嵌套卡禁止）；门禁白名单区（geist / CJK 破折号 / 空态氛围光一处）。
- **globals.css**：色板全家族降暖（canvas #faf8f3→#f7f7f5，card/secondary/muted/accent/border 同步）；
  primary #bc5b3c→**#b5532f**（白字 4.47→4.95）；暗色 #191713→#181816；chart-4/5 暖调压灰。
  `--font-heading` 回归 `--font-sans`（CardTitle/AlertDialogTitle 退出衬线）。**噪点层 .grain-overlay 删除**。
- **layout.tsx**：摘噪点层挂载；品牌印/字标衬线保留（品牌资产位）。
- **衬线域收缩**：compare/console 页 h1 去 font-display；`font-heading` 指向 sans；
  answer.tsx prose-headings 衬线保留 + **prose-blockquote 衬线新增**（政策条文=内容时刻）。
- **nested-cards ×6 清零**（真根因与修法）：
  - `CardHeader/CardFooter` 的 `rounded-t/b-xl` 删除——圆角由 Card 的 overflow-hidden 裁剪承担，
    检测器把「带圆角的 header 套在带 ring 的 Card 里」判成卡中卡（compare ×2 / console ×3 全是它）；
  - chat 底部操作带 `border-t bg-background` → `border-t`（bg 与画布同色本就冗余；
    检测器 `\bborder\b` 正则把 border-t 算边框，与 composer 的 border+bg 凑成卡中卡 ×1）；
  - ConfirmCard 渐变描边双容器（p-px 外壳+内层 Card）→ 单层 Card + `ring-primary/30`；
  - TrackPanel 用户问题 `rounded-lg bg-muted` 小块 → 「问：」前缀 + 600 字重平铺；
  - console SearchBench 命中列表逐条卡片 → divide-y 发丝线列表。
- **SpotlightCard 退役**（组件删除）：radial-spotlight-glow（console ×3）与
  clipped-overflow-container（Tooltip 被 overflow-hidden 裁剪 ×3）两项基线同源，面板回归素 Card。
- **skipped-heading**：console 台账分组 h3 → p（页面大纲只剩 h1）。
- **disabled 态重做**：button.tsx `disabled:opacity-50` → `disabled:bg-muted disabled:text-muted-foreground`
  （4.70:1；opacity-50 像素对比 1.1:1 被检测器命中）。
- **空态说明行去延迟入场**：delay 1.2s 淡入会被检测器采样成低对比（opacity stack 1.1:1）；
  operate.md「产品页不排加载表演」顺带落实，入场时刻只留 BlurText 标语。
- **tabular-nums**：compare 差异表（事件数/耗时/引用数）、console 请假天数列。

### P20-4 门禁与验收（2026-10-01）

**`make design-lint` 新增并全绿**：scripts/design-lint.sh + Makefile 目标。
detect 三页 PASS + grep 三规则（衬线域白名单/h-screen 零/冷色零）PASS。
- 脚本要点：**必须扫生产构建**（dev server 会间歇性吐未编译完的空 Tailwind CSS，URL 扫描
  不稳定——实测复现）；`.impeccable/config.json` 白名单要从 apps/web cwd 读；
  本机 7897 代理下 curl 须 `--noproxy`。
- 检测器行为注记：引擎 4.x 对零 finding 页面**静默退出 0**（不打印），脚本按退出码报 PASS/FAIL；
  `npx` 拉的是 npm 4.1.0 shim（引擎 4.0.0 sidecar），规则与 4.4.0 源码一致。

**detect 复扫 vs 基线（29 → 0）**：cream-palette/low-contrast/buried-raster/radial-spotlight-glow/
nested-cards/clipped-overflow-container/overused-font(白名单)/skipped-heading/body-text-viewport-edge
全部清零或白名单化。

**tsc --noEmit + next build 绿**；git pathspec 限定 apps/web + docs + DESIGN.md +
Makefile + scripts（apps/server 四文件未提交改动未触碰）。

**亮暗双主题 × 三页截图目检（1280×800，生产构建）**：六张全过——
chat 亮（纸感画布/衬线空态/muted 实底 disabled 钮）、chat 暗（近黑 #181816）、
compare 双主题（h1 sans、素 Card 面板）、console 双主题（无光斑、divide-y 台账）。

**真跑三态（生产构建 + make run 后端）**：
- 直答成功态：转专业绩点题 → 直答徽章 + 全文 + 3 引用 + 20486ms（**顺带闭掉 P18 遗留的
  成功态真跑缺口**——当时被 LLM 429 拦截，P19 列的定时补验未执行，本次实测通过）；
- 办理全链：预约羽毛球馆 → 槽位收集 → ConfirmCard（拍平后单层卡）→ 确认 →
  回执「已预约 羽毛球馆 2026-10-02 19:00-21:00（凭证号 VE-0271）」→ console 台账可见落库；
- 错误态：杀 API 发问 → 「出错了：Failed to fetch」destructive alert 正常渲染。

**执行中发现并当场处理的坑**：
1. macOS bash 3.2 对 `$PORT` 后紧跟全角字符会解析成带坏字节的变量名（`PORT\xff`）——
   中英文混排 shell 脚本里变量一律 `${PORT}` 花括号 + 收尾标点前置；
2. dev server 与 next start 并存会互写 .next（vendor-chunks MODULE_NOT_FOUND / 静态路由 500），
   构建前必须杀光 next 进程；rm .next 会连带删字体缓存，重 build 需走代理拉 Google Fonts；
3. 检测器 `\bborder\b` 会把 border-t/border-b 全算边框（card-like 判定），设计侧对应纪律：
   分隔线容器不要同时带 bg 类（已写入 DESIGN.md console-panel/chat 操作带的做法）。

（基线明细以上，执行完）
