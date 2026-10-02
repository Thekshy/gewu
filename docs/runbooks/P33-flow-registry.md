# P33 办理流程注册表化：同构收编 run_flow + 单一真相源（任务书）

> **背景**：P31 后 harness 已收敛单循环，工具面 = 3 知识/联网工具 + 8 业务
> 工具平铺。2026-10-02 架构讨论拍板办理域扩展路线：**办理流程同构
> （槽位收集→确认→执行→回执），最优扩展形态不是更多工具，而是收编为
> `run_flow(flow_id, args)` + `query_flows()` 两个入口 + 注册表数据行**
> ——「同构收编、异构检索」判据：能收编的降维成数据，收编不了的才上
> tool retrieval（后者触发条件留档 §5，本票不做）。当前痛点（本票顺带
> 修）：写性判定存在**三个真相源**——mw.py `WRITE_TOOLS` 静态集合、
> txmeta.py `FLOW_DEFS`、tools.py `Tool.read_only`——加新写流程漏登任何
> 一处 = 静默失去确认门（mw.py 防御注释自认了这个坑）。
> **执行前提：P31 已收口上线（四 commit 19125c1..be87b13，tag
> `classic-pre-retirement`）**。**与 P32-memory-layer-upgrade（并行立项）
> 文件面交集 = mw.py**——不同区域（本票动写闸三件，P32 动
> AgentPromptMiddleware 的 mem_block），若两票并行执行须 pathspec 提交
> 且先 grep 交集确认无同函数改动，建议串行。

## 0. 拍板结论

| # | 问题 | 拍板 | 依据 |
|---|------|------|------|
| Q1 | 收编范围 | **hybrid**：高频流程（预约/请假四写 + query_venues/my_bookings 等高频读）保留专属 @tool；新增 run_flow + query_flows 作长尾与未来流程唯一入口；**迁移 leave_status 作样板**（低频/读操作/单参数，风险最小，验证「新流程=注册表一行」主张） | 专属工具的 schema 精确度与 docstring 引导在高频路径值回票价；收编的收益在长尾与扩展成本 O(1) |
| Q2 | 注册表统一 | **单一真相源**：tools.py `Tool` 扩为 `ToolSpec`（内嵌 flow 定义：required/optional/description/triggers/domain），`FLOW_DEFS` 与 `WRITE_TOOLS` 改为从注册表**派生**的视图，mw/agenttools 消费派生视图；txmeta.py 退为槽位解析器库（slot_meta/parse 函数） | 结构性消灭三源陷阱；加新流程只动一处，漏登不可能发生 |
| Q3 | 事件契约 | **PARITY 零改动**：run_flow 只是入口门，status/action_result/pending_action 事件的 `tool` 字段=实际 flow_id（确认卡片 label、回执、前端徽章、eval 断言全部照旧） | 契约稳定性优先；run_flow 是内部路由细节，不外泄到事件层 |
| Q4 | 闸的动态解析 | 共享 helper `resolve_flow(tool_call) -> ToolSpec \| None`：`name=="run_flow"` 时解开 args 取 flow_id 查注册表，否则按 name 直查；WriteSlotGate / HITL `when` 谓词 / write_call_ready 三处统一走它。缺 flow_id/未知 id → 闸放行、执行层 call_tool 返回 unknown_tool 回执（执行层仍是单一出口） | 闸从「工具名静态判定」升级为「解开参数动态判定」是本票唯一真正的机制变更 |
| Q5 | HITL 装配 | write_cfg 增 run_flow 单条目（when=resolve_flow 得 spec 且 `not read_only` 且参数齐）；高频写四件的专属条目原样保留，两形态并存无冲突 | 写确认门与入口形态正交——无论专属工具还是 run_flow 进入，确认门语义不变 |
| Q6 | 检索台阶 | **本票不做逻辑**、**数据先就位**：triggers（触发词数组）/domain/description 字段本次进注册表 schema；query_flows 首版=全量清单（按角色过滤）+ 可选 `q` 参数做注册表内包含匹配（零 FTS/向量依赖）。FTS/向量台阶触发条件留档 §5 | 流程 <10 时检索是过度设计；字段就位后将来加检索不动 schema |
| Q7 | 样板迁移方式 | leave_status 专属 @tool 从 tool_list 移除、注册表行保留——模型办理请假单查询只能走 query_flows→run_flow；事件 tool 字段不变，eval 无感 | 双入口（专属+run_flow 并存同一流程）会误导模型选择，样板必须单入口才真实验证路径 |

## 1. 目标 / 非目标

**目标**
- **O(1) 扩展主张成真**：新增一个办理流程 = 注册表一行 + business 函数，
  agenttools/mw/agent 零改动（以临时测试流程验证并留档论文素材）。
- 三源归一：写性/流程定义/权限单一真相源，派生视图消费。
- run_flow + query_flows 落地并全链真跑（收集→确认→执行→回执）。
- 样板迁移 leave_status，证明长尾路径质量不降。

**非目标**
- 不做 FTS/向量流程检索（触发条件见 §5）。
- 不迁移高频四写与高频读工具（对照期保留，退役判断留 §5）。
- 不改 business/db.py 业务逻辑与 PARITY 事件形状。
- 不动 RAG/联网工具（异构域，与本票判据无关）。

## 2. 设计与实现

### 2.1 P33-1 注册表统一（先行票，行为零变更）

- tools.py：`Tool` → `ToolSpec`（新增 `slots_required/slots_optional/triggers/
  domain/flow_desc` 字段，现有 8 工具补齐数据；`flow` 存在性即「流程工具」）。
- 派生视图：`flow_defs()`（name→flow 定义，供 mw/resume 消费）、
  `write_tools()`（read_only=False 且有流程定义，替代 mw.py:52 静态集合）。
- txmeta.py 收缩：FLOW_DEFS 常量删除，slot_meta/parse_slot/normalize_slot/
  build_confirm/SLOT_ORDER 保留为解析器库（消费 tools.py 数据）。
- 一致性测试：每个 ToolSpec——read_only=False ⇒ 必有 slots_required；
  triggers 非空；roles 非空（把曾经的「约定」变测试闸）。
- 验收：现有 8 工具行为零变更（全量单测绿即证）。

### 2.2 P33-2 run_flow + query_flows（机制票）

- **query_flows(q="", runtime=None) → str**：返回
  `{flow_id | label | 描述 | 必填槽位 | 所需角色}` 清单（按当前 role 过滤
  不可见项）；q 非空时对 label/description/triggers 做包含匹配。docstring
  引导：办理类诉求先查清单，选不准时**反问用户**而非猜。
- **run_flow(flow_id, args: dict, runtime=None) → Command**：
  - resolve_flow 查注册表——未知/缺失 flow_id → unknown_tool 回执；
  - 读流程：归一参数 → call_tool 执行 → action_result（tool=flow_id）；
  - 写流程：参数交 HITL（when 谓词动态判定），确认后同读路径执行。
  - args 形态：平铺 dict（与专属工具参数同名同义），槽位门按注册表
    required 逐项检查。
- **闸改造**：write_call_ready / WriteSlotGate / write_cfg when 统一改走
  resolve_flow；单测覆盖全分支（run_flow×读/×写/×未知 id/×缺 id/
  专属工具名直查不回归）。
- **样板迁移**：leave_status 专属 @tool 移除；eval 若有 leave_status
  用例真跑验证无感（事件同形）。
- AGENT_SYSTEM 补一条办理引导（何时 query_flows、何时直接用高频专属
  工具——列举保留的专属工具名防幻觉调用旧名）。

## 3. 风险与守卫

| 风险 | 守卫 |
|---|---|
| 闸静态→动态解析引入漏判（写流程绕过确认门） | resolve_flow 单测全分支 + 写流程经 run_flow 真跑两轮确认 + 取消路径；一致性测试（2.1）先行 |
| 模型幻觉调用已移除的专属工具名（leave_status） | 不在 tool_list=create_agent 层拒绝；执行层 unknown_tool 回执兜底；AGENT_SYSTEM 引导 |
| run_flow 通用 schema（args=dict）参数质量低于专属工具 | 样板选低频读操作；槽位门 + 确认卡片双重人眼兜底；高频不迁移（Q1） |
| 三源合并遗漏消费方（某处仍读旧常量） | 全局 grep FLOW_DEFS/WRITE_TOOLS 消费点清单化；ruff + 全量单测 |
| 与 P32-memory-layer 工作区冲突（mw.py 交集） | 串行执行优先；若并行=pathspec 提交+grep 交集函数确认无重叠 |
| eval 断言漂移 | 事件 tool=flow_id 契约（Q3）；leave_status 用例真跑对照 |

## 4. 验收门禁

- [ ] 单测全绿（注册表一致性 / resolve_flow 全分支 / run_flow 全链 /
      query_flows 角色过滤与 q 匹配 / 专属工具零回归）
- [ ] ruff + lint-arch + tsc + build + design-lint 全绿
- [ ] 真跑剧本：样板 leave_status 经 query_flows→run_flow 完成；高频
      预约照旧走专属工具两轮确认（含修改与取消）；**写流程经 run_flow
      同样两轮确认**（确认卡片 label 与专属路径一致）；学生经 run_flow
      调 approve_leave 收越权回执
- [ ] **O(1) 扩展验证**（论文素材）：临时测试流程仅加注册表行即可
      query_flows 可见、run_flow 可办、写流程确认门生效，全程零代码改动
      （验证后移除，过程留档 §6）
- [ ] 文档：architecture 05/06 补「同构收编 vs 异构检索」判据与注册表
      叙事；roadmap 勾选；任务书 §6 执行记录

## 5. 遗留与后续

- **检索台阶**（本票只就位数据）：流程 >50 或 query_flows 清单内误选
  可观测时，升级 q 匹配为 PG FTS（复用知识库机器）；流程描述语义相近
  难分时，再升级 pgvector 流程检索。三级台阶：查表→FTS→向量。
- **高频专属工具退役判断**：run_flow 路径质量经对照期（线上 trace 对比
  误选率/槽位准确率）证实时，专属工具可逐步收编；不建议本票动。
- **二义反问**：query_flows 返回多条相近流程时模型的反问行为，可在
  AGENT_SYSTEM 引导 + 真跑观察后决定是否加代码闸。
- tool retrieval（异构域）：工具总数逼近 ~20~30 时立项，与流程检索正交。

## 6. 执行记录（2026-10-02，两票全绿）

### 提交

| 票 | commit | 内容 |
| --- | --- | --- |
| P33-1 | fa12ff4 | 注册表统一：Tool→ToolSpec 内嵌流程定义（slots_required/slots_optional/triggers/domain，8 工具补数据）；FLOW_DEFS/WRITE_TOOLS 删净退役为 flow_defs()/write_tools() 派生视图；txmeta 收缩为解析器库；test_registry.py 一致性闸 + 派生视图与旧常量逐字等价锚定 |
| P33-2 | （本次） | resolve_flow/flow_args 闸动态解析 + write_call_ready 收紧为单谓词（HITL when+PendingAction 共用）+ write_cfg 增 run_flow 条目 + query_flows/run_flow 工具 + leave_status 样板迁移 + AGENT_SYSTEM 第 7 条 + resume 桥载荷解包 + test_run_flow.py 全链测试 + architecture 05/06 重写 + roadmap |

### 门禁

单测 286 全绿（P33-1 锚点 273 + 新增 13：注册表一致性/resolve_flow 全分支/flow_args 双形态/write_call_ready run_flow 形态/run_flow 全链 8 例/query_flows 角色过滤与 q 匹配/幻觉旧名优雅降级）+ ruff + lint-arch + tsc + build + design-lint 全绿。

### 真跑剧本（本地 8001，PG 5432，账号 p33run@qtu.edu.cn 新注册，SSE 逐事件核验）

1. **高频专属两轮确认**：submit_leave 请假（确认卡含天数/审批人推导）→ 确认 → 回执；book_venue 预约（模型自主先查 query_venues 余量）→「改成后天晚上」修改轮正确识别日期变更重发新卡 →「算了不约了」取消 → 落库验证为空。
2. **样板 leave_status**：「查请假单 LV-0001 审批到哪一步」→ 模型自主 query_flows → run_flow(flow_id=leave_status) → action_result tool=leave_status（PARITY 同形）→ route=factual 与专属时代一致。
3. **写流程经 run_flow 两轮确认**（与 O(1) 验证合并承载）：临时流程 book_venue_temp（见下）确认卡 label=快速预约场馆 → 确认 → 执行落库 → route=transaction；修改轮「改成周日晚上」resume 桥解包载荷正确识别槽位变更 → 新卡 → 确认 → 二次落库。
4. **越权回执**：学生 run_flow(approve_leave) → 确认卡同形（label=批准请假）→ 确认 → 执行层权限拦截「当前身份（学生）无权执行」success=False。学生 query_flows 清单本就无 approve_leave 行（角色过滤），模型会主动解释并拒绝绕权——真跑里先出现模型守规拒调，用参数级显式指令才完成机制验证（守卫语义双重成立）。
5. **O(1) 扩展验证**：tools_for() 仅加 book_venue_temp 一行（复用既有 _book 业务函数），agenttools/mw/agent 零改动——query_flows 可见、run_flow 可办、写确认门自动生效、修改轮/回执/路由全通，落库两笔核验。验证后移除该行，286 测试回归全绿。

### 撞坑留档

- **@tool 参数不可名 `args`**：pydantic schema 生成时被改写为 `v__args` 别名，运行时 TypeError。run_flow 槽位参数更名 `slots`（与注册表 slots_required 语言对齐）；flow_args/resume 解包双兼容 slots（主）+ args（容错）。
- **ToolMessage.name 由工具节点强制改写**：langgraph `_validate_tool_command` 对 Command 回传的 ToolMessage 一律 `message.name = call["name"]`——「ToolMessage.name=flow_id 供路由合成」方案不可行。改为 effective_route 从 AIMessage.tool_calls 解 flow_id 判写性（调用参数本就在消息轨迹里，更忠实）。
- **缺 flow_id 的 schema 拦截**：flow_id 必填时缺失调用被 ToolNode 参数校验拦截、到不了执行层 unknown 回执。flow_id 改缺省空串，让缺失 id 走执行层「未知流程」回执（Q4 机制口径）。
- **build_confirm 流程特有润色按 tool 键控**：场馆名回显（book_venue 分支）、请假天数/审批推导（submit_leave 分支）不随注册表自动继承——临时流程确认卡显示 venue_id 原值。O(1) 主张仍成立（机制链路全通），润色规则数据化（confirm_enrich 字段或按 domain 复用）留 §5 遗留。
- **模型行为观察**：① query_flows 单轮连调两次（flash 复读，只读无害未设闸）；② query_flows 清单作为 ToolMessage 留在会话历史，后续轮模型直接复用 flow_id 免再查（合法跨轮记忆，非幻觉）；③ 二义主动反问成立（「周五晚上」模型先反问是今天还是下周五）。
- **并行会话（P32 memory-layer）mw.py 交集**：P33-1 提交时 P32 未提交 hunks 与本票同文件不同函数——`git diff` 按 hunk 特征过滤后 `git apply --cached` 仅暂存本票 hunks，P32 改动原样留在工作区（其提交 4a1bf09/93158c5 后核实无互相卷带）。P33-2 提交时 P32 已入库，无需再筛选。

### 遗留确认（对应 §5）

- 检索台阶三级（查表→FTS→向量）触发条件未变；triggers/domain 数据已全量就位。
- 高频专属工具退役判断：待 run_flow 路径线上 trace 对照（误选率/槽位准确率），本票未动。
- build_confirm 润色数据化、query_flows 复读闸、二义反问代码闸：真跑观察到的三个小改进点，均非阻塞。
