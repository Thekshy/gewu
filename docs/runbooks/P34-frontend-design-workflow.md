# P34 前端设计工作流：六步回路与参照驱动（任务书）

> **背景**：用户对前端现状的判词是「太普通」。社区流行的工作流把前端
> 任务拆成四步（审美 / 动效 / 工程 / 自测，各由 skill 承担），逐 skill
> 覆盖后发现整体仍普通——2026-10-02 方法论讨论定位了根因：**四步全是
> 执行侧的管道，普通感的来源恰好落在它不覆盖的两头**——前面缺
> 「概念 + 参照」（决策侧：回答"这个产品该长什么样、为什么"），后面缺
> 「视觉评审回路」（反馈侧：回答"现在这样普通不普通"）。本仓库自身
> 历史即完整证据链：P16 堆 reactbits 组件（动效=组件库路线）→ P18/P20
> 相继退役 → P25 换 america.gov/chat 同构对标（概念+参照路线）才真正
> 脱离「AI 脸」——起作用的每一步都是概念与参照，没有一步是 skill。
> 另一半真相：DESIGN.md 拨盘 `variance: 4` +「熟悉感是特性」+「工具
> 消失在任务里」——**operate 纪律本身在系统性压制惊艳，「普通」有
> 一半是自己拍板要的**。故本票不推翻 P25、不换肤，路线 = 拨盘分层 +
> 内容驱动 + 功能性动效。

## 0. 拍板结论

| # | 问题 | 拍板 | 依据 |
|---|------|------|------|
| Q1 | 工作流形态 | **四步管道 → 六步回路**：①概念 → ②参照 → ③系统 → ④实现（含动效原则）→ ⑤视觉评审 → ⑥工程自测；⑤ 不过则打回 ①/② 重来。以后任何前端任务强制先过 ①② 再进 ④，收尾必过 ⑤⑥ | 管道无回路则第一步的概念错误一路错到底；gewu ③⑥ 已是范本（DESIGN.md 契约 + design-lint 门禁），缺口只在 ②⑤ |
| Q2 | 「普通感」归因与路线 | **不换肤、不提全站 variance**。三抓手：**(a) 拨盘分层**——工具页（console/memory/admin）保持 operate，品牌时刻提编辑感；**(b) 内容驱动**——差异化做在独有内容件上（引用溯源/时间线/流程可视化），壳永远是普通的；**(c) 功能性动效替换装饰动效** | 通用审美 skill 的输出天然趋同，趋同 = 普通的定义；只有从产品语义推导的概念与数据形状推导的内容件是不可复制的 |
| Q3 | 编辑式时刻边界 | **从一处扩到三处**（白名单式管理，沿用空态氛围光先例）：空态（已有）+ **首答完成时刻** + **办理成功回执时刻**。全站其余保持 operate 纪律，编辑感不得扩散 | variance 预算集中花在用户情绪峰值点（第一印象 / 任务达成），比全站加料感知更强且不破坏工具页纪律 |
| Q4 | 参照规范 | **参照池 3-5 个同构产品，特征必须参数化**——「好看」「高级感」不可验收，「衬线 display 仅空态、比率 2 倍、primary 使用位封顶六处」才可验收。参数化清单沉淀进 DESIGN.md 新增 `## referencing` 节 | 审美不能凭空生成，人类设计师 80% 时间在看和选；跳过参照直接套 skill = 全用默认值 |
| Q5 | 视觉评审回路 | **chrome-devtools MCP 截图（亮暗双主题 × 关键页）→ 拿参照参数清单逐项对照 → 差距清单 → 回修**。design-lint 只测契约违规（嵌套卡/第四种表面色），测不出平庸——视觉评审补这个洞，是四步「自测」缺的那一半 | 代码全绿与「不普通」完全正交；审美问题只有看渲染结果才能发现 |
| Q6 | 动效原则 | **动效 ≠ 组件库**。预制动效组件（reactbits 类）不回潮（DESIGN.md motion 禁令不松）；新增动效先过三问：为什么动（状态变化/注意力引导/空间连续性）、用户此刻注意力在哪、多久（250-300ms 档）。优先功能性动效：View Transitions API、列表 layout 动画、composer focus 空间连续性 | P16→P20 已为「动效=组件库」路线付过学费并反转；动效 skill 的正确形态是提取参照的曲线/时长参数，不是提供组件 |
| Q7 | 参照池范围 | **三层参照**：闭源产品参照（附录 A.1-A.3，学形态）+ **开源项目参照**（A.4，同栈读源码 / 同构学内容件）+ **skill 生态参照**（A.5，官方 frontend-design 的两遍工作流与 tells 清单**收编进 DESIGN.md**，不装 skill 本体） | 闭源只能看「长什么样」，开源能看「怎么实现」（token/组件/动效参数直接读码）；官方 skill 的方法论（subject grounding / token plan / 截图 critique）与六步回路 ①③⑤ 一一对应——**skill 是回路的加速器，不是回路的替代**，收编方法不引入依赖 |

## 0.5 二拍修订（2026-10-02 同日，方向翻转留档）

用户拍板转向：**以「提升高级感」为目标，动用主流设计思路与 UI 动效**。
一拍 Q2「不换肤、不提全站 variance」作废，本节为新方向基准：

- **推倒对象辨析（本节核心）**：要推倒的是**概念层与拨盘**——
  DESIGN.md 的概念只有一句话 + 三个拨盘数字，推倒成本≈零；**不是
  基建层**（token 纪律 / kit 规则 / design-lint / 六步回路 / 组件
  契约是 P16→P25 的学费，全部保留）。P25 已示范过同构操作：换情绪层
  不动 craft 地板。历史警告：P16→P18→P25 三轮每轮都推倒上一轮、
  每轮之后仍不满意——**变量从来不是改的量，是动手前做没做概念+
  参照的功课**。本次顺序约束不变：先 P34-1 诊断（参照池 + 差距
  清单 + 新概念陈述拍板），后动 token 与页面。
- **拨盘意向**：高级感与 variance 4 / density 5 直接冲突，意向
  variance 5-6 / motion 5 / density 3-4；**具体数字在 P34-1 差距
  清单产出后拍，不拍数字不写码**。
- **「高级感」参数化警戒**：主流元素里藏着官方 tells（近黑底+酸绿=
  tell#2、渐变洗卡片=tell#4）；高级感的来源是**细节密度**（hairline
  细节、微妙光、精准 hover、spring 曲线），不是元素堆叠——Linear
  全站元素极少、细节数百处。tells 清单在新概念下继续有效。

## 1. 目标 / 非目标

**目标**
- 六步回路固化为本仓前端工作流（写进 DESIGN.md，新会话可直接执行）。
- 参照池参数化清单落库，与 DESIGN.md 现契约的**差距清单**产出（本票的
  「普通」从形容词变成可执行的 diff）。
- 按差距清单落地 2-3 项感知最强改动（候选见 §2.2 与附录 A 差距初判）。
- 视觉评审回路跑通一轮全流程（截图 → 对照 → 回修 → 复审）。

**非目标**（0.5 二拍修订后）
- 不推翻基建层：token 纪律 / kit 规则 / design-lint / 组件契约 /
  六步回路全部保留（「不走回头路」限指暖陶土情绪层不复活，不限制
  概念重构）。
- 概念与拨盘重构**先诊断后动手**：新概念陈述与拨盘数字未拍板前，
  不改任何页面代码。
- 不引入预制动效组件库（reactbits/motion providers 类）。
- 工作流沉淀为**跨项目个人资产**（方法论文档不绑定 gewu 的
  DESIGN.md），gewu 是第一个试验场。

## 2. 设计与实现

### 2.1 P34-1 参照提炼票（先行票，零代码，纯文档）

- **参照池定稿**：三层（Q7）+ **高级感标杆组（0.5 二拍新增，新概念
  参照主力）**：Linear / Vercel(Geist) / Stripe / Arc / Raycast /
  motion.dev——dark minimal 系（安静表面 + hairline + 严控动效 +
  微交互），2026 主流共识；输出=「高级感参数清单」（排版尺度对比 /
  材质细节 / 动效曲线与时长档 / 密度与留白），与现 DESIGN.md 对照
  出差距。闭源层：附录 A 初稿基础上，执行时对 america.gov/chat、
  GOV.UK Design System、Perplexity 逐个**截图校准**（Perplexity 为
  SPA，文本抓取拿不到正文，必须截图目检）；可补 1-2 个（候选：
  Notion help center、Claude.ai 官网）。开源层（A.4）：同栈项目
  clone 到本地读源码取 token/组件形态，同构项目截图学内容件。skill 层
  （A.5）：官方 frontend-design 的 tells 清单与两遍工作流翻译入册。
- **参数化**：每个参照翻成四类参数——typography（族/尺度比率/衬线域）、
  color（primary/link/canvas 使用位数）、layout（宽度/密度/导航形态）、
  **内容件形态**（引用/溯源/信任信号的展示形状，这是同构参照最值钱的
  部分）。
- **差距清单**：参数对照 DESIGN.md 现值，输出「gewu 缺什么」的可执行
  diff（禁止形容词条目），按「感知强度 ÷ 实现成本」排序取前 2-3 项
  作为 2.2 输入。
- 沉淀：DESIGN.md 新增 `## referencing` 节（参照池 + 参数摘要 + 维护
  节奏：每次大改版前校准一次）。

### 2.2 P34-2 品牌时刻 + 内容件票（实施票，按差距清单裁剪）

候选池（最终以 2.1 差距清单排序裁剪，以下是附录 A 初判的预选）：

- **编辑式时刻二、三**（Q3 白名单扩容）：
  - 首答完成时刻：首次回答 done 的收束动效/排版强调（一次性，仅会话
    首答，不重复打扰）。
  - 办理成功回执时刻：receipt-alert 升级为凭证卡（mono 凭证号 + 完成
    图标 + 办理摘要），成功是全流程情绪峰值，值一次编辑式排版。
- **内容件升级**（内容驱动主战场）：
  - 空态建议 chips → **任务预览卡**（america.gov task preview 同构：
    用真实任务形态代替纯文字 chip，如「预约体育馆」卡内直接展示场地/
    时段缩略形态；数据可先静态占位）。
  - 输入框**轮换示例问题**（america.gov rotating questions 同构，
    低成本高感知）。
  - 回答内引用的部门徽标位从 sources-dialog 前移到消息流引用行
    （信任信号前置）。
- **功能性动效**（Q6 三问过滤后）：
  - 消息列表插入的 layout 动画（新消息入场推挤旧消息的连续性）。
  - composer focus / Dialog 开合的空间连续性（若现状已有则核对时长
    与曲线档位）。
- 每项落地后 DESIGN.md 对应 component 条目回写；编辑式时刻白名单
  与门禁白名单同款管理（逐条 + 理由，禁止扩散）。

### 2.3 P34-3 视觉评审回路票（固化票）

- **流程固化**：截图脚本/Makefile 目标（如 `make design-review`：
  亮暗 × 关键页[chat 空态/chat 对话中/console/compare/memory/admin]截图
  落地 `docs/runbooks/assets/p34/`）。
- **评审口径**：拿 `## referencing` 参数清单逐项打分（有/无/部分），
  输出差距表； adjective 验收无效（「更精致了」不是结论，「primary
  使用位从 9 处收敛到 6 处」是结论）。
- **回路**：差距表非空 → 回修 → 复截 → 复审，直至清单项全闭或明确
  拒绝（拒绝须留理由）。
- DESIGN.md 回写 `## workflow` 节：六步回路图 + 本票产出的流程引用，
  作为以后前端任务的标准动作。

## 3. 风险与守卫

| 风险 | 守卫 |
|---|---|
| 编辑式时刻从三处白名单扩散成「到处都编辑」 | 白名单逐条+理由管理（空态氛围光先例）；design-lint 语义可加规则时加规则，不可加则 CODE REVIEW 清单项 |
| 变相换肤（借「品牌时刻」改全局 token） | token 层零改动写入验收；改动只允许组件级局部样式 |
| 动效组件库借「功能性动效」之名回潮 | 每个新动效过 Q6 三问并留档 DESIGN.md motion 节；预制组件零引入 |
| 参照参数化退化为形容词清单（「官方感更强」） | 2.1 验收硬性要求：每条差距须可被 design-lint / 截图对照判真伪 |
| 评审主观性（AI 评审员口径漂移） | 评审只对照参数清单逐项判有/无/部分，不做整体主观评分 |
| 与并行会话工作区冲突（web/ 目录） | 军规照旧：pathspec 提交、先 grep 交集；本票纯前端与 server 侧 P32/P33 无文件交集 |
| 任务预览卡依赖后端数据形态 | 首版静态占位（数据形状定型后升级），不为本票开后端工单 |

## 4. 验收门禁

- [ ] DESIGN.md 新增 `## referencing`（参照池参数清单）与 `## workflow`
      （六步回路 + 评审流程引用）两节，形容词零条目
- [ ] 差距清单产出并按感知/成本排序，2.2 落地项从中选取（留选择理由）
- [ ] 落地 2-3 项改动：编辑式时刻二三 / 内容件 / 功能性动效中至少
      各一类（具体以差距清单为准）
- [ ] 视觉评审回路跑通一轮：截图（亮暗 × 关键页）落 assets、差距表
      产出、回修后复审闭项（或明确拒绝留理由）
- [ ] ruff + tsc + build + design-lint 全绿；工具页（console/memory/
      admin）零改动（Q2 拨盘分层的直接证据）
- [ ] 真跑：chat 空态 → 首答 → 办理回执全流程目检（亮暗双主题）
- [ ] roadmap 勾选；任务书 §6 执行记录

## 5. 遗留与后续

- **尺度对比重权衡**（~~本票不做~~ **0.5 二拍已提级为本票主线**）：
  现 scale 比率 1.14-1.2 密度优先，display 30px 仅空态一处。惊艳感与
  尺度对比强相关，高级感概念重构必然触碰它——与 DENSITY 的冲突按
  0.5 拨盘意向解决（density 3-4），具体数字 P34-1 诊断后拍。
- 参照池维护：每次大改版前校准一次；新参照进池须附参数化清单。
- 任务预览卡数据化：后端场地余量/时段接口就绪后从静态占位升级。
- 若「内容驱动」路线验证有效，下一步是把信息设计做深（引用溯源链
  可视化、制度知识地图）——数据形状驱动，通用 skill 永远给不了。

## 6. 执行记录

### P34-1 参照提炼票（2026-10-02 执行，纯文档零代码）

- **采集方式**：chrome-devtools MCP 真开标杆站，读 computed styles
  （真实渲染值非目测）+ transition 声明统计 + analyze_image 视觉分析
  （Linear）。四站硬数据：linear.app / vercel.com / motion.dev /
  stripe.com（中文版页）。
- **america.gov/chat 补采集被 Cloudflare 验证页拦截**（8s+15s 两轮等待
  未过）——留档跳过：P25 对标已吸收其形态参数，高级感差距清单主力
  是 Linear 系，不受影响；后续如需重试走带登录态的浏览器。
- **产出一：DESIGN.md 新增 `referencing` 节**——三层参照池 + 高级感
  硬参数（四站实测）+ 差距清单（6 项按感知÷成本排序）+ AI tells
  官方五条翻译与 gewu 辨析 + 维护节奏。
- **产出二：差距清单 Top2**（P34-2 输入）：①排版尺度断崖（display
  42-64px/负字距/tight 行高 vs 现 30px 封顶零负字距，比率 2.1:1 vs
  标杆 4:1）②动效慢一倍（标杆交互档 120-180ms 配 expo-out 陡曲线
  vs 现 250-300ms easeOut）。两项均纯 CSS token 级改动，感知★★★
  成本低。
- **产出三：概念一句话 + 拨盘提案**（写入 DESIGN.md referencing 节，
  **待用户拍板**）：「权威而灵敏的数字政务窗口」；variance 4→6 /
  motion 4→5 / density 5→4。拍板前不动 token 不写码（0.5 顺序约束）。
- **跨站一致性佐证**（方法有效性）：动效 120-180ms 三站独立实测完全
  一致（Linear 0.12/0.16/0.2s、Vercel 0.15/0.2/0.1s、motion.dev
  0.12-0.2s 为主峰）；负字距四站全有（-0.86~-3.84px）；Vercel 与
  gewu 同用 Geist，证明差距在用法（尺度/字距/节奏）不在字体选型。
- **执行口径注记**：采集值含拉丁优先场景（Linear/Vercel 是英文站），
  CJK 适配警戒已写入 referencing 节（汉字负字距收窄至 -0.01em、
  大字号中文用 500-550 不用 400）。

### P34-2 第一批：高级感三件套（2026-10-02 执行，同日拍板后）

- **拍板生效**：概念「权威而灵敏」+ 拨盘 variance 6 / motion 5 / density 4
  写入 DESIGN.md frontmatter 与 referencing 节（契约先行，后动代码）。
- **落地清单（差距清单 Top3）**：
  1. 尺度断崖：空态标语 30px→48px（移动 34px）+ leading 1.15 +
     tracking -0.01em（CJK 警戒内）；方印 size-16→20 / text-3xl→4xl；
     新增 subhead 24px 档入契约（品牌时刻第二批消费）。
  2. 动效提速：motion 库 ease "easeOut" 0.25s → [0.16,1,0.3,1] 0.18s
     （消息/chips 入场，y 10→8）；方印 0.45→0.4；BlurText delay 55→45 /
     step 0.3→0.25；globals.css 入 `--ease-out-expo` token + composer
     150ms ease-out-expo + focus shadow-md 微光（「光从边框来」）。
  3. density 校准：消息间 space-y-4→5（对齐契约 20px）；底部操作带
     space-y-2→3 / py-3→4。
- **视觉评审回路实战（六步回路第⑤步首次全流程）**：build 产物起 3201，
  截图亮暗双主题 → 视觉模型评审 → 修复 → 复审。**第一轮抓到 4 个真问题**：
  ① CJK 断词（「预/约场馆」被拆）——text-balance 对 BlurText 逐字
  span+flex-wrap 无效，终解=手动两行 BlurText（断点落短语边界）；
  ② 环境光代码块**整块重复渲染两次**且混入契约外 amber 光斑（评审
  判「黄光偏右过强」的根因）——收敛为契约内单份 primary 冷光；
  ③ 说明行/信任行 proximity 反转——组内 space-y-2、与标语拉开；
  ④ chips/composer 拥挤。修复后亮暗双主题均 PASS（暗色评审顺带确认
  CTA 为全屏最亮元素=亮度做 CTA 原则自然达成）。
- **门禁**：build ✓（无代理直连）+ design-lint 五页全绿 ✓（修复后复跑）。
- **未做（第二批候选）**：品牌时刻二（首答完成）与三（回执升级 subhead
  档）、任务预览卡、轮换示例问题、暗色 CTA 反色试点（评审确认现状已
  达成）、tooling 页零改原则下的对照验证。
- **执行坑留档**：3100 被 dev server 占用（EADDRINUSE）→ 验收起服务
  用 3201 分端口（军规：不杀他人进程）；analyze_image 偶发空响应，
  重试即恢复。

### P34-2 第二批 + P34-3（2026-10-02 执行，用户授权「按推荐全做+push+部署」）

- **四项落地**：
  1. 首答完成时刻（白名单之二）：MessageActions 收束浮现（400ms
     expo-out + delay 0.1s）+ 首答追问 chips 逐个 stagger 60ms；
     `initial={i===1 ? … : false}` 保证后续回答零动效。
  2. 办理成功回执（白名单之三）：ReceiptAlert 成功分支升格——图标
     spring 落定（500/30）+ 凭证号 mono 独立行 primary 色（「签收章」
     语义）；失败分支照旧不升格；仍单层 Alert。
  3. 任务预览卡：SUGGESTIONS 退役 → TASK_CARDS（icon+title+真实任务
     desc+q）；rounded-xl+hover 边框亮化+shadow-sm（150ms
     ease-out-expo）；suggestion-chip 契约退役留档。
  4. 输入框轮换示例：ROTATING_EXAMPLES 5 条，输入空时 2.8s 轮换，
     有值即停；placeholder 前缀「试试：」。
- **P34-3 固化**：`scripts/design-review.sh` + `make design-review`
  （build 产物临时服务 3210 + CHECKLIST.md 留档）；DESIGN.md 落
  `workflow` 节（六步回路 + 编辑式时刻白名单三处正式化）。
- **视觉评审（design-review 首跑）**：亮暗双主题 PASS；评审认可任务卡
  「读起来像真实任务预览而非通用 chip」（america.gov 同构达成）、
  轮换 placeholder「像真人输入的提示」。
- **门禁**：build ✓ + design-lint 五页全绿 ✓（第二批后复跑）。
- **部署链路确认**：P32 DDL（memory_fact.deleted_at）内联启动自动迁移
  （memory.py:70），服务器无需手动 SQL；工作区仅 P34 改动（P33 已被
  并行会话 commit 完毕：947c593/fa12ff4），push 将带 P32×2+P33×2+P34。

---

## 附录 A：参照池初稿（2026-10-02 预研，执行时截图校准）

### A.1 america.gov/chat（P25 对标原点，本次补参数化）

文本预研可确认的结构特征：

- **信任三件套**：官方横幅（"An official website of the United States
  Government"）+ 机构徽章（DOI/State/Commerce）+ 隐私声明（"Your
  personal information isn't collected or stored"）。gewu 对应物 = 空态
  信任行（已有）+ sources-dialog 部门徽标位（已有）；**差距 = 信任
  信号未前置到消息流**。
- **任务预览件（task preview widgets）**：空态用真实任务形态的预览
  （职位匹配含薪资、护照追踪、药价对比）代替营销图——「展示你能办成
  什么」而非「描述产品」。gewu 对应物 = 纯文字 suggestion chips；
  **差距初判：感知强、首版可静态，预选落地**。
- **轮换示例问题**：输入框内轮换 10 个示例问题。gewu 无；
  **差距初判：低成本高感知，预选落地**。
- **footer 官方署名**：大 footer + GSA 署名 + 语言切换。gewu 页脚
  meta 已有制度文档声明；差距小，可选。
- 排版/色为推断（文本抓取拿不到 CSS），执行时以截图校准 DESIGN.md
  的 america.gov 条目。

### A.2 GOV.UK Design System（机构感金标准，原则层最有参照价值）

- 核心原则直接可引：「Make government services look and feel like
  GOV.UK」——**一致性/熟悉感本身即设计目标**，与 P20「熟悉感是特性」
  同构（gewu 已在正确的路上，这条路的天花板就是「克制的普通」，
  这正是 Q2 拨盘分层拍板的依据）。
- 「do not assign new meanings to colours」「don't restyle buttons」
  —— 不给既有色/组件赋新义，对应 gewu 的 kit 规则与六处 primary
  封顶，双方同构，无差距。
- 已知 token（待截图/源码校准）：link 蓝 #1d70b8、focus 黄 #ffdd00
  （签名级焦点态）、正文 #0b0c0c、body 19px/1.6、16px 间距基。
  **差距初判：focus 态 gewu 走通用 outline-ring，GOV.UK 的签名黄是
  「普通组件做出机构签名」的范例——可选，优先级低**。
- 尺度对比（19px body + 大标题层级）与 gewu DENSITY 5 冲突，
  列入 §5 概念重权衡，本票不动。

### A.3 Perplexity（引用溯源形态参照，SPA 文本抓取失败）

- 执行时必须截图目检后补全本节；已知待校准项：行内引用上标序号
  形态、sources 顶部聚合卡、related questions 尾件、流式 step 展示。
- gewu 的 citation-chip + sources-dialog 形态已接近，**预判差距主要
  在「引用的时间线化/溯源链」而非有无**——对应 §5 信息设计深做线。

### A.4 开源项目参照池（2026-10-02 调研，三层用法）

**同栈源码层**（Next.js + shadcn/ui + Tailwind，直接读码取形态——
闭源参照给不了的增量）：

- **vercel/chatbot**（原 ai-chatbot，~20k★）：Next.js + AI SDK 官方
  全功能模板，auth + 持久化 + 多模型路由 + 流式。与 gewu 同栈，
  消息流/composer/artifact 渲染的组件结构可直接读源码对照
  （gewu 技术栈正是从它这一系演化来的，读它 = 读「官方基准实现」）。
- **shadcn-ui/chatbot-template**（shadcn 官方，~1k★）：极简模板，
  自带 **shadcn/typeset**（官方排版组件）——对 gewu answer.tsx 的
  prose 域（政策条文/标题/引用块渲染）是同源参照，值得专门看。
- **assistant-ui**：React chat 组件库（shadcn 风格）。**不入依赖**，
  当「chat 组件形态词汇表」查——想要某个形态先看它有没有现成解。

**形态层**（截图目检学形态，栈不同不读码）：

- **LobeChat**：公认 UI 最精致的完整 AI chat 应用。学空态/艺术字/
  会话管理形态；注意社区对其开源纯度有争议——**学形态，不学架构**。
- **Open WebUI**（Svelte）：自托管之王。多用户/知识库交互形态参照。
- LibreChat / NextChat：稳定性好但形态普通，低优先级。

**同构业务层**（RAG 问答同域——内容件直接同构，毕设答辩级参照）：

- **AnythingLLM**：RAG workspace，引用面板/工作区形态。
- **Dify / FastGPT**：国内 RAG 平台。知识库管理页与 gewu
  console/admin 同构，引用与溯源面板形态可对标（同域竞品分析素材）。

元资源：billmei/every-chatgpt-gui（LLM 前端客户端大全，扩展时查）。

### A.5 skill 能力地图（官方 anthropics/skills，19 个）

与前端相关的官方 skill 评估（仓库 ~179k★；除文档四件 source-available
外均 Apache 2.0）：

- **frontend-design ★★★（收编，不装）**：官方「治 AI 脸」方法论，
  与六步回路三处一一对应——subject grounding（独特性从主题的行业/
  材料/惯用语推导，toy for girls 8-11 ≠ analyst dashboard）= ①概念；
  两遍工作流（先产 token plan：4-6 个命名 hex + 字体角色 + ASCII
  线框，对照 brief 评审、改掉泛化项并说明改了什么，然后才写码）=
  ③系统的前置计划；截图 critique + Chanel「去掉一件配饰」= ⑤视觉
  评审。**执行方式：方法论翻译进 DESIGN.md 的 referencing/workflow
  节，tells 清单入册（见下），不引入 skill 依赖**。
- **webapp-testing**：对应⑥工程自测。gewu 已有 design-lint + 真跑
  剧本，对照即可，不引入。
- **theme-factory**：artifact 换肤选色器（10 预设主题+按描述生成）。
  与 DESIGN.md 契约纪律重复且更弱，不引入。
- canvas-design / brand-guidelines / web-artifacts-builder：外围，
  当前不引入。
- 社区合集：VoltAgent/awesome-agent-skills（1000+）、
  awesome-claude-skills-zh——按需查，不预装。

**AI 设计 tells 官方五条**（frontend-design 点名的「未审视默认」，
翻译入 DESIGN.md referencing 节作为负参照；brief 明确要求的合法）：

1. 暖米色画布（#F4F1EA 系）+ 高对比衬线 + 陶土强调（#D97757 附近，
   Anthropic 关联 tell）——**gewu P18「Claude 暖编辑风」正是此配方，
   P25 机构蓝换肤 = 逃离官方点名的 tell，「不走回头路」条款的又一
   依据**。
2. 近黑底 + 单一酸绿/朱红强调。
3. 报纸风 broadsheet：hairline 细线 + 零圆角。
4. SaaS 卡片套件：同形圆角卡 + 统一阴影 + 渐变洗。
5. 模板 chrome：字距拉开的全大写 eyebrow、中点分隔 meta 串、em-dash
   标签碎片、tinted 黑（#0B0B0B/#111）、mono 数据标签、尾部「→」。

gewu 自查辨析：DESIGN.md 的「单号/凭证号/doc_id/延迟 ms 走 mono」
不是 tell #5——mono 在此编码「机器产生的标识符」语义，属于官方同一
文档推崇的「结构即信息」（编号只给真实序列），是有语义的结构，不是
装饰性默认。
