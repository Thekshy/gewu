# 06 · 知行执行层（transaction）

「格物」负责让学生知道（信息问答），「知行」负责让学生办成（业务执行）。核心原则（PARITY §9）不变：**读操作直接执行；写操作必须经过「确认摘要 → 用户确认 → 执行 → 回执」**。P31-2 起 classic 手写办理图（entry_gate/advance/tx_confirm/tx_gate/tx_resume 节点族）已全量退役——办理不再是独立图，而是 **agent 主循环内的工具调用**：槽位收集=WriteSlotGate 拦截追问、确认=PendingAction 摘要 + HITL 中断（[05](05-react-agent.md)）、执行=call_tool 单一出口。P33 起办理流程进一步收编为**注册表（单一真相源）+ run_flow / query_flows 统一入口**。

## 办理流程注册表（P33 单一真相源）

注册表即工具表本体：`tools.py` 的 `ToolSpec` 一行内嵌该流程的全部元数据——

| 字段 | 承载 | 消费方 |
| --- | --- | --- |
| `roles` / `read_only` | 权限矩阵、读写属性 | `call_tool` 单一出口、路由合成 |
| `slots_required` / `slots_optional` | 流程定义（label 取自 `label` 字段） | 槽位门、确认摘要、resume 桥 |
| `triggers` / `domain` | 触发词与业务域（检索台阶数据，Q6 只就位） | `query_flows` q 匹配；将来 FTS/向量不动 schema |

`slots_required` 非空即「流程工具」。历史上写性判定存在**三个真相源**（mw.py `WRITE_TOOLS` 静态集合、txmeta.py `FLOW_DEFS`、tools.py `read_only`），加新写流程漏登任何一处 = 静默失去确认门——P33 结构性消灭：后两者退役为**派生视图** `flow_defs()` / `write_tools()`，全部实时读注册表，一致性测试闸（`test_registry.py`）保证「写工具必有流程定义」「triggers/roles 非空」等约定。**新增一个办理流程 = 注册表一行 + business 函数，agenttools/mw/agent 零改动**（O(1) 主张已真跑验证：临时流程仅加注册表行，query_flows 可见、run_flow 可办、写确认门自动生效，验证后移除）。

## 两个入口：专属工具与 run_flow（同构收编判据）

「同构收编、异构检索」：办理流程是**同构**的（槽位收集→确认→执行→回执），最优扩展形态不是更多工具，而是收编为 `run_flow(flow_id, slots)` + `query_flows(q)` 两个入口 + 注册表数据行；知识/联网检索是**异构**域（返回证据不产生回执），不上 run_flow，将来工具总数逼近 ~20~30 才立项 tool retrieval（与流程检索正交）。

- **高频保留专属 @tool**（Q1 hybrid）：query_venues/my_bookings/pending_leaves + 写四件 book_venue/cancel_booking/submit_leave/approve_leave——专属 schema 的参数精确度与 docstring 引导在高频路径值回票价；
- **leave_status 样板迁移**（Q7）：专属 @tool 移除、注册表行保留——请假单查询只能走 query_flows→run_flow，事件 `tool` 字段不变（`leave_status`），eval 无感；
- **query_flows**：全量清单（flow_id｜名称｜说明｜必填槽位｜所需角色），按当前角色过滤不可见项；可选 `q` 做包含匹配（id/名称/说明/触发词，零 FTS/向量依赖）。检索台阶三级：查表 → PG FTS（流程 >50 或误选可观测）→ pgvector（语义难分），本票只就位数据；
- 事件契约 **PARITY 零改动**（Q3）：`pending_action`/`action_result` 的 `tool` 字段=实际 flow_id，确认卡片 label、回执、前端徽章、eval 断言与专属路径完全同形。

## 槽位系统（解析器库 txmeta.py）

每个槽位有 label/ask 文案与**确定性解析器**（`txmeta.slot_meta`，文案逐字保留 PARITY §9.3 表格）：

| 槽位 | label | 追问文案（节选） | 确定性解析 |
| --- | --- | --- | --- |
| `venue` | 场馆 | 想预约哪个场馆？可选：羽毛球馆、篮球场、研讨间301/302 | 场馆名 → venue_id（查业务库） |
| `date` | 日期 | 预约哪一天？（如：明天、周三、9月2日） | `dates.parse_iso`（周几/相对日/月日全格式） |
| `slot` | 时段 | 可选 5 档；也可回复上午/下午/晚上 | 标准段直接匹配；「晚上七点」→19:00-21:00；上午/傍晚等多选词**不命中**（须指明） |
| `purpose` / `reason` | 用途/事由 | 自由文本 | 原样保留（不猜测） |
| `leave_type` | 类型 | 事假 / 病假 / 其他 | 词表匹配 |
| `start_date` / `end_date` | 起止日期 | 如：明天、下周一（含当天） | 同 `date` |
| `booking_id` / `ticket_id` | 单号 | 形如 VE-0001 / LV-0001 | 正则抽取 |

**LLM 给的原始参数一律过解析器归一**（`normalize_tool_args` → `normalize_slot`，call_tool 之前）——日期换算、场馆名→ID、时段口语→标准段都在确定性代码里完成。分工即：**LLM 找表述，代码算日期**——「下周三到底是哪天」LLM 极易算错，工具描述强制日期参数传**中文原文**、禁止自行换算。原 `FLOW_DEFS` 常量已退役：流程必填/可选定义改由 `flow_defs()` 派生视图供给，txmeta 收缩为纯解析器库（slot_meta/parse_*/build_confirm/SLOT_ORDER）。

## 闸动态解析（resolve_flow，Q4 机制变更）

写性判定从「工具名静态集合」升级为「解开参数动态判定」，共享 helper `tools.resolve_flow(tool_call) -> ToolSpec | None`：

- `name=="run_flow"` → 解开 `args.flow_id` 查注册表；专属工具名 → 按 name 直查——两形态经同一判定收敛；
- 配对 helper `flow_args(tool_call)` 归一槽位参数形态（专属=args 原样；run_flow=解出内层 slots，兼容 args 键——@tool 参数不可名 `args`，pydantic schema 会改写为 `v__args`）；
- `write_call_ready` 单谓词承载三处：HITL `when`、PendingAction 摘要发射、（语义反向的）WriteSlotGate——就绪 = 有流程 spec ∧ 写流程 ∧ 必填参数齐。读流程/非流程/未知或缺失 flow_id → False：HITL 跳过中断（读流程经 run_flow 直执行），槽位门收集缺参；
- **缺 flow_id/未知 id → 闸放行**，执行层 run_flow 返回 unknown 语义回执（「未知流程：…请先用 query_flows 查询」）——执行层仍是单一出口；模型幻觉调用已退役专属名（leave_status）由工具节点兜底错误 observation，图不崩、业务层零执行。

## 确认门与 resume 桥

写流程调用（无论专属工具还是 run_flow 进入，确认门语义不变——Q5 两形态并存无冲突）：

1. 参数不齐 → WriteSlotGate 拦截：emit slot_question + 引导模型向用户收集；
2. 参数齐 → PendingActionMiddleware 发确认摘要（`build_confirm` 产 pending_action + 文案；请假自动补「共 N 天 / X 审批」与病假附证明提示；预约回显场馆名）→ HITL `interrupt(HITLRequest)` 暂停（checkpointer 落盘，中断前零副作用）；
3. 用户回复经 `resume.py` 翻译为 decisions：approve=确认 / reject=取消 / respond=修改重发或切话题（`classify_reply` LLM 优先启发式兜底）；run_flow 中断载荷在桥内**解包**（tool 还原 flow_id、slots 取内层）后走同一套槽位修改检测；
4. 放行执行 → action_result 回执（`tool` 字段=flow_id）→ effective route 合成（写流程=transaction/hybrid；run_flow 的写性从 AIMessage.tool_calls 解 flow_id 判定——工具节点会把回执 ToolMessage.name 统一改写为 run_flow，框架强制）。

**模型可发起写操作，不能拍板**——确认摘要/槽位元数据由确定性代码产出（classic tx_confirm 的语义承继），前端零改动。

## 失败恢复（回执驱动）

业务层返回结构化错误（`business.Result`）：

| 字段 | 含义 | 恢复动作 |
| --- | --- | --- |
| `ok` / `message` | 结果与人话描述 | 直接进回执，模型向用户转述 |
| `err` | invalid / quota / conflict / not_found / permission / missing_arg / unknown_tool | 非成功即 success=false 回执 |
| `field` | 字段级失败标记 | 冲突时 `alternatives` 拼进 message（「可选时段：…」），模型引导用户改参数重发 |
| `receipt` | VE-XXXX / LV-XXXX | 成功回执凭证号 |

classic 时代的「tx_phase 字段级回退状态机」已随节点族退役；agent 时代的恢复 = **回执是有效 observation**：模型拿到冲突/缺参回执后向用户说明可选项、重新发起调用（缺必填参数另有槽位门硬拦截兜底）。权限失败（学生调 approve_leave）同样以越权回执交模型转述（tx-006 断言语义）。

## 权限矩阵与确定性

- **权限在工具层不在业务系统**：business 对角色无感知，判定收敛在 `agent.tools.call_tool` 单一出口——专属工具、run_flow、未来任何入口都绕不过这道闸：

```python
def call_tool(tools, business, name, args, role, user) -> Result:
    t = tools.get(name)
    if t is None:
        return Result(err="unknown_tool", message=f"未知工具：{name}")
    if not has_role(t.roles, role):
        return Result(err="permission",
            message=f"当前身份（{role_label(role)}）无权执行「{t.label}」")
    return t.fn(business, args, user)
```

学生调 `approve_leave`/`pending_leaves`（counselor 专属）被明确拒绝，评测集有专项用例；query_flows 清单同步按角色过滤（学生看不见 approve_leave 行）。

- **业务规则与语料一致**：请假 1—3 天辅导员批、3 天以上 7 天以内学院批、超过 7 天教务处批（`approver_of`）；场馆每时段容量（羽毛球馆 2 组/篮球场 1 组/研讨间各 1），「每人每天 2 时段」配额在业务库判定——`gewu/business/db.py`（P21-2 起 PG；`user` 列在 PG 为保留字，SQL 内一律双引号）。

## 相关文件

| 文件 | 职责 |
| --- | --- |
| `gewu/agent/tools.py` | **注册表单一真相源**（ToolSpec+流程定义）与 call_tool 权限出口；resolve_flow/flow_args 闸解析 |
| `gewu/agent/agenttools.py` | @tool 工具族：query_flows/run_flow 统一入口 + 专属工具薄包 |
| `gewu/agent/txmeta.py` | 槽位解析器库（slot_meta/normalize_slot/build_confirm/SLOT_ORDER） |
| `gewu/agent/mw.py` | WriteSlotGate/PendingAction 中间件与 write_call_ready 谓词 |
| `gewu/agent/resume.py` | HITL 决策翻译（含 run_flow 载荷解包） |
| `gewu/business/db.py` | 业务全量（场馆/预约/请假单、Result 结构） |
| `gewu/dates.py` | 确定性中文日期解析（UTC+8） |
| `tests/` | test_registry.py / test_run_flow.py / test_tx.py / test_agent_mw.py / test_business_write.py |

---

下一篇《07 · 状态与持久化》盘点四类状态的生命周期：PG checkpoints、业务/记忆表与每日预算。
