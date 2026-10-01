---
version: alpha
name: Gewu-design-system
description: >
  格物 Gewu 的界面设计系统：暖米白画布上的编辑式排版（editorial warm canvas）。
  逆向自 Anthropic Claude 的设计语言（源头分析见 VoltAgent/awesome-design-md 的
  design-md/claude/DESIGN.md），按「校园制度问答 + 业务办理」的产品气质本地化：
  米白纸感画布 + 思源宋体衬线标题 + 陶土珊瑚主色 + 暖黑暗色模式。
  反对立场：不用纯白画布、不用冷青/靛渐变、不做气泡套卡片的微信式对话流、
  不堆特效——高级感来自字阶、留白、hairline 纪律与克制的暖色。

colors:
  primary: "#bc5b3c"            # 陶土珊瑚（陶土朱，比 Claude 珊瑚略深，米白底上对比更好）
  primary-strong: "#9c4630"     # 按压/激活
  on-primary: "#ffffff"
  ink: "#1c1a17"                # 暖墨（标题/正文强）
  body: "#3d3933"               # 正文
  muted: "#6e675c"              # 次级文本
  muted-soft: "#8e877a"         # 说明/脚注
  hairline: "#e6dfd2"           # 1px 边线（米白上的台阶感，不是墨线）
  hairline-soft: "#eee8db"
  canvas: "#faf8f3"             # 画布（暖米白，品牌识别的第一来源）
  surface-card: "#f3efe6"       # 卡片（比画布深一档）
  surface-strong: "#ece6d8"     # 更强一档（选中 tab / 强调带）
  surface-user: "#f0e9dc"       # 用户消息色块
  accent-amber: "#d9a441"       # 警示/高亮小面积
  success: "#4e8a5a"            # 回执成功（暖调绿）
  error: "#bf4545"

  dark:
    canvas: "#191713"           # 暖黑画布（非蓝黑）
    surface-card: "#221f19"
    surface-strong: "#2a251d"
    hairline: "#39332a"
    on-canvas: "#f2eee3"        # 暖白正文
    on-canvas-muted: "#a49d8f"
    primary: "#d98a63"          # 暗色下的提亮陶土
    on-primary: "#2b1a10"

typography:
  display:
    fontFamily: "Noto Serif SC, Source Han Serif SC, serif"   # --font-display（next/font 自托管）
    usage: 页面标题、空态标语、品牌字、回答内标题
    weight: 600（空态标语可 400）
    letterSpacing: -0.01em ~ -0.02em
  body:
    fontFamily: "Geist + PingFang SC / HarmonyOS Sans / Microsoft YaHei, sans-serif"  # --font-sans
    usage: 界面标签、消息正文、表格
  mono:
    fontFamily: "Geist Mono, ui-monospace"  # --font-mono
    usage: 单号/凭证号/doc_id/延迟 ms/版本号
  scale:
    display-lg: 30px / 1.25（空态标语、页面 h1）
    title: 16px / 1.4 / 600
    body: 14px / 1.6
    caption: 12px / 1.4
    meta: 11px / 1.4（页脚、DoneMeta）

rounded:
  sm: 6px      # chip / 徽章内
  md: 10px     # 按钮 / 输入
  lg: 14px     # 卡片 / composer
  pill: 9999px # 建议 chips / 徽章

spacing:
  base: 4px
  rhythm: 消息间 20px；区块间 24px；页面最大宽度对话 768px / 台面 1152px

components:
  top-nav:
    高 52px，canvas 底 + hairline 下边线；左侧品牌（衬线「格」方印 + 衬线「格物」字标），
    中部三视图 pill 导航（active = surface-strong 底 + ink 字），右侧主题切换。
  brand-mark:
    衬线「格」字方印：primary 底 + on-primary 字 + rounded-md + 微阴影。品牌唯一固定用 primary 的地方。
  empty-state:
    65vh 居中编辑式排版：方印 logo → 衬线大标语（display，BlurText 逐字入场）→
    muted 说明行 → 无卡片。顶部一层极淡暖光（primary 5-8% 光斑 + 微点阵），不是 WebGL 极光。
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
    pill + hairline 边 + muted 字，hover 时 surface-card 底 + ink 字。
  route-badge:
    research/hybrid = primary 实底 pill（全站最稀缺的 coral 时刻）；其余 = surface-card 底 pill。
  research-trace:
    hairline 左竖线 + 圆点时间轴，子问题 600 加重、来源行 caption 灰。折叠态 caption。
  confirm-card:
    消息流中唯一的高亮块：primary 50%→透明渐变描边（p-px 内层）+ surface-card 底，
    dl 键值表 + 实心确认钮 / outline 取消钮。
  receipt-alert:
    成功 = success 图标 + ink 文本 + hairline 边 Alert（凭证号 mono）；失败 = error 变体。
  citation-chip:
    surface-card pill + BookMarked 前缀，Tooltip 悬浮 mono doc_id。
  done-meta:
    meta 字号：mono 延迟 + outline 状态徽章（warn 用 accent-amber）。
  page-h1:
    对比实验台 / 演示控制台标题用 display 衬线，说明行 muted。
  console-panel:
    SpotlightCard + Card 结构，暖色 card 底自动生效；健康点 success 色；预算条实心 primary。
  track-panel:
    compare 双轨卡片，tag 方印沿用 brand-mark 规则；时间线 tool chip = surface-card pill。

motion:
  入场：fade + y 6-10px，250-300ms easeOut（消息行、卡片）。
  流式：光标 1s steps 闪烁；首包前 shimmer 1.5s 扫光；TypingDots 0.9s 弹跳。
  品牌：BlurText 逐字（CJK letters 模式，delay 55ms）只在空态标语用一次。
  纹理：全页 3.2% feTurbulence 胶片噪点（纸质感，常驻 z-60 不可交互）。
  禁用：WebGL 极光、边框流光、点击火花等「特效感」组件——与编辑式气质冲突。

do:
- 画布永远是暖米白（dark 下暖黑），纯白/纯灰 = 回到「默认脸」。
- 标题一律衬线 display，正文一律无衬线 body——双轨不可混。
- primary 稀缺使用：品牌印、路由徽章、确认卡描边、发送按钮、focus 环。不再多。
- 深度靠 surface 台阶与 hairline，不靠阴影与描边叠加；阴影最多 shadow-sm。
- 语义色（成功绿/警示琥珀/错误红）独立于 primary，回执/健康点用语义色。

dont:
- 不用冷青 cyan/teal/indigo 渐变（旧 P16 语汇，全部退役）。
- 不给 assistant 回答套卡片/玻璃/流光——回答直接铺在画布上。
- 不引入第四种表面色（紫卡、绿带）。
- 衬线标题不加粗超过 600，不用衬线排界面标签。
