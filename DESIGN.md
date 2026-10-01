---
version: P25
name: Gewu-design-system
description: >
  格物 Gewu 的界面设计系统：机构蓝官方风（对标 america.gov/chat——同构产品，
  官方信息 AI 问答助手）× Operate 工具型纪律。P25 依据用户拍板换肤：情绪层
  从 P20 的「陶土暖纸」切换为「机构蓝冷静纸」——navy 主色 + 冷调近白画布 +
  link 蓝行内链接（官方、可信、冷静，与「官方制度问答」的产品语义同构）；
  P20 的 craft 地板全部继承（可扫读、一致性、原生预期优先、无嵌套卡、
  熟悉感是特性、「工具消失在任务里」）。拨盘 baseline：VARIANCE 4 /
  MOTION 4 / DENSITY 5。

mode: operate
dials: { variance: 4, motion: 4, density: 5 }

colors:
  primary: "#1a3a5c"            # 机构深蓝（白字对比 11.6 ≥ AAA，勿改浅）
  primary-strong: "#142d46"     # 按压/激活
  on-primary: "#ffffff"
  link: "#1157d0"               # 行内链接蓝（对画布 6.2 ≥ AA）——独立于 primary
                               # 的 america.gov 签名：回答内链接/来源条目
  ink: "#1c2430"                # 冷墨（标题/正文强）
  body: "#2e3746"               # 正文
  muted: "#5a6572"              # 次级文本
  muted-soft: "#7c8590"         # 说明/脚注
  hairline: "#d8dde8"           # 1px 冷调边线
  canvas: "#fafbfd"             # 画布（冷调近白，非纯白非暖纸）
  surface-card: "#f2f4f8"       # 卡片（比画布深一档）
  surface-strong: "#e8ebf1"     # 更强一档（选中 tab / 强调带）
  surface-user: "#1a3a5c"       # 用户消息 = primary 实底（P25 起，america.gov 同款）
  accent-amber: "#c08a2d"       # 警示/高亮小面积（冷化琥珀）
  success: "#2e7d4f"            # 回执成功（冷调绿）
  error: "#c03434"

  dark:
    canvas: "#10161f"           # 近黑海军蓝画布
    surface-card: "#17202b"
    surface-strong: "#1f2a37"
    hairline: "oklch(1 0 0 / 9%)"
    on-canvas: "#e8ecf2"
    on-canvas-muted: "#98a3b0"
    primary: "#8ab0dd"          # 暗色下的提亮冰蓝（on-primary #0c1826 对比 7.6 达标）
    on-primary: "#0c1826"
    link: "#8ab0dd"             # 暗色下链接与提亮 primary 同值（复用对比结论）

typography:
  衬线域（font-display / Noto Serif SC，全站仅此四处）:
  - 品牌方印「格」与「格物」字标（layout.tsx，品牌资产）
  - 空态大标语（page.tsx + BlurText）——对应 america.gov "Hello, America" 衬线 hero
  - 政策条文/引用块（answer.tsx prose-blockquote）
  - 回答内文档标题（answer.tsx prose-headings）
  chrome（全 sans）: 页面 h1、导航、按钮、标签、CardTitle、表格、消息正文
  body:
    fontFamily: "Geist + PingFang SC / HarmonyOS Sans / Microsoft YaHei, sans-serif"  # --font-sans
  mono:
    fontFamily: "Geist Mono, ui-monospace"  # --font-mono
    usage: 单号/凭证号/doc_id/延迟 ms/版本号
  scale: # 固定 rem 紧比率（operate.md：产品 UI 不用 fluid clamp），比率 1.14–1.2
    title: 16px / 1.4 / 500-600
    body: 14px / 1.6
    caption: 12px / 1.4
    meta: 11px / 1.4（页脚、DoneMeta）
    display-lg: 30px / 1.25（仅空态标语，衬线，600）
  data: 数字列一律 tabular-nums（表格计数/耗时/百分比），单号走 mono
  links: 回答内行内链接 = link 蓝 + underline underline-offset-2（america.gov
    签名；不用 primary、不用默认无下划线）

rounded:
  sm: 6px      # chip / 徽章内
  md: 10px     # 按钮 / 输入
  lg: 16px     # 浮层契约档（Dialog/composer；实现=--radius 0.75rem 阶梯的 xl 档 ≈16.8px）
  pill: 9999px # 建议 chips / 徽章

spacing:
  base: 4px
  rhythm: 消息间 20px；区块间 24px；页面最大宽度对话 768px / 台面 1152px

kit-规则:
  先找 kit 再发明：任何新 UI 先复用本契约与 ui/ 组件（Card/Button/Badge/Alert/
  Table/Tooltip/Dialog…），确有缺口才新建模式并回写本文件。禁止在页面里发明
  「第二种卡片/第二种按钮/第二种 pill」。
  嵌套卡禁止：卡片内不再套卡片。分组用间距、divide-y 发丝线与字重，
  不用小卡容器（craft-floor：cards are the lazy container）。

components:
  top-nav:
    高 52px，canvas 底 + hairline 下边线；左侧品牌（衬线「格」方印 + 衬线「格物」字标），
    中部三视图 pill 导航（active = surface-strong 底 + ink 字），右侧主题切换。
    导航项带 hover 与 aria-current，focus 走全局 outline-ring。
  brand-mark:
    衬线「格」字方印：primary 底 + on-primary 字 + rounded-md + 微阴影。
  empty-state:
    65vh 居中：方印 logo → 衬线大标语（display，BlurText 逐字入场）→ muted
    说明行 → 信任行（P25：回答仅基于钱塘大学官方制度文档、引用可溯到发文
    部门）→ 无卡片。顶部一层极淡冷光（primary 光斑 + 微点阵，token 化后自动
    呈 navy）——全站唯一的氛围光，仅此一处。
  assistant-message:
    **无气泡无卡片**：左列 7px 方形小徽标（primary/10 底 primary 字，Sparkles）+
    右侧内容直接铺在 canvas 上。RouteBadge / ResearchTrace / Answer / 引用行 /
    MessageActions 纵向排布，元素间距 10px。生成中用 TypingDots + shimmer
    骨架 + 文末闪烁光标（.streaming-caret）。行内链接走 link 蓝。
  user-message:
    **primary 实底气泡（P25 起，america.gov 同款）**：右对齐 bg-primary +
    on-primary 字 + rounded-2xl(右下 sm) + 无描边无渐变无阴影。
  message-actions:
    每条 assistant 消息底部（done 后出现）的安静尾件：caption 尺寸、无底色、
    上方 8px 留白（不画线不套卡）。「来源 N」（outline 小钮，预览前两个部门
    名）/ 👍 / 👎（ghost icon 按钮，已选态 primary 字 + disabled）/ 复制。
    icon 按钮全部带 aria-label；历史恢复的静态消息仅出「复制」。
  sources-dialog:
    居中 Dialog（radius-lg + shadow-md + backdrop-blur 遮罩，Esc/点遮罩关闭）：
    按 source（发文部门）分组——组头 = 部门徽标位（圆形 surface-card 底 +
    lucide Landmark 图标，对应 america.gov 的机构徽章位）+ 部门名 600 +
    n 来源徽章，Collapsible 展开；组内条目 = [n] 文档标题（link 蓝下划线观感）
    + doc_id caption mono 平铺。Tooltip 不进 Dialog（doc_id 直接平铺）。
  follow-up-chip:
    回答下方追问 pill（P25）：suggestion-chip 同款形态 + ChevronRight 前缀
    （与空态建议区分）；点击即发送，发送后本组置灰；事件晚到时 fade+y 6px 入场。
  suggestion-chip:
    pill + hairline 边 + muted 字，hover 时 surface-strong 底 + ink 字；disabled 态必备。
  route-badge:
    research/hybrid = primary 实底 pill（全站最稀缺的 navy 时刻之一）；其余 = surface-card 底 pill。
  research-trace:
    hairline 左竖线 + 圆点时间轴，子问题 600 加重、来源行 caption 灰。折叠态 caption。
  confirm-card:
    消息流中唯一的高亮块：单层 Card + primary 调 ring/border；dl 键值表 +
    实心确认钮 / outline 取消钮。
  receipt-alert:
    成功 = success 图标 + ink 文本 + hairline 边 Alert（凭证号 mono）；失败 = error 变体。
  citation-chip:
    surface-card pill + BookMarked 前缀，Tooltip 悬浮 mono doc_id（Tooltip 走 portal，
    不被卡片 overflow-hidden 裁剪）。
  done-meta:
    mono 延迟 + outline 状态徽章（warn 用 accent-amber）。
  page-h1:
    sans 500-600，说明行 muted。标题上方留白 ≥ 下方。
  console-panel:
    素 Card：面板即容器，面板内列表/表格用 divide-y 与 hairline 分组，
    不再套卡；健康点 success 色；预算条实心 primary。
  track-panel:
    compare 双轨卡片；轨内用户问题平铺（「问：」前缀 + 600 字重）；时间线
    tool chip = surface-card pill。

motion:
  入场：fade + y 6-10px，250-300ms easeOut（消息行、卡片）；产品界面不排
  页面级入场序列（operate.md：用户要的是进任务，不是看加载表演）。
  流式：光标 1s steps 闪烁；首包前 shimmer 1.5s 扫光；TypingDots 0.9s 弹跳。
  品牌：BlurText 逐字（CJK letters 模式，delay 55ms）只在空态标语用一次。
  禁用：WebGL 极光、边框流光、点击火花、鼠标跟随聚光、全页噪点纹理。
  状态语义件仅三件：流式光标 / shimmer 骨架 / TypingDots。

do:
- 画布是冷调近白（dark 下海军蓝近黑）；暖陶土/cream/米白不回退（P18/P20
  两轮已退役，P25 第三次换肤也不走回头路）。
- 衬线只出现在衬线域四处；UI chrome 一律 sans——分层不可混。
- primary（navy）使用位封顶六处：品牌印、用户消息实底、路由徽章、确认卡
  描边、发送按钮、focus 环（+赞踩已选态字色）。不再多。
- 行内链接一律 link 蓝，不借 primary。
- 深度靠 surface 台阶与 hairline；阴影只允许两档：shadow-sm（静态卡）与
  shadow-md（浮层：composer focus / Dialog / 抽屉）。
- 语义色（success 冷绿/警示琥珀/错误红）独立于 primary，回执/健康点用语义色。
- 交互件七态齐全（default/hover/focus/active/disabled/loading/error/empty），
  Tooltip/Popover 必须逃出 overflow-hidden 祖先（portal 或去裁剪）。

dont:
- 不用暖陶土/cream/米白画布，不用冷青 cyan/teal/indigo 渐变与紫卡（默认脸）。
- 不给 assistant 回答套卡片/玻璃/流光——回答直接铺在画布上。
- 不引入第四种表面色；卡片里不再套卡片。
- 衬线不上界面标签/按钮/页面标题；衬线标题不加粗超过 600。
- 不加装饰性光斑（空态一处除外）、噪点、渐变文字、彩色左边条（>1px）。

门禁白名单（design-lint 剩余项逐条+理由）:
- overused-font(Geist): 产品 UI 明文有权用熟悉的 sans 默认（operate.md
  "System fonts and familiar sans defaults"）；中文正文落系统黑体，Geist 只盖拉丁。
- em-dash 门禁对 CJK 文案不适用：中文破折号「——」是规范标点，非英文 AI tell。
- 空态氛围光（chat 页顶部 primary 光斑 + 点阵）：空态是全站唯一编辑式时刻，
  token 化后自动呈 navy，检测器未命中，作为 deliberate choice 保留于此一处，
  不得扩散到 console/compare/memory/admin。

换肤迁移注记（P25-0 执行口径，token 值对照本文件 colors）:
- token 层 = globals.css `:root`/`.dark` 值替换（shadcn 变量名与 Tailwind
  类零改）；新增 `--link`/`--color-link` 一枚（answer prose 链接用）。
- 组件契约改动仅两处：user-message `bg-secondary` → `bg-primary`（page.tsx
  类名一行）；answer.tsx prose 链接色挂 link token。
- compare/console/memory/admin/login 随 token 自动换肤，逐页目检亮暗双主题
  + design-lint 六页零 finding（design-lint 无色值断言，已核实零风险）。
- P20 的 craft 地板条款（无嵌套卡/熟悉感/density/七态）原样继承，本文件
  只换情绪层——工程纪律不随皮肤漂移。
