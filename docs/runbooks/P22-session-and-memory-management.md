# P22 会话与记忆管理：会话 CRUD + 历史恢复 + memory_fact 可见可管（任务书）

> **背景**：P21 用户体系落地后，身份/归属/权限的地基已就位（cookie 会话、
> role 服务端权威、台账与记忆按 email 归属）。但「会话」仍是**客户端自报的
> 裸 uuid**（前端 useRef 刷新即丢、无列表/改名/删除），长期记忆**仅注入不可
> 管理**（roadmap 遗留项「长期记忆消亡与用户侧可见性」）。本票把会话升格为
> 服务端资源（CRUD 一套），并给 memory_fact 用户可见可管的面板——用户级
> 记忆从「黑盒增强」变「透明资产」。
>
> 编号说明：P22 承接 P21 三票制的第二票；P23（管理后台）在其后。

## 0. 拍板结论

| # | 问题 | 拍板 | 依据 |
|---|------|------|------|
| Q1 | 会话归属机制 | **服务端显式创建**：`POST /api/sessions` 下发 session_id；`/api/chat` 只认已登记且属本人的 session_id（否则 404） | 会话成为资源，CRUD 语义完整；侧边栏体验自然（新建即入列）；compare 双轨适配为 kind=compare（不入对话列表） |
| Q2 | 历史消息恢复 | **完整恢复**：`GET /api/sessions/{id}/messages` 从 checkpointer state 提取对话文本序列，前端切换/刷新后恢复 | 对话级纯文本渲染（事件级细节不恢复）；体验完整闭环「刷新不丢」 |
| Q3 | 记忆面板位置 | **独立页 `/memory`**：nav 加第四项「记忆」 | fact 查看/编辑/删除/新增一页承载；design-lint 检测页 +1（四页→五页） |
| Q4 | 删除会话连带 | **删干净**：checkpointer thread + memory_episodic（按 session_id）+ chat_sessions 行三处连带 | 用户预期「删除=清掉」；顺序=先 checkpointer 后业务行（跨连接非事务，见 §3） |
| Q5 | 会话标题 | **首问截断回填**（前 20 字，chat 端点首见空 title 时 `UPDATE ... WHERE title=''`），PATCH 可改名 | 不引 LLM 命名（简单优先、零成本零延迟）；幂等条件更新天然防并发覆盖 |

## 1. 目标 / 非目标

**目标**

- PG 新表 `chat_sessions`（幂等 DDL，auth/business 同款惯例）；对外用户键
  沿用 email（注意 `user` 保留字双引号，P21 首坑）。
- 会话五端点（均需登录，本人视角）：
  - `POST /api/sessions`：创建（可带 `{kind}` 缺省 chat）→ `{session_id, title, kind, created_at}`
  - `GET /api/sessions?kind=chat`：本人会话列表（kind 过滤，updated_at 倒序）
  - `PATCH /api/sessions/{id}`：改名 `{title}`（1~60 字）
  - `DELETE /api/sessions/{id}`：三处连带删除（Q4）
  - `GET /api/sessions/{id}/messages`：历史恢复（Q2）
- `/api/chat` 归属校验：session 必须存在且 `"user"=登录 email`，否则 **404**
  （不泄露他人会话存在性）；`"default"` 缺省值废弃（未传 session_id=422）；
  首条消息回填 title（Q5）+ 每轮刷 updated_at。
- 记忆三端点（本人视角，MemoryStore 已有读写面按需薄封）：
  `GET /api/memory/facts`（列表）、`POST /api/memory/facts`（upsert：
  `{kind,key,value}`，kind ∈ profile|preference|constraint）、
  `DELETE /api/memory/facts?kind=&key=`。
- 前端侧边栏（对话页）：桌面左栏（约 260px）+ 移动端轻量抽屉（自写，零新
  依赖）；新建/切换/改名/删除；当前会话 id 持久 localStorage（刷新不丢）；
  切换会话拉取历史恢复渲染（纯文本对话级 + RouteBadge 若 state 可取）。
- 前端 `/memory` 页 + nav 第四项：fact 表格（kind 分组）、inline 编辑、删除
  确认、新增表单。
- compare 页适配：两轨首次发送前各 `POST /api/sessions {kind:"compare"}`，
  会话 id 改用服务端下发值。

**非目标**

- 不做事件级历史重建（citations/steps/action 卡片不恢复，纯文本对话级）。
- 不做 LLM 会话命名 / 记忆自动整理（P23 后观察）。
- 不做 memory_episodic 的用户界面（随会话删除连带清理即可；fact 面板够闭
  roadmap 遗留）。
- 不做会话搜索/置顶/归档；不做跨设备会话同步推送（列表每次拉取）。
- 不动编排与 SSE 契约（chat 只加归属校验与 title/updated_at 副作用）。

## 2. 设计与实现

### 2.1 会话域（P22-1）

新表（`gewu/session/store.py`，或并入 auth 域旁新包——遵守 lint-arch 支撑域
规则，HTTP 层挂 `gewu/api/sessions.py`）：

```
chat_sessions(
    session_id TEXT PRIMARY KEY,          -- 服务端 secrets.token_urlsafe(16)
    "user"     TEXT NOT NULL,             -- email（保留字双引号）
    title      TEXT NOT NULL DEFAULT '',
    kind       TEXT NOT NULL DEFAULT 'chat' CHECK (kind IN ('chat','compare')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_chat_sessions_user ON chat_sessions("user", updated_at DESC);
```

- 归属校验在 chat 装配点（`api/chat.py`）：一条 SELECT，404 早退；
  title 回填与 updated_at 同轮 UPDATE。
- **硬点一：messages 提取**。`graph.get_state(config).values["messages"]` →
  过滤规则：只取 HumanMessage 文本与 AIMessage 非空 text；工具调用轮
  （AI 无 text 只有 tool_calls）与其 ToolMessage 跳过（对话级视图无噪声）；
  system 消息跳过。输出 `[{role:"user"|"assistant", text}]`。interrupt 悬停
  态（next 非空）正常返回已有消息（get_state 天然支持，验证点）。
- **硬点二：checkpointer 删除**。优先 `PostgresSaver.delete_thread(thread_id)`
  （验证 langgraph-checkpoint-postgres 版本是否有此 API）；无则 SQL 直删三表
  `checkpoints/checkpoint_blobs/checkpoint_writes WHERE thread_id=%s`（与
  checkpointer 同 DSN 的独立连接）。
- 单测：创建/列表（kind 过滤+本人隔离）/改名（越权 404）/删除三处连带
  （checkpointer 行数断言 + episodic 断言）/messages 提取（含工具轮过滤、
  interrupt 态）/chat 归属 404 与 default 废弃 422。

### 2.2 记忆端点（P22-2）

- `MemoryStore` 增薄方法：`all_facts(user_id)`（全量，按 kind,key 排序）、
  `delete_fact(user_id, kind, key)`；`upsert_facts` 复用（单条即可）。
- 端点全登录态；DELETE 用 query 定位复合主键。防越权由 user_id=email 保证。
- 单测：CRUD 全路径 + 未登录 401 + 他人不可见。

### 2.3 前端侧边栏与历史恢复（P22-3）

- 布局：`app/page.tsx` 改双栏——`md:grid md:grid-cols-[260px_1fr]`；移动端
  顶栏汉堡按钮开抽屉（fixed + backdrop，自写，零依赖）。侧栏新组件
  `components/session-list.tsx`。
- 数据流：`lib/api.ts` 增 createSession/listSessions/renameSession/
  deleteSession/fetchMessages + memory 四函数；页面加载=拉列表+localStorage
  读 current id（无则 create 新会话并写入）。
- 切换会话：setMessages(历史) + sessionId.current=id；恢复渲染复用现有
  user/assistant 气泡结构（`done:true` 静态态），不渲染事件级卡片。
- 「新对话」按钮：create → 清屏 → current 更新（侧栏即时出现，title 空
  显示「新对话」占位，首轮后回填）。
- 删除：AlertDialog 确认 → delete → 若删的是当前会话则切到列表首个或新建。
- compare 页：两轨 send 前 ensure sessions（useRef 缓存创建结果，kind=compare）。
- **DESIGN.md 纪律**：侧栏是导航 chrome（sans、hairline 分隔、无卡片嵌套）；
  design-lint 四页基线必须保持零 finding。

### 2.4 /memory 页（P22-4）

- `app/memory/page.tsx`：`useRequireUser` 守卫；fact 表格按 kind 分三组
  （身份/偏好/约束），行内编辑（Input + 保存/取消）、删除确认、底部新增
  表单（kind/key/value）；空态文案说明「事实由对话中自动抽取，也可手动
  维护；每轮对话注入上下文」。
- `components/nav.tsx` 加「记忆」；design-lint 检测页清单四页→五页。

### 2.5 门禁与文档（P22-5）

- 全绿：ruff / pytest / lint-arch / tsc / next build / design-lint（五页）。
- 真跑剧本（§6 留档）：登录 → 新建会话 → 对话（侧边栏出现+title 回填）→
  刷新页面历史恢复 → 改名 → 新建第二会话切换 → 删除第一会话（PG 三表行数
  断言）→ /memory 增改删 → 未登录 curl 401/404。
- 文档：PARITY §0.6（新端点 + chat session 语义变更）；architecture
  07（会话状态加 chat_sessions 一行）/11（记忆可管注记）；roadmap 勾选 P22
  与「长期记忆可见性」遗留闭线；本任务书 §6。

## 3. 风险与守卫

| 风险 | 守卫 |
|---|---|
| checkpointer 删除跨连接非事务（业务行删了 cp 删失败=悬空检查点） | 顺序=先 cp 后业务行；悬空检查点无害（无入口可达），留档说明 |
| messages 提取踩 langgraph 消息类型分支（AI text 空/tool_calls/ToolMessage） | 提取函数独立单测（构造含工具轮的 thread）；真跑含 interrupt 态验证 |
| default 缺省值废弃破坏 curl 直调旧习惯 | PARITY §0.6 显式契约变更；错误体给指引文案（「请先 POST /api/sessions」） |
| 侧边栏/抽屉引入 design-lint finding（nested-cards/h-screen） | 导航 chrome 用 hairline；检测前自跑五页；新页面入检测清单 |
| localStorage 持久 current id 指向已删会话 | 加载时列表校验，无效则丢弃重建 |
| Node25 localStorage dev 坑 | dev script 已固化（P16），照用 |
| title 回填并发覆盖用户改名 | `UPDATE ... WHERE title=''` 条件更新，改名后不再回填 |
| compare 双轨创建时序（首跑前 ensure） | useRef 缓存 + send 前await；失败即报错停止双发 |

## 4. 验收门禁

- [x] 单测全绿（会话 CRUD/归属 404/messages 提取/连带删除/记忆 CRUD）——212 passed
- [x] ruff + lint-arch 全绿
- [x] 前端 tsc + next build + design-lint **五页**（含 /memory）全绿
- [x] 真跑剧本（§2.5）全过，PG 三表行数断言留档（§6.3：12|8|36|4|1 → 0|0|0|0|0）
- [x] PARITY §0.6 / architecture 07+11 / roadmap（P22 + 记忆遗留双勾）/ §6 更新

## 5. 遗留与后续

- 事件级历史重建（引用/步骤卡片恢复）：观察需求，暂不做（对话级够用）。
- LLM 会话命名 / 记忆去重整理 / episodic 可见化：P23 后观察。
- 会话分页：内测量级不需要（列表 updated_at 倒序全量）。
- P23 管理后台在其后立项（用户/用量/邀请码 UI/per-user 预算/会话巡查）。

## 6. 执行记录

**执行日期：2026-10-01（当日立项当日执行完毕）。**

### 6.1 交付清单（对照 §2 ticket）

- **P22-1 会话域**：`gewu/session/store.py`（SessionStore：幂等 DDL + create/get/
  list/rename/delete/note_turn，`"user"` 保留字全程双引号）+ `gewu/api/sessions.py`
  （五端点，越权/不存在统一 404）。chat 装配点（`api/chat.py`）：归属校验 SELECT
  早退 + `note_turn`（title 首问 20 字 CASE 条件回填 + updated_at 同轮刷新，
  一条 UPDATE）；session_id 必填（422 带指引文案）、`"default"` 废弃。
- **硬点一（messages 提取）**：`extract_dialog_messages()` 独立纯函数——
  HumanMessage 文本 + AIMessage 非空 text（兼容 str 与多模态 list content），
  工具调用轮/ToolMessage/system 跳过；interrupt 悬停态天然支持（get_state
  只读 values，不依赖 next）。**实现偏离拍板一处**：classic 链路（mode=
  classic/direct/research）不写 messages（mode_dispatch 只把 auto/react 送进
  agent 子图），提取为空时**退 memory_episodic 兜底**（`episodes_for_session`，
  每轮 user/assistant 双条、链路无关）——否则强制直答/研究轮刷新即丢，违背
  Q2「完整恢复」的拍板精神。真跑双路径各验一次（§6.3）。
- **硬点二（checkpointer 删除）**：langgraph-checkpoint-postgres 3.1.2 有原生
  `PostgresSaver.delete_thread(thread_id)`（MemorySaver 同名 API），直接采用，
  无需 SQL 直删三表的回退分支。删除顺序=先 cp 后 episodic 后业务行。
- **P22-2 记忆端点**：MemoryStore 增 `all_facts`/`delete_fact`/`episodes_for_session`/
  `delete_episodes` 四薄方法；`gewu/api/memory.py` 三端点（kind ∈ profile|
  preference|constraint，key ≤60、value ≤500）。
- **P22-3 前端**：`app/page.tsx` 双栏（`md:` 左栏 260px 常驻 + 移动端 fixed 抽屉
  自写零依赖，sticky「会话」入口）；`components/session-list.tsx`（导航 chrome：
  sans/hairline/无卡片嵌套，行内改名 + AlertDialog 删除确认）；`lib/api.ts` 增
  会话五函数 + 记忆三函数，`streamChat` 的 sessionId 改必填；localStorage 持久
  current id（`gewu.current-session`，加载时列表校验无效丢弃重建）；发送完成
  刷侧栏（title 回填即时可见）。compare 两轨首跑前 `ensureSession`（kind=
  compare，useRef 缓存创建结果，失败即报错停双发）。
- **P22-4 /memory 页**：kind 三分组面板（身份/偏好/约束）+ 表格行内编辑 +
  删除确认 + 底部新增表单（upsert 语义说明）；nav 第四项；design-lint 检测
  清单四页→五页。
- **P22-5 门禁与文档**：见 §6.2–6.4。

### 6.2 门禁结果（全绿）

| 门禁 | 结果 |
| --- | --- |
| pytest | **212 passed**（191→212，+21：会话 9 + 记忆 4 + chat 归属/422 新 2 + 既有 6 文件适配 sess/cp 夹具） |
| ruff check + format | 全绿 |
| lint-arch | 全绿（新增规则：session 支撑域 ↛ 业务域；sessions/memory 路由入禁 llm 清单） |
| tsc --noEmit | 全绿 |
| next build | 全绿（/memory 入路由清单） |
| design-lint **五页** | 全绿零 finding（侧栏/抽屉/记忆页未引入 nested-cards/h-screen/衬线泄漏） |

测试侧结构变化：`create_app` 增 `sessions`/`checkpointer` 可注入参数（此前
checkpointer 不可注入，连带删除的行级断言无法做）；conftest 增 `sess`/`cp` 夹具
（`cp` = PostgresSaver 连测试库 + 每用例清 checkpoints 三表归零）；test_chat_api
的 make_client 注册后预建固定 id 会话（s1/t/u/e）。

### 6.3 真跑剧本（§2.5 全过）

后端 curl（uvicorn :8000 真跑，账号 p22test@qtu.edu.cn）：

1. 注册登录（邀请码 `make invite`）→ me 200。
2. `POST /api/sessions` → `{session_id:"5ZIq…", title:"", kind:"chat"}`。
3. 对话（direct 真跑 LLM）：route→154×answer_delta→citations→done；列表 title
   回填「图书馆开放时间是什么？」、updated_at 刷新。
4. 历史恢复双路径：classic 会话（direct）→ episodic 兜底 2 条；agent 会话
   （auto，6 个 SSE 事件含工具轮）→ checkpointer 提取 2 条（工具轮被滤掉）。
5. PATCH 改名「图书馆咨询」；**改名后再对话 title 不被覆盖**（CASE 条件生效），
   updated_at 倒序正确。
6. 删除第一会话：删前 PG `12|8|36|4|1`（checkpoints/blobs/writes/episodic/
   chat_sessions）→ 删后 **`0|0|0|0|0`**，messages 端点 404，第二会话存活。
7. /memory：新增 2 条→upsert 覆盖（major 计算机科学→软件工程）→列表按
   kind,key 排序→删除 200/重复删 404。
8. 未登录：sessions/memory 401；chat 未登记 session 404、未传 422 带指引。

前端浏览器（next start 生产构建 :3200 + Chrome）：

- 登录 → 首载自动新建会话入列；点击「借书最多能借几本？」切换 → 历史完整
  恢复渲染（markdown）；**reload 后仍在该会话且历史恢复**（localStorage 持久，
  「刷新不丢」闭环）。
- 「新对话」即建即列清屏；删除当前会话 → AlertDialog 确认 → 三处连带 → 自动
  切到列表首个。
- /memory 页：编辑保存（major→计算机科学与技术）、新增（preference/sport/
  羽毛球）、删除确认弹窗（关闭并执行）全过。
- 布局目检：桌面亮/暗双主题（左栏 260px + 主区层次清晰）、移动端 390px
  （抽屉 backdrop + 内切换自动关闭）截图留档。
- compare 页适配：代码路径（ensureSession + reply 续轮传 session）经 tsc/build
  验证，同题双发链路与 P17 相同未再全跑。

### 6.4 撞坑记录（对后来者有值）

1. **函数内局部 import 遮蔽全局名**（chat.py）：在函数中段新写 `raise
   HTTPException(404)`，而函数后段预算分支里有一处 P14 时代遗留的
   `from fastapi import HTTPException` 局部导入——Python 把 HTTPException
   判为局部变量，首行引用即 UnboundLocalError。修法：删局部导入（顶部已
   import）。测试 `test_chat_session_id_required_and_registered` 当场抓住。
2. **残留 pytest 进程持锁**：真跑前发现全量 pytest 「挂起」——旧的后台
   `pytest -q` 进程持有 conftest 的 pg_advisory_lock(941012)，新进程无限等锁。
   `pgrep -fl pytest` 找到 kill 后秒过。后台长测要先查锁持有者。
3. **8000 端口旧 server 残留**：真跑首请求 `/api/sessions` 404——旧版 uvicorn
   还占着 8000（新实例 bind 失败但 `&` 后台静默）。`lsof -ti tcp:8000` +
   kill 重起。契约类真跑先打一发新端点确认跑的是新代码。
4. **compare 页 AlertDialogTrigger 是 BaseUI `render` 形态**而非 shadcn 的
   `asChild`（P16 迁移后的既有约定，console 页有现成范式）；确认按钮用
   `AlertDialogCancel variant="destructive"`（Close 原语：关窗并执行）。
5. **多测试文件连锁适配**：`create_app` 新增 SessionStore 后，五个测试文件的
   make_client 不传 `sessions` 会连 5433 真库（无退路，不同于 checkpointer 的
   MemorySaver 回退）——统一注入 `sess` 夹具；跨行函数签名的批量正则漏改导致
   `sess` 进了 `usage` 位置参数（TypeError 多值），逐文件核对修复。

### 6.5 遗留

- 事件级历史重建（引用/步骤卡片恢复）：不做（§5 原判断维持）。
- compare 会话不入对话列表但不提供清理入口：内测量级小，PG 直清即可，P23
  会话巡查顺带。
- localStorage 持久 current id 在**跨设备**场景不同步（每设备首次各自新建）：
  列表每次拉取，属预期行为非缺陷。
