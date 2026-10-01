# P18 前端设计语言升级：Claude 暖编辑风（任务书 + 执行记录）

> **背景**：P16/P16+ 完成组件库现代化与动效加餐后，观感仍偏「通用 AI demo」：
> 纯白画布 + 中性灰 token + 青蓝 primary + 青渐变气泡 + 特效叠加（Aurora/BorderBeam/
> ClickSpark）。经 VoltAgent/awesome-design-md 73 份 DESIGN.md 对照选型，用户拍板
> 采用 **Claude 暖编辑风**（米白画布 + 衬线标题 + 珊瑚主色 + 编辑式对话流），
> 本地化为仓库根 `DESIGN.md`（gewu 组件映射版）后执行。本任务只动**呈现层**：
> SSE 契约、状态机、`lib/api.ts`、后端零改动。

## 0. 拍板结论（用户确认）

| # | 问题 | 拍板 | 依据 |
|---|------|------|------|
| Q1 | 设计语言 | **Claude 暖编辑风**（备选 Linear 暗色奢华 / Minimax AI 原生浅色） | 产品灵魂=政策条文/引用/回执，「文献感」与内容契合；差异化最大；中文衬线标题是独有牌 |
| Q2 | 落地机制 | 逆向 DESIGN.md → 本地化 `DESIGN.md`（repo 根）→ 按它换血 | DESIGN.md 是 AI 可读设计系统契约，后续任何 UI 迭代都有据可依 |
| Q3 | 主色 | 陶土珊瑚 `#bc5b3c`（Claude 珊瑚略加深），暗色提亮 `#d98a63` | 保留暖编辑识别度，米白底上对比优于原版珊瑚 |
| Q4 | 范围 | 三页全换 + 字体双轨 + 去气泡化 + 特效重排 | 观感升级要整脸换，不做局部缝合 |

## 1. 目标 / 非目标

**目标**
- token 换血：`globals.css` 亮暗两套暖色板（米白画布 `#faf8f3` / 暖黑 `#191713`），
  全站无一处冷青。
- 字体双轨：`Noto Serif SC`（next/font 自托管）挂 `--font-display` 用于标题/品牌/
  空态标语/回答内标题；正文 Geist + 系统 CJK 黑体栈；`Geist Mono` 挂 `--font-mono`。
- 聊天页去气泡化：assistant 回答撤卡片直接铺画布；用户消息降为安静色块；
  composer 玻璃感改 hairline 卡。
- 特效重排：撤 Aurora（WebGL 冷极光）、BorderBeam、ClickSpark（含 `ogl` 依赖）；
  保留 BlurText（衬线标语）、噪点层、shimmer/光标/TypingDots。
- 三页适配：compare/console h1 衬线化、预算条实心主色、SpotlightCard 随 token 自动变暖。
- 真跑验收：亮/暗双主题 × 三页截图走查 + 后端起服后一轮真实问答链路截图。

**非目标**
- 不动 `lib/api.ts` SSE 契约、各页事件 switch、会话隔离状态机、后端。
- 不做移动端专项适配；不新增页面/组件功能。
- 不动 P17 会话（apps/server 有未提交改动，commit 严格 pathspec 限定）。

## 2. 改动盘点

| 文件 | 动作 |
|---|---|
| `DESIGN.md`（新） | 设计系统契约（token + 组件规格 + do/dont） |
| `app/globals.css` | token 换血 + selection 色；动效层（caret/shimmer/噪点）保留 |
| `app/layout.tsx` | 字体三件套（Serif SC/Geist/Geist Mono）+ 品牌印衬线化 |
| `app/page.tsx` | 空态编辑式化、去气泡、composer 重排、特效摘除 |
| `components/message-parts.tsx` | ConfirmCard/徽章随 token（微调类名） |
| `components/answer.tsx` | prose-headings 衬线 |
| `app/compare/page.tsx`、`app/console/page.tsx` | h1 衬线、预算条、composer 一致化 |
| 删 `components/Aurora.tsx`、`ClickSpark.tsx`、`ui/border-beam.tsx` | 特效退役 |
| `package.json` | 移除 `ogl`（Aurora 专属依赖） |

## 3. 任务分解

1. **P18-1 token + 字体**：globals.css 两套暖色板、layout 字体、@theme 挂
   `--font-display`；`::selection` 暖色。
2. **P18-2 聊天页**：空态（衬线标语 + 暖光斑）、消息流去气泡、composer、建议 chips。
3. **P18-3 三页适配 + 清扫**：compare/console/共享积木；删三个特效组件与 `ogl`。
4. **P18-4 验收留档**：build/tsc 绿、截图目检、真跑一轮、本文 §6 执行记录。

## 4. 验收门禁

- `npm run build` 零错误（含 tsc）。
- 亮/暗 × 聊天空态/对话态/compare/console 截图目检：无冷青残留、无气泡卡、
  衬线标题生效、暗色为暖黑。
- `grep -r "cyan\|teal-\|indigo\|22d3ee\|6366f1" apps/web/app apps/web/components`
  仅允许出现在「退役说明」注释里。
- 真跑：后端起服 + 一轮「转专业绩点」直答（引用来源正常渲染）。

## 5. 风险与对策

| 风险 | 对策 |
|---|---|
| Noto Serif SC 包体大（CJK 切片多） | next/font 只引 400/600 两档；自托管后离线稳定 |
| 珊瑚在米白上对比不足 | primary 取比 Claude 原版更深一档；按钮文字用白，正文强调用 ink |
| 去气泡后消息层级变平 | 层级交给 RouteBadge/trace/引用行与间距节奏；确认卡保留唯一高亮块 |
| 并发会话卷走 staged | commit 一律 pathspec 限定（apps/web、DESIGN.md、docs/runbooks/P18*） |

## 6. 执行记录（2026-10-01，一次会话完成）

**改动落地**（与 §2 盘点一致）：
- `DESIGN.md`（repo 根，新）：暖编辑风契约，token/组件规格/do-dont 全量，逆向自
  VoltAgent/awesome-design-md 的 claude/DESIGN.md。
- `globals.css`：亮暗两套暖色板（画布 `#faf8f3` / 暗色 `#191713`，primary 陶土
  `#bc5b3c`/暗色提亮 `#d98a63`），radius 锚点 0.625rem→0.75rem，`::selection` 珊瑚
  22%；字体三轨挂 `@theme inline`（--font-display/sans/mono）；动效层
  （caret/shimmer/噪点）原样保留。
- `layout.tsx`：Noto Serif SC（400/600）+ Geist + Geist Mono 三 next/font 自托管；
  品牌方印改 primary 实底 + 衬线「格」，字标衬线化。
- `page.tsx`：空态去 Aurora 换衬线大标语 + 暖光斑；assistant 回答去气泡卡
  （原玻璃卡+BorderBeam → 画布直铺）；用户消息青渐变气泡 → secondary 安静色块；
  composer 去玻璃化；发送钮去渐变去 ClickSpark。
- `answer.tsx`：prose-headings 衬线。`compare/console`：h1 衬线化、composer 对齐、
  预算条 cyan→teal 渐变改实心 primary。
- 删除 `Aurora.tsx`/`ClickSpark.tsx`/`ui/border-beam.tsx`，`ogl` 依赖移除。

**门禁**：`tsc --noEmit` 绿、`next build` 绿（6/6 静态页）；冷色 grep
（cyan/teal-/indigo/22d3ee/6366f1）零命中。

**真跑与目检**（dev :3100 + 后端 :8000 真数据）：
- 亮/暗 × 三页截图走查：空态衬线标语、暖黑暗色、珊瑚方印、console 检索调试
  （真实命中 top1=转专业管理办法）全部正常。
- LLM 429（key 5 小时限额，13:47 重置）拦住直答流——错误态视觉验证通过
  （用户色块 + 无气泡 assistant + destructive Alert + mono 延迟 + 状态徽章）；
  成功态（引用行/prose 表格/ConfirmCard/回执）用**临时预览页**
  （`app/p18preview/`，假数据渲染全部消息积木）亮暗双主题目检后删除，未入 commit。
- 环境坑：:3100 上的旧 dev server（改动前启动）增量编译损坏（layout.css 404
  全页裸 HTML），杀掉重启后恢复；:8000 API 为会话外已运行实例，未动。
- 遗留发现（非本任务范围）：compare 页 B 轨文案仍写「ReAct 自主组合工具/mode=react」，
  但实际运行 mode=classic（P17 改），文案与行为不一致，待后续任务修正。
