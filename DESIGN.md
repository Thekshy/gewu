---
version: P25
name: Gewu-design-system
description: >
  格物 Gewu 的界面设计系统：机构蓝官方风（对标 america.gov/chat——同构产品，
  官方信息 AI 问答助手）× Operate 工具型纪律。P25 依据用户拍板换肤：情绪层
  从 P20 的「陶土暖纸」切换为「机构蓝冷静纸」——navy 主色 + 冷调近白画布 +
  link 蓝行内链接（官方、可信、冷静，与「官方制度问答」的产品语义同构）；
  P20 的 craft 地板全部继承（可扫读、一致性、原生预期优先、无嵌套卡、
  熟悉感是特性、「工具消失在任务里」）。P34（10-02）拍板高级感转向
  「权威而灵敏」：拨盘 VARIANCE 6 / MOTION 5 / DENSITY 4——尺度断崖
  （display 48px + 负字距）、动效提速（120-180ms + expo-out）、
  hover 微光；参照与依据见文件末 referencing 节。

mode: operate
dials: { variance: 6, motion: 5, density: 4 }  # P34 拍板(10-02):高级感转向——variance 4→6/motion 4→5/density 5→4

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
  scale: # 固定 rem；P34 起层级二元化——display 断崖 + chrome 紧档（标杆比率 ≈4:1）
    title: 16px / 1.4 / 500-600
    body: 14px / 1.6
    caption: 12px / 1.4
    meta: 11px / 1.4（页脚、DoneMeta）
    display-lg: 48px / 1.15 / -0.01em（仅空态标语，衬线，600；移动 34px——
      CJK 警戒：汉字负字距不小于 -0.01em，大字号不用 400 字重）
    subhead: 24px / 1.3 / 500（品牌时刻副标题档——首答完成/回执时刻消费，
      P34-2 第二批；UI chrome 不用此档）
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
  rhythm: 消息间 20px（space-y-5，P34 校准对齐本契约）；区块间 24px；页面最大宽度对话 768px / 台面 1152px

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
    65vh 居中：方印 logo（P34 放大 size-20 / text-4xl，scale 0.85→1 入场）→
    衬线大标语（display-lg 48px / tracking -0.01em，BlurText 逐字入场）→
    muted 说明行 → 信任行（P25：回答仅基于钱塘大学官方制度文档、引用可
    溯到发文部门）→ 无卡片。顶部一层极淡冷光（primary 光斑 + 微点阵，
    token 化后自动呈 navy）——全站唯一的氛围光，仅此一处。
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
    首答（会话第一条回答）done 时整条 fade+y 8px 400ms expo-out 收束浮现
    （首答完成时刻，编辑式白名单之二），后续回答无入场动效。icon 按钮全部
    带 aria-label；历史恢复的静态消息仅出「复制」。
  composer:
    hairline 卡 + 150ms ease-out-expo：focus 时 border primary/60 + ring-4
    primary/10 + shadow-md 微光（「光从边框来」）；placeholder 轮换示例问题
    （P34-2，america.gov rotating questions 同构）：输入为空时 2.8s 轮换，
    有值即停。
  sources-dialog:
    居中 Dialog（radius-lg + shadow-md + backdrop-blur 遮罩，Esc/点遮罩关闭）：
    按 source（发文部门）分组——组头 = 部门徽标位（圆形 surface-card 底 +
    lucide Landmark 图标，对应 america.gov 的机构徽章位）+ 部门名 600 +
    n 来源徽章，Collapsible 展开；组内条目 = [n] 文档标题（link 蓝下划线观感）
    + doc_id caption mono 平铺。Tooltip 不进 Dialog（doc_id 直接平铺）。
  follow-up-chip:
    回答下方追问 pill（P25）：suggestion-chip 同款形态 + ChevronRight 前缀
    （与空态建议区分）；点击即发送，发送后本组置灰；事件晚到时 fade+y 6px
    入场，180ms expo-out；首答（会话第一条回答）逐 chip stagger 60ms——
    首答完成时刻的节奏件，后续回答不 stagger。
  task-card:
    空态任务预览卡（P34-2，america.gov task preview 同构；编辑式白名单
    形态之一）：图标块（size-8 primary/10 底 rounded-lg）+ 两行文案
    （title sm 500 + desc xs muted，文案=真实任务语义非营销词）；
    rounded-xl + border + bg-card，hover 边框亮化 primary/40 + shadow-sm
    （150ms ease-out-expo，「光从边框来」）。点击发送 q。对话后不出现——
    空态专属（suggestion-chip 由此退役，历史留档 P25）。
  route-badge:
    research/hybrid = primary 实底 pill（全站最稀缺的 navy 时刻之一）；其余 = surface-card 底 pill。
  research-trace:
    hairline 左竖线 + 圆点时间轴，子问题 600 加重、来源行 caption 灰。折叠态 caption。
  confirm-card:
    消息流中唯一的高亮块：单层 Card + primary 调 ring/border；dl 键值表 +
    实心确认钮 / outline 取消钮。
  receipt-alert:
    成功=办理完成时刻（P34-2 升格，编辑式白名单之三）：单层 Alert 内
    success 图标 spring 落定（stiffness 500/damping 30）+ 消息文案 +
    凭证号 mono 独立行（xs primary 色，数字政务「签收章」语义）；失败 =
    error 变体照旧（message + 图标，无升格）。
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

motion（P34-2 提速：快=灵敏=工具感，标杆交互档 120-180ms）:
  曲线: 全站 expo-out cubic-bezier(0.16,1,0.3,1)——CSS 走 globals.css
    `--ease-out-expo` token（ease-out-expo 工具类），motion 库写数组
    [0.16,1,0.3,1]。
  入场: fade + y 6-8px，180ms expo-out（消息行、chips）；品牌方印
    scale 0.85→1 400ms expo-out；产品界面仍不排页面级入场序列
    （operate.md：用户要的是进任务，不是看加载表演）。
  交互反馈: hover/focus 120-180ms（chips 走 transition-colors 150ms 档；
    composer 150ms ease-out-expo + focus 时 shadow-md 微光——「光从边框
    来」，Linear 式 hover 材质）。
  流式: 光标 1s steps 闪烁；首包前 shimmer 1.5s 扫光；TypingDots 0.9s
    弹跳（循环呼吸类不提速）。
  品牌: BlurText 逐字（CJK letters 模式，delay 45ms / step 250ms）
    只在空态标语用一次。
  禁用: WebGL 极光、边框流光、点击火花、鼠标跟随聚光、全页噪点纹理。
  状态语义件仅三件: 流式光标 / shimmer 骨架 / TypingDots。

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

referencing（P34-1 落库，2026-10-02；参照驱动工作流的真相源）:
  维护节奏: 每次大改版前校准一次；新参照进池必须附参数化清单，形容词
  条目无效。采集方式 = chrome-devtools computed styles（真实渲染值，
  非目测）+ 视觉模型分析；执行记录见 P34 任务书 §6。
  三层参照池:
  - 高级感标杆组（二拍方向的主力参照）: Linear / Vercel(Geist) /
    motion.dev / Stripe（Arc/Raycast 未采集备选）
  - 同构产品组: america.gov/chat（P25 已对标；本次补采集被 Cloudflare
    拦截留档）/ GOV.UK DS / Perplexity（SPA 待截图）
  - 开源参照组: vercel/chatbot（同栈基准）/ shadcn-ui/chatbot-template
    （typeset 排版组件）/ assistant-ui（组件形态词典）/ LobeChat /
    Open WebUI（形态层）/ AnythingLLM / Dify / FastGPT（同构业务层）
  高级感硬参数（四站实测，2026-10-02）:
  - 排版尺度: display 一律 42-64px，行高 = 1.0×字号（tight），负字距
    -0.86 ~ -3.84px（越大越紧）；副标题独立成档 32-56px；正文 14-16px。
    display:body 比率 ≈ 4:1。层级二元化——要么 huge 要么 small，中间
    档只给 UI chrome（Linear 视觉分析结论）。
  - 字重: 可变字体微调字重 450/510（非整档 500），「轻而大」优于
    「粗而大」（Vercel 64px/400 佐证）。
  - 色彩纪律: 强调色极稀缺（Linear 全视口约 30px² 的 amber）；CTA 靠
    亮度反差（白 pill + 深字）不靠色相；深度靠 hairline 边框与光，
    不靠阴影（Linear「light from borders, not shadows」）。
  - 动效节奏: 交互反馈 120-180ms（三站峰值均为 150ms 档），状态过渡
    150-250ms，入场/编排 200-500ms；曲线 = 陡 ease-out 族——
    cubic-bezier(0.16,1,0.3,1)（expo-out）/ cubic-bezier(0.25,1,0.5,1)
    （quint-out）。快 = 灵敏 = 工具感。
  - 细节密度: 高级感来源是细节（真实数据感/已交互态/mono 标识符/
    精准 hover）而非元素数量（Linear 全站装饰元素 ≈2 个）。
  - CJK 本土化警戒: 负字距对拉丁 -0.02~-0.04em，对汉字收窄到
    -0.01em 或 0（方块字过紧伤可读性）；「轻而大」对中文标题慎用
    （汉字笔画密度高，400 字重大字号发虚，用 500-550）。
  与现契约的差距清单（感知÷成本排序，P34-2 的输入）:
  1. 排版尺度断崖【感知★★★ 成本低】: 现 display 30px 仅空态、title
     16px、无副标题档、零负字距（display:body ≈ 2.1:1 vs 标杆 4:1）。
     → display 48-56px + 负字距 + subhead 24px 新档。
  2. 动效慢一倍【感知★★★ 成本低】: 现 250-300ms easeOut vs 标杆
     120-180ms 陡曲线。→ 交互档 120-180ms + (0.16,1,0.3,1)；入场保留
     200-250ms 但换曲线。
  3. hover 材质缺微光【感知★★ 成本低】: hairline 体系已有，缺 hover
     边框亮化/微妙光。→ 边框交互态（border-color 过渡 + 微 glow）。
  4. CTA 色相依赖【感知★★ 成本低】: navy 实底按钮；标杆用亮度反转
     （暗色下亮底深字 pill）。→ 暗色模式主按钮试点反色。
  5. 细节密度半成品【感知★★ 成本中】: mono 单号/doc_id 已有（=标杆的
     真实数据感）；缺已交互态细节与 hover 微反馈的系统性。
  6. 可变字重【感知★ 成本低 CJK 增益小】: 510/450 微调字重，拉丁
     场景可吸收，汉字场景低优先。
  AI tells 负参照（Anthropic 官方 frontend-design 点名，brief 未要求时
  即为未审视默认）:
  1. 暖米色画布（#F4F1EA 系）+ 高对比衬线 + 陶土强调（#D97757 系）——
     P18「Claude 暖编辑风」正是此配方，P25 已逃离，不走回头路。
  2. 近黑底 + 单一酸绿/朱红强调。
  3. 报纸风 broadsheet（hairline + 零圆角 + 细线密排）。
  4. SaaS 卡片套件（同形圆角卡 + 统一阴影 + 渐变洗）。
  5. 模板 chrome（全大写 tracked eyebrow / 中点分隔 meta / em-dash 标签
     碎片 / tinted 黑 #0B0B0B / 无语义 mono 数据标签 / 尾缀「→」）。
  辨析: 近黑画布与 hairline 本身不是 tell（Linear/Vercel 即反例），
  tell 是「近黑+酸绿单强调」「hairline+报纸密排」的组合模板化；
  gewu 的 mono 用于凭证号/doc_id 编码「机器标识符」语义，属结构即
  信息，合法。
  概念与拨盘（2026-10-02 用户拍板生效，P34-2 执行依据）:
  - 概念一句话: 「权威而灵敏的数字政务窗口——机构蓝的官方可信 ×
    Linear 式精准工具感；尺度断崖制造第一印象，120-180ms 的灵敏
    反馈维持工具信任，细节密度（真实单号/真实部门/已交互态）是
    高级感的唯一来源。」
  - 拨盘: variance 6（品牌三时刻+display 层+材质微光）/
    motion 5（更频但更快，快而准）/
    density 4（对话区留白+display 层拉开；不降 3——工具型产品
    保可用性）。

workflow（P34-3 落库：前端任务标准动作，六步回路）:
  ①概念: 从产品语义推导气质一句话（当前=「权威而灵敏的数字政务窗口」），
    遇分歧以它裁决；换概念=拨盘级决策，须用户拍板。
  ②参照: 先查 referencing 节参照池，缺参照先补采集（chrome-devtools
    computed styles 实测，形容词清单无效）再动手。
  ③系统: 改动先回写本契约（token/scale/motion/components），契约先行
    后写码——DESIGN.md 是拍板账本不是事后文档。
  ④实现: 按 ③ 的参数写码；预制组件与 tells 清单（referencing 节）禁入。
  ⑤视觉评审: `make design-review` 起临时服务，亮暗 × 关键页截图 →
    对照参照参数逐项判（有/无/部分）→ 差距回修 → 复审；形容词结论无效。
  ⑥工程自测: ruff + tsc + build + design-lint + 真跑剧本，全绿才收尾。
  回路: ⑤ 不过打回 ①②；P34-2 首轮评审抓 4 真问题（CJK 断词/环境光
  重复渲染/proximity 反转/chips 拥挤）即此步价值的留档。
  编辑式时刻白名单（全站仅三处，逐条+理由，禁扩散）:
  1. 空态（BlurText 逐字 + 氛围光——全站唯一光斑位）。
  2. 首答完成（操作条收束浮现 + 追问 chips stagger，一次性节奏）。
  3. 办理成功回执（图标 spring 落定 + 凭证号 mono 升格「签收章」）。
