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

- [ ] 单测全绿（会话 CRUD/归属 404/messages 提取/连带删除/记忆 CRUD）
- [ ] ruff + lint-arch 全绿
- [ ] 前端 tsc + next build + design-lint **五页**（含 /memory）全绿
- [ ] 真跑剧本（§2.5）全过，PG 三表行数断言留档
- [ ] PARITY §0.6 / architecture 07+11 / roadmap（P22 + 记忆遗留双勾）/ §6 更新

## 5. 遗留与后续

- 事件级历史重建（引用/步骤卡片恢复）：观察需求，暂不做（对话级够用）。
- LLM 会话命名 / 记忆去重整理 / episodic 可见化：P23 后观察。
- 会话分页：内测量级不需要（列表 updated_at 倒序全量）。
- P23 管理后台在其后立项（用户/用量/邀请码 UI/per-user 预算/会话巡查）。

## 6. 执行记录

（待执行后回填）
