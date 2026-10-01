---
version: P20
name: Gewu-design-system
description: >
  格物 Gewu 的界面设计系统：Operate 模式的工具型产品 UI（neutral paper canvas）。
  P20 依据 impeccable operate.md + craft-floor.md 重定契约：三页（对话/对比实验台/
  演示控制台）全部是「用户在任务中」的 Operate 界面——可扫读、一致性、原生预期优先，
  「工具消失在任务里」。米白 cream 画布与全 UI 衬线标题随 P18 退役（检测器判定
  cream-palette = 当前最泛滥的 AI 默认高级感）；陶土珊瑚主色与「文献感」保留在
  真正有文献语义的内容时刻。拨盘 baseline：VARIANCE 4 / MOTION 4 / DENSITY 5。
  反对立场：不用 cream/beige 画布、不做气泡套卡片、不堆特效与装饰光斑、
  衬线不上 UI 控件——熟悉感（familiarity）在产品 UI 里是特性不是缺陷。

mode: operate
dials: { variance: 4, motion: 4, density: 5 }

colors:
  primary: "#b5532f"            # 陶土珊瑚（P20 加深：白字对比 4.47→4.95 ≥ AA，勿改浅）
  primary-strong: "#9c4630"     # 按压/激活（白字 6.30）
  on-primary: "#ffffff"
  ink: "#1c1a17"                # 暖墨（标题/正文强）
  body: "#3d3933"               # 正文
  muted: "#6b6862"              # 次级文本
  muted-soft: "#8d8a83"         # 说明/脚注
  hairline: "#e3e2dd"           # 1px 边线
  canvas: "#f7f7f5"             # 画布（中性纸感，微暖非纯白非 cream）
  surface-card: "#f1f1ee"       # 卡片（比画布深一档）
  surface-strong: "#e8e7e2"     # 更强一档（选中 tab / 强调带）
  surface-user: "#edece8"       # 用户消息色块
  accent-amber: "#d9a441"       # 警示/高亮小面积
  success: "#4e8a5a"            # 回执成功（暖调绿）
  error: "#bf4545"

  dark:
    canvas: "#181816"           # 近黑画布（P20 微降暖）
    surface-card: "#21201d"
    surface-strong: "#2e2c26"
    hairline: "oklch(1 0 0 / 9%)"
    on-canvas: "#f2f0e9"
    on-canvas-muted: "#a3a096"
    primary: "#d98a63"          # 暗色下的提亮陶土（on-primary #2b1a10 对比 6.17 达标）
    on-primary: "#2b1a10"

typography:
  衬线域（font-display / Noto Serif SC，全站仅此四处）:
  - 品牌方印「格」与「格物」字标（layout.tsx，品牌资产）
  - 空态大标语（page.tsx + BlurText）
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

rounded:
  sm: 6px      # chip / 徽章内
  md: 10px     # 按钮 / 输入
  lg: 14px     # 卡片 / composer
  pill: 9999px # 建议 chips / 徽章

spacing:
  base: 4px
  rhythm: 消息间 20px；区块间 24px；页面最大宽度对话 768px / 台面 1152px

kit-规则:
  先找 kit 再发明：任何新 UI 先复用本契约与 ui/ 组件（Card/Button/Badge/Alert/
  Table/Tooltip…），确有缺口才新建模式并回写本文件。禁止在页面里发明
  「第二种卡片/第二种按钮/第二种 pill」。
  嵌套卡禁止：卡片内不再套卡片。分组用间距、divide-y 发丝线与字重，
  不用小卡容器（craft-floor：cards are the lazy container）。

components:
  top-nav:
    高 52px，canvas 底 + hairline 下边线；左侧品牌（衬线「格」方印 + 衬线「格物」字标），
    中部三视图 pill 导航（active = surface-strong 底 + ink 字），右侧主题切换。
    导航项带 hover 与 aria-current，focus 走全局 outline-ring。
  brand-mark:
    衬线「格」字方印：primary 底 + on-primary 字 + rounded-md + 微阴影。品牌唯一固定用 primary 的地方。
  empty-state:
    65vh 居中：方印 logo → 衬线大标语（display，BlurText 逐字入场）→ muted 说明行 →
    无卡片。顶部一层极淡暖光（primary 光斑 + 微点阵）——全站唯一的氛围光，仅此一处。
  assistant-message:
    **无气泡无卡片**：左列 7px 方形小徽标（primary/10 底 primary 字，Sparkles）+
    右侧内容直接铺在 canvas 上。RouteBadge / ResearchTrace / Answer / 引用行纵向排布，
    元素间距 10px。生成中用 TypingDots + shimmer 骨架 + 文末闪烁光标（.streaming-caret）。
  user-message:
    右对齐安静色块：surface-user 底 + ink 字 + rounded-2xl(右下 sm) + 无描边无阴影无渐变。
  composer:
    rounded-2xl + hairline 边 + surface-card 底 + 柔和投影；focus 时 primary 细边 + 4% 光环；
    内含身份/模式 Select（ghost 化）+ textarea + 实心 primary 发送按钮（无渐变无火花）。
  suggestion-chip:
    pill + hairline 边 + muted 字，hover 时 surface-strong 底 + ink 字；disabled 态必备。
  route-badge:
    research/hybrid = primary 实底 pill（全站最稀缺的 coral 时刻）；其余 = surface-card 底 pill。
  research-trace:
    hairline 左竖线 + 圆点时间轴，子问题 600 加重、来源行 caption 灰。折叠态 caption。
  confirm-card:
    消息流中唯一的高亮块：单层 Card + primary 调 ring/border（P20 拍平渐变描边套卡，
    消 nested-cards）；dl 键值表 + 实心确认钮 / outline 取消钮。
  receipt-alert:
    成功 = success 图标 + ink 文本 + hairline 边 Alert（凭证号 mono）；失败 = error 变体。
  citation-chip:
    surface-card pill + BookMarked 前缀，Tooltip 悬浮 mono doc_id（Tooltip 走 portal，
    不被卡片 overflow-hidden 裁剪）。
  done-meta:
    mono 延迟 + outline 状态徽章（warn 用 accent-amber）。
  page-h1:
    sans 500-600（P20 起退出衬线域），说明行 muted。标题上方留白 ≥ 下方。
  console-panel:
    素 Card（SpotlightCard 已退役）：面板即容器，面板内列表/表格用 divide-y 与
    hairline 分组，不再套卡；健康点 success 色；预算条实心 primary。
  track-panel:
    compare 双轨卡片；轨内用户问题平铺（「问：」前缀 + 600 字重，P20 起不用
    bg-muted 小块容器）；时间线 tool chip = surface-card pill。

motion:
  入场：fade + y 6-10px，250-300ms easeOut（消息行、卡片）；产品界面不排
  页面级入场序列（operate.md：用户要的是进任务，不是看加载表演）。
  流式：光标 1s steps 闪烁；首包前 shimmer 1.5s 扫光；TypingDots 0.9s 弹跳。
  品牌：BlurText 逐字（CJK letters 模式，delay 55ms）只在空态标语用一次。
  禁用：WebGL 极光、边框流光、点击火花、鼠标跟随聚光（SpotlightCard 已退役）、
  全页噪点纹理（buried-raster：3.2% 不透明度到不了屏幕，已撤）。
  状态语义件仅三件：流式光标 / shimmer 骨架 / TypingDots。

do:
- 画布是中性纸感（dark 下近黑），cream/beige（#faf8f3 系）与纯白都不回退。
- 衬线只出现在衬线域四处；UI chrome 一律 sans——分层不可混。
- primary 稀缺使用：品牌印、路由徽章、确认卡描边、发送按钮、focus 环。不再多。
- 深度靠 surface 台阶与 hairline，不靠阴影与描边叠加；阴影最多 shadow-sm。
- 语义色（成功绿/警示琥珀/错误红）独立于 primary，回执/健康点用语义色。
- 交互件七态齐全（default/hover/focus/active/disabled/loading/error/empty），
  Tooltip/Popover 必须逃出 overflow-hidden 祖先（portal 或去裁剪）。

dont:
- 不用 cream/beige 画布、不用冷青 cyan/teal/indigo 渐变（两头都是「默认脸」）。
- 不给 assistant 回答套卡片/玻璃/流光——回答直接铺在画布上。
- 不引入第四种表面色（紫卡、绿带）；卡片里不再套卡片。
- 衬线不上界面标签/按钮/页面标题；衬线标题不加粗超过 600。
- 不加装饰性光斑、噪点、渐变文字、彩色左边条（>1px）——craft-floor Refuse 清单。

门禁白名单（design-lint 剩余项逐条+理由）:
- overused-font(Geist): 产品 UI 明文有权用熟悉的 sans 默认（operate.md
  "System fonts and familiar sans defaults"）；中文正文落系统黑体，Geist 只盖拉丁。
- em-dash 门禁对 CJK 文案不适用：中文破折号「——」是规范标点，非英文 AI tell。
- 空态氛围光（chat 页顶部 primary 光斑 + 点阵）：空态是全站唯一编辑式时刻，
  检测器未命中，作为 deliberate choice 保留于此一处，不得扩散到 console/compare。
