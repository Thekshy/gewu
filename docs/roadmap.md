# 路线图

## M0 · 骨架（已完成）

- [x] 问题路由（LLM + 启发式降级）
- [x] 混合检索：BM25 字符二元语法 + 向量 + RRF
- [x] RAG 直答与 Deep Research 链路（SSE 全事件流）
- [x] 引用与拒答机制
- [x] 限流 + 每日 token 预算
- [x] 合成语料（15 篇）与种子评测集（18 题）
- [x] CI（gofmt + go vet + go test + next build；Python 版历史见 tag python-final）

## M1 · 检索质量

- [ ] 评测集扩充至 100+ 题（含同义改写、口语化提问）——P15 已建变体生成管线
      （`make variants`，flash 生成口语化/同义/缩略变体、gold 继承原题），检索集
      现 60 题（15 原题 + 45 变体），人工校验后可持续扩
- [x] 混合检索权重调优（RRF k 值 / 加权 RRF）——P15：加权 RRF（向量 0.7/关键词
      0.3，归一 [0,1]，WeKnora 公式）+ 检索参数全量配置化（POOL_N/K/权重/阈值）
- [x] 查询改写：口语问题先规范化再检索（LLM 把「最多/借几本」换写成「上限/外借」，无 key 时跳过）
- [x] 分块策略实验：按条款切分 vs 滑动窗口，指标对比——P15 切片策略链落地
      （profiler 画像→heading→recursive 降级，参数全配置化 + `make ingest` 一键
      重建）；小库上指标天花板（60 题近满分），系统性消融待语料扩充后补数据
- [x] 向量检索接入（需含 embeddings 额度的 key，编码套餐不含）

## M2 · 知行执行层（初版完成）

- [x] 能力域路由：知识问答 / 业务办理 / **混合意图**（咨询政策的「请假找谁批」不会误判为办理）
- [x] mock 校内业务系统：场馆预约（容量/每日限额/冲突）+ 请假审批（分级审批与语料一致）
- [x] 工具层 + 权限矩阵（学生/辅导员），越权在工具层被拦截
- [x] 槽位收集与多轮澄清：LLM 抽取 + 确定性中文日期解析兜底（明天/下周三/9月2日/请 N 天假）
- [x] 写操作确认流：确认摘要 → 用户确认 → 执行 → 回执；读操作直接执行
- [x] 失败恢复：时段冲突给可选项重问、字段非法重新收集；切话题自动放弃流程
- [x] 交易型评测：多轮用例断言业务库真实状态（预约/请假单、冲突恢复、权限拦截）
- [x] 会话状态持久化（P8-1：SESSION_STORE=sqlite，data/sessions.db，重启续办到确认已真跑验证）
- [ ] 更多业务域（报修、活动报名）与辅导员审批闭环体验

## M3 · 评测体系

- [ ] LLM-as-judge：答案忠实度（faithfulness）打分，抽样人工校准
- [ ] badcase 库：线上/评测失败案例沉淀与回归
- [x] 检索层独立评测（recall@k），与端到端指标分层归因——P15：`make
      retrieval-eval`（doc 级 Recall@k/MRR/NDCG@k，指标族参考 WeKnora），--no-rewrite
      / --no-rerank 分离 LLM 方差；before/after 对比留档 eval/reports/
- [ ] 每次模型/提示词变更产出对比报告

## M4 · 部署与展示

- [ ] 用户体系（P21~P23）：邀请码封闭注册 / 会话管理 / 记忆可见化 / 管理后台——
      公网部署前置（防配额滥用、role 权限坐实、台账归属）
- [x] 可演示前端（P11）：对话 / 对比实验台 / 控制台三视图——同题 A/B 双流
      （cascade workflow ↔ ReAct agent）、业务台账、检索调试；验收记录见
      [runbooks/P11-web-demo.md §5](runbooks/P11-web-demo.md)
- [ ] Docker Compose 部署到公网 ECS（Nginx + SSE 配置）
- [ ] 5 分钟演示录屏：事实题 / 多跳题 / 拒答三条路径（三页面已就绪可开录）
- [ ] 技术报告：设计决策、评测数据、badcase 复盘
- [ ] GitHub Actions 部署流水线（push main → 构建 → 上线）

## P 系列 · 能力演进与退役（已完成）

- [x] P0~P5 微服务迁移：六服务 + 26/26 逐题 PARITY（历史形态见 [docs/history/](history/) 与 ADR-0009）
- [x] P6 对齐业界主流：级联路由 / 父子块 RAG / Rerank / Memory / ReAct（开关灰度）
- [x] P7 真跑修复：误路由安全网 / 并发健壮性 / 父子块退化；多轮指代补全贯通路由与检索
- [x] P8 微服务退役：会话持久化先吸收（P8-1）→ 六服务删除（P8-3）→ 单体模块化收口（P8-4，
      lint-arch 依赖规则守护 + X-Trace-Id 观测 + ADR-0009）
- [x] P10 LLM 响应侧收口：finish_reason/usage 三元组解析（P10-1）→ ReAct 截断防御——
      length 先于工具解析，不执行不完整调用、Pi 式合成 observation 回填（P10-2）→
      流式截断 status + done.reason 单点收口（P10-3，completed/max_tokens/error/aborted，
      SSE 契约只增不改）；报告见 eval/reports/P10-*.md 四份（基线/llm/ReAct/done）
- [x] P14 LangGraph 迁移：编排层全量迁 Python+LangGraph（PARITY 契约下前端零改动、
      interrupt() 确认门、PostgresSaver 重启续办、P10 截断防御等价移植）；Go 后端退役
      （tag `go-final`）；评测 28+8 全绿——ADR-0010 / [P14 任务书](runbooks/P14-langgraph-migration.md)
- [x] P15 RAG 对齐 WeKnora：入库 CLI+自适应切片策略链/加权 RRF/精排容错/检索层独立评测
- [x] P17 编排层 agent-first 重构：mode=auto 切换 create_agent+middleware 单循环（guard lenient
      安检/写确认门迁 HITL/截断防御平移/deep_research 工具化/route 两段式+chitchat）；
      cascade 降级 mode=classic 实验基线；SummarizationMiddleware 收口上下文压缩（P13 顺延线闭线）；
      chitchat 新集 8/8 + 双轨对照报告 eval/reports/orchestration-20261001.md——
      [P17 任务书](runbooks/P17-agent-first-orchestration.md)
- [ ] agent 轨多轮办理稳定化：tx-002/tx-003 对话式收集的 GLM 非确定（P17 已知 flaky，重放全对）；
      classic 轨论文完成后按退役模式收口（tag+留档+删码）
- [x] 长期记忆消亡与用户侧可见性（当前仅注入不可管理）——P22 承接并闭线：
      /memory 面板 + /api/memory/facts 三端点（查看/编辑/删除/新增，upsert 语义）、
      会话删除连带清 episodic
- [x] P21 用户体系·认证地基：邀请码封闭注册（内测）+ argon2 密码 + httpOnly
      cookie + role 服务端化（tools_for 权限矩阵坐实）+ business/memory 迁 PG
      （P13 遗留闭线）+ CORS 收紧/前端同源代理——
      [P21 任务书](runbooks/P21-user-auth-foundation.md)（2026-10-01 执行完毕：
      SSE 过 next rewrite 透传 0.17s 首事件验证、真跑剧本八步全过、191 单测）
- [x] P22 会话与记忆管理：会话归属表/列表/改名/删除 + 前端多会话侧边栏 +
      memory_fact 用户可见可管理——
      [P22 任务书](runbooks/P22-session-and-memory-management.md)（2026-10-01 执行完毕：
      服务端显式创建/历史完整恢复/独立页 /memory 三拍板落地；212 单测、
      删除三处连带 PG 断言、浏览器真跑剧本全过）
- [x] P23 管理后台：admin 用户/用量/会话巡查 + 邀请码发放 UI + per-user
      token 预算——[P23 任务书](runbooks/P23-admin-console.md)（2026-10-01 执行
      完毕：contextvar 记账贯通（真跑 732 token 落账实证）+ users.daily_token_limit
      个性化限额 + admin 八端点 + 独立页 /admin、225 单测、浏览器目检亮暗双过）
- [x] P24 线上观测补盲与检索链路提速：agent 主循环 [llm] per-call 埋点
      （UsageRecordMiddleware，闭 538b9cf 盲区）+ 工具路径免二次改写
      （search expand=False + Rewriter 去重，线上「食堂位置」×2 实证）+
      检索词丢原话代码闸（SearchQueryGuardMiddleware）+ 延迟归因真跑
      （auto 轮 14837ms 拆账，POOL_N 缩缩减评测门禁可选）——线上日志取证
      2026-10-01，语料缺口线暂缓——
      [P24 任务书](runbooks/P24-telemetry-and-retrieval-tuning.md)（2026-10-01
      执行完毕：212 测全绿、线上三轮真跑（食堂轮 agent主循环 ×2 埋点落地
      5026+3112ms、检索串重复词消失、办理 VE-0272）、两次主模型串行占 56%
      坐实结构性成本、POOL_N 未达加菜门槛）
- [x] P25 对话体验对齐 america.gov：**结构+皮肤都仿**（用户复拍板）——机构蓝
      换肤（DESIGN.md P25 版：navy #1a3a5c/冷调近白画布/link 蓝新 token/用户
      消息实底气泡/暗色海军蓝+冰蓝）+ 消息操作条（来源 N/赞踩/复制）+ 来源按
      发文部门分组 Dialog + done 后 flash 生成追问 pills（SSE 追发 follow_ups，
      主路径零延迟增量、8s 超时静默、三层代码守卫）+ POST /api/feedback
      （upsert 覆盖）+ 空态信任行 + skip 链接 ×2 + 错误态重试钮——
      [P25 任务书](runbooks/P25-conversation-experience-parity.md)
      （2026-10-01 执行完毕：240 单测（+15）、design-lint 六页零 finding
      （换肤零改动即过=token 层收口兑现）、curl+浏览器真跑剧本全过（真 flash
      追问三轮质量在线、feedback good→bad 覆盖单行 PG 断言、停服重试闭环）；
      compare 双轨零改动保对照干净）
- [x] P27 第一方链路追踪：trace/span 落 PG（三接缝系统性采集=chat 轮首尾/
      ToolTraceMiddleware 栈最外层含拦截留痕/LLMService+UsageRecord 双 LLM
      接缝，新工具零观测成本）+ OTel GenAI 语义命名保 ARMS exporter 后路
      + `make trace-query` 六动作 CLI（第一消费者=AI 排障）+ P27-0 日期
      感知小修（AGENT_SYSTEM 注入「今天是」，闭 TYLOO「昨天」不可解根因）
      ——三方案比选（LangSmith 实测连通但出境+配额出局；ARMS 量级不值）与
      AI 消费者视角拍板留档任务书 §0——
      [P27 任务书](runbooks/P27-first-party-tracing.md)（2026-10-02 执行：
      真跑验证 web_search 检索词原样落 span、guard 拦截轮也留痕；admin 链路
      页/feedback join/TTL 列 B 期）
- [x] P26 联网检索（阿里 IQS）+ agent 通用化：web_search 工具条件注册（key
      空=工具/提示词准则/日限闸三处全缺席=能力注入，WeKnora 范式）+ 适配层
      防腐（实测口径钉死：contents 勿传/publishedTime 为 ISO 串/hostname
      中文站名）+ AGENT_SYSTEM 通用化（校外问题尽力答+口径声明，旧「引导
      回校园」废止）+ SearchQueryGuard 扩展 + 每日次数闸（IQS 按次计费）
      + citations 跨轮清零修复（agent_in 每轮重置，真跑发现的存量 bug）
      ——无任务书（会话内实施，方案对话留档），10-02 晨已先行部署线上
- [x] P28 Guard 拦截语义收窄：block 从「高置信范围外」改为「仅危险/违法违规/
      学术不端」（guard 与 AGENT_SYSTEM 第 9 条同一堵墙，P26 通用化对齐；
      两次线上实证 guard 非确定把校外首触变掷骰子）——GUARD_BLOCK_ANSWER
      新话术分家（classic REFUSAL_ANSWER 基线零改动）+ eval refu-001~003
      换危险样例（原道歉邮件/A股/电影按新口径都该答）——
      [P28 任务书](runbooks/P28-guard-block-narrowing.md)（2026-10-02 执行：
      TYLOO 同题首触放行联网且拿到 10-01 真实赛果=原始诉求闭环；危险样例
      三连仍拦；发现存量缺口=run_eval 未适配 P21 认证，另立 chore）
- [x] 联网检索三改（P29 量级，会话内直改未立票）：freshness 时间窗（web_search
      加 day/week/month/year → IQS 顶层 timeRange，**四档枚举实测全生效**，
      advancedParams/queryContext 里同名参数均无效——口径钉死在 websearch.py）
      + miss 换词重试（适配层 (hits,status) 三分支：无命中回执引导换词二跳，
      WeKnora「证据不足改写再试」条款进 docstring）+ canonical URL 去重与
      scheme 过滤。真跑：TYLOO 同题 span 见 freshness=day、引用全部落在
      昨日赛况（旧闻绝迹）、校内题零联网回归。
- [x] P31 harness 收敛单循环：guard 降码闸（P31-1：classify_guard LLM 判定与
      meta 出口退役，GREETING_RE/GREETING_RE+DANGER_RE 关键词三分支——纯问候
      零成本放行、危险词硬红线 21ms 短路零 LLM、软寒暄交主循环自然答；热路径
      小模型清零达成，首个 answer_delta 前零 small 调用实证）+ classic 全量
      退役（P31-2：级联路由/直答/深研/tx 流程节点与 refusal 链删除，tag
      `classic-pre-retirement` 留档，评测轨改 agent-only；txmeta.py 槽位
      元数据拆分、classify_reply 迁 resume.py、research.plan 保留；mode 枚举
      收窄 auto/react、compare 页与 mode 选择 UI 删除、run_eval refusal 口径
      改 guard 关键词 block 或主循环拒答）+ 外壳塌缩（P31-3：端点直调
      create_agent 编译产物、checkpointer 直挂、mem_block 装配迁
      AgentPromptMiddleware、subgraphs 摘除、resume 桥单形态）——
      判断架构终局表述=动作级代码闸是唯一下限机制，软语义全部交主模型
      prompt；classic 28 题历史报告与 P17/P19 执行记录留档——
      [P31 任务书](runbooks/P31-agent-only-harness.md)（2026-10-02 执行：
      §0 八条拍板四家生产系统调研背书）
- [x] P32 记忆层升级（对齐 zcode 模式）：写路径「开眼」（P32-1：抽取 prompt
      携带现有事实清单+禁抽清单、窗口单轮→最近 6 条 episodic、输出 facts+forget
      双字段且裁决权在代码（kind/key 复合串归一化 + 禁抽丢弃 + 先删后写纠正
      语义获胜）+ memory_fact 软删 tombstone——面板删除不再被抽取复活，恢复
      =面板重发）+ 读路径「分轨+陈旧」（P32-2：memory_block 头部两行声明
      「背景非指令/与 RAG 冲突以检索为准」+ 行尾「N 天前更新」标注 + 事实
      >20 时 profile 常驻非核心按问题词面 2-gram 筛选、≤20 与原行为等价、
      零新增热路径 LLM 调用）。对照基线=zai-org/ZCode v3.14.3 源码调研
      （index-only 召回/抽取子代理/代码级工具沙箱；LLM 选择器仅桌面版未合入
      main，不上——保 P31 热路径成就）——
      [P32 任务书](runbooks/P32-memory-layer-upgrade.md)（2026-10-02 执行）
