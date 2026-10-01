# P23 管理后台：用户/用量/会话巡查 + 邀请码发放 UI + per-user token 预算（任务书）

> **背景**：P21 认证地基（role 服务端权威 + 邀请码）与 P22 会话资源化之后，
> 管理面仍是 CLI（`make invite`/`make admin`）+ console 页的台账重置一角；
> token 预算只有**全局单闸**（data/usage.json），无按用户记账——公网部署的
> 「防配额滥用」只防了一半（一个用户可吃光全员预算）。本票补齐管理后台：
> admin 一页可见可管用户/邀请码/会话/用量，token 记账按用户落 PG 并配
> per-user 限额闸。
>
> 编号说明：P23 承接 P21 三票制的第三票；P24（线上观测）已并行执行完毕。

## 0. 拍板结论

| # | 问题 | 拍板 | 依据 |
|---|------|------|------|
| Q1 | per-user 记账怎么贯通 | **contextvar**：`gewu/usage.py` 定义 `current_user` ContextVar，chat 入口 set(email)，LLMService 记账口读后双写（全局 budget 不变 + per-user PG） | 记账口三处（`_record`/`record_usage`/`ChatStreamResult`）签名零变化；同线程传播天然覆盖 graph 迭代与 agent 子图 |
| Q2 | per-user 限额形态 | **users 加列 `daily_token_limit`**（BIGINT NULL，NULL=用 `DAILY_USER_BUDGET` 全局缺省 20 万）；chat 入口全局闸之后再 per-user 闸（429 文案区分） | 语义完整（admin 可差异化）且不引新表；全局闸保留=公网兜底不回归 |
| Q3 | 会话巡查深度 | **列表级**：admin 可看全量会话（user/title/kind/updated_at）与删除（三处连带）；**不开放他人会话内容 UI** | 隐私默认保守；内测量级 DB 直查够用；删除能力满足清障需求 |
| Q4 | 用户管理边界 | PATCH `{role?, status?, daily_token_limit?}`；**不能改自己的 role/status**（防唯一 admin 锁死；limit 可改自己） | 最小守卫防呆；disable 后 cookie 立即失效（user_for_token 已过滤 status，零新代码） |
| Q5 | 邀请码发放 | POST `/api/admin/invites`（uses/days/note）复用 `AuthStore.create_invite`（UPDATE..RETURNING 原子核销不变）+ GET 列表（含 used_count/expires_at） | CLI 语义平移到 UI；`make invite` 保留 |
| Q6 | 前端形态 | 独立页 `/admin`（console 不动）；nav 第五项「管理」**仅 admin 渲染**；design-lint 检测页五→六 | 管理域与用户域分页清晰；隐藏不可达入口 |

## 1. 目标 / 非目标

**目标**

- 新支撑域 `gewu/usage.py`：`UsageStore`（PG 表 `token_usage(user_id, day, tokens)`
  PK(user_id,day)，UPSERT 累加）+ `current_user` ContextVar；LLMService 三记账口
  双写（usage store 由装配注入，None=不记，测试替身零改动）。
- per-user 限额闸：`users.daily_token_limit` 列（幂等 DDL 加列）+ chat 入口
  `ensure_user(email)`（超限 429「今日个人 token 预算已用尽（上限 N）」）。
  异步 consolidate 线程显式带 user（`copy_context` 或线程内 set）。
- admin 八端点（全部 require_admin）：
  - `GET /api/admin/stats`：用户/会话/邀请码数 + 今日全员 token + 全局预算水位
  - `GET /api/admin/users`：用户列表（role/status/created/last_login/limit/today_tokens）
  - `PATCH /api/admin/users/{email}`：改 role/status/daily_token_limit（Q4 守卫）
  - `GET/POST /api/admin/invites`：列表 / 发放
  - `GET /api/admin/sessions?kind=&q=`：全量会话巡查（q 按 user/title 模糊）
  - `DELETE /api/admin/sessions/{id}`：删任意会话（三处连带，复用 P22 顺序）
  - `GET /api/admin/usage?days=7`：按日聚合 + 今日 top 用户
- AuthStore 扩展：`list_users`/`update_user`/`daily_limit`/`list_invites`/`stats`。
- 前端 `/admin`：stats 卡行 + 用户表（role select/停用开关/限额编辑）+ 邀请码
  （发放表单+列表）+ 会话巡查表（过滤+删除确认）+ 用量趋势表；
  `useRequireAdmin` hook；nav 第五项仅 admin。
- 文档：PARITY §0.7 + 路由清单；architecture 09（支撑域加 usage）/11（后台
  落地注记，CLI→UI 演进）；roadmap P23 勾选；本任务书 §6。

**非目标**

- 不做他人会话**内容**的查看 UI（Q3 拍板；DB 直查）。
- 不做预算充值/月度配额/导出报表（观察需求）。
- 不做 audit log（操作留痕属 P25+ 观察）。
- 不动全局预算闸与 usage.json（公网兜底保留，双轨并存）。
- 不做 per-hour 限流调整（RateLimitMiddleware 现值够用）。

## 2. 设计与实现

### 2.1 用量域与 per-user 闸（P23-1）

```
gewu/usage.py
  current_user: ContextVar[str | None]          # chat 入口 set；异步线程需显式传
  UsageStore(dsn):
    add(user_id, tokens)      # UPSERT tokens = tokens + n（同日累加）
    today(user_id) -> int
    today_all() -> list[(user_id, tokens)]       # admin 巡查（倒序）
    daily_totals(days=7) -> list[(day, tokens)]  # 趋势（含今日）
```

- LLMService 构造增 `usage=None`；`_record`/`record_usage`/`ChatStreamResult`
  迭代末三处：`budget.add(n)` 之外 `usage.add(current_user.get(), n)`（None 跳过）。
- chat.py：`token = current_user.set(user.email)`（try/finally reset）；
  `_consolidate_async` 的 work() 首行同 set（线程不继承 contextvar）。
- 限额闸：chat 入口 budget.ensure() 之后
  `limit = auth.daily_limit(email) or settings.daily_user_budget`；
  `usage.today(email) >= limit` → 429。
- config：`DAILY_USER_BUDGET` 缺省 200_000。

### 2.2 admin 端点（P23-2）

- `gewu/api/admin.py`；均 `require_admin`（403 语义沿用 P21）。
- users PATCH 校验：role ∈ 三值；status ∈ active|disabled；limit 为 null 或
  1~10_000_000；`email == 登录 admin.email` 时 role/status 字段拒绝（422
  「不能修改自己的角色或状态」）。email 大小写归一（.strip().lower()）。
- sessions 巡查：`SessionStore.list_all(kind, q)`（admin 侧新方法，无属主过滤；
  q 过滤 `"user" ILIKE %q% OR title ILIKE %q%`）；DELETE 复用 P22 的连带顺序
  （checkpointer → episodic → 业务行），不做属主校验。
- usage 趋势：`daily_totals` 聚合 + `today_all` top N。
- stats：AuthStore.stats()（P21 `_stats` 公开化）+ SessionStore 计数 + 今日用量
  + budget used/limit。

### 2.3 前端 /admin（P23-3）

- `lib/auth.ts` 增 `useRequireAdmin`（非 admin 跳回 /）；`lib/api.ts` 增 admin
  函数族（fetchAdminStats/listUsers/updateUser/listInvites/createInvite/
  listAllSessions/deleteAnySession/fetchUsageDaily）。
- `app/admin/page.tsx` 五区：stats 卡行（四卡 divide-y 风格）/ 用户表 / 邀请码
  区 / 会话巡查表 / 用量趋势表。DESIGN.md 纪律：素 Card + Table + divide-y，
  无嵌套卡；危险操作（停用/删除）AlertDialog 确认。
- nav：`useUser()` 判 role，admin 追加「管理」项。
- design-lint：检测页清单五→六。

### 2.4 门禁与文档（P23-4）

- 全绿：ruff / pytest / lint-arch（usage 入支撑域规则）/ tsc / next build /
  design-lint（六页）。
- 真跑剧本（§6 留档）：admin 登录 → stats → 用户表（改 u2 限额→u2 chat 撞
  per-user 429→恢复限额）→ 停用 u2 → u2 请求 401 → 恢复 → 邀请码发放+注册核销
  → 会话巡查（kind/q 过滤）→ admin 删任一会话（PG 三表断言）→ 用量趋势非空
  → student 访问 admin 端点全 403 → /admin 页浏览器目检（亮/暗）。
- 文档：PARITY §0.7（八端点 + per-user 429 文案）/ 路由清单；architecture
  09 支撑域表加 usage.py、11「管理与发放」节改写（CLI→UI，CLI 保留）；
  roadmap P23 勾选；任务书 §6。

## 3. 风险与守卫

| 风险 | 守卫 |
|---|---|
| contextvar 在 SSE 生成器/agent 子图丢上下文 | 同线程自然传播；单测断言 chat 一轮后 `usage.today(email)>0`（FakeChatStream usage 注入） |
| consolidate 异步线程记账丢 user | work() 首行显式 set（小头，丢也只影响统计不影响闸） |
| per-user 闸绕过（直调 eval 无 user） | eval 路径 contextvar=None → 只记全局（现状语义）；HTTP 侧全部经 chat 入口 |
| admin 改自己把自己锁死 | PATCH 拒绝改自己的 role/status（Q4） |
| usage 表无用户维度清理（删用户不存在） | 本票不做删用户；token_usage 只增不改用户表 |
| 六页 design-lint 新 finding | /admin 复用 console 范式（素 Card+Table）；检测前自跑 |
| nav 按 role 渲染造成普通用户闪烁 | 服务端无会话态，接受一次 me 往返后渲染（内测可接受，留档） |

## 4. 验收门禁

- [x] 单测全绿（UsageStore CRUD/contextvar 贯通/admin 八端点/自我保护/per-user 429）——225 passed（212→225，+13）
- [x] ruff + lint-arch 全绿（usage 入支撑域规则、admin 入禁 llm 清单）
- [x] tsc + next build + design-lint **六页**（含 /admin）全绿
- [x] 真跑剧本（§2.4）全过，admin 删会话 PG 断言留档（§6.3）
- [x] PARITY §0.7 / architecture 09+11 / roadmap P23 勾选 / §6 更新

## 5. 遗留与后续

- audit log（admin 操作留痕）与预算月度化：观察内测需求。
- 会话内容级巡查（admin 代读）：隐私边界明确后才考虑。
- per-user 用量计入 usage.json 全局闸的口径统一（当前双轨各自独立）：观察对账
  需求后定。

## 6. 执行记录

**执行日期：2026-10-01（当日立项当日执行完毕）。**

### 6.1 交付清单（对照 §2 ticket）

- **P23-1 用量域**：`gewu/usage.py`（UsageStore：token_usage 表 UPSERT 累加/
  today/today_all/daily_totals + `current_user` ContextVar + `make_usage_store`
  探测式软降级工厂）；LLMService 三记账口（`_record`/`record_usage`/
  ChatStreamResult）统一走 `_record_both` 双写；config 增 `DAILY_USER_BUDGET`
  （缺省 20 万）。
- **per-user 闸**：users 幂等 ALTER 加列 `daily_token_limit`；chat 入口全局闸后
  个人闸（查询失败放行+日志，软防护）；AuthStore 增 `daily_limit`。
- **P23-2 admin 端点**：`gewu/api/admin.py` 八端点；AuthStore 增
  list_users/update_user（停用连带删 auth_sessions——踢下线不可复活）/
  list_invites/stats（原 _stats 公开化）；SessionStore 增 list_all(kind,q)/
  count/delete_any。
- **P23-3 前端**：`/admin` 页五区（统计五格/用户表/邀请码/会话巡查/用量双栏）；
  `useRequireAdmin`；nav 第五项仅 admin 渲染；design-lint 五→六页。
- **测试**：test_admin_api（403 守卫/stats/users PATCH 全校验/自我保护/停用即踢
  不可复活/per-user 429 两档/邀请码发放核销/巡查+admin 删连带/usage 趋势）+
  test_usage（CRUD/contextvar 双写贯通/无 store 不炸）；conftest 增 usage 夹具。

### 6.2 门禁结果（全绿）

pytest **225 passed**（+13）/ ruff check+format / lint-arch / tsc / next build
（/admin 入路由）/ design-lint **六页**零 finding（/admin 复用 console 范式）。

### 6.3 真跑剧本（§2.4 全过，uvicorn :8000 + curl + Chrome）

1. p22test 提权 admin（make admin）→ 登录 → stats：users 3/sessions/invites/
   chat_sessions 2/budget 87.8 万水位（真实数据）。
2. POST invites 发码（uses=1/days=7/note）→ 新用户注册核销 → 列表 used_count 1/1。
3. per-user 限额：设 50000 → 用量抬到 60000 → chat 429
   「今日个人 token 预算已用尽（上限 50000）」→ PATCH null 恢复默认。
4. 停用 u23 → me 401（cookie 立即失效）→ 启用 → **旧 cookie 仍 401**（会话已删，
   需重新登录）→ 重新登录 200。
5. 自我保护：admin 改自己 status → 422「不能修改自己的角色或状态」。
6. student 访问 admin 八端点 → 403；匿名 → 401。
7. 会话巡查：全量表（user/title/kind/updated_at）+ q=u23@ 过滤 1 条 + kind 过滤；
   admin 删 u23 会话 → PG 断言（该会话删前仅业务行 1 → 删后五处全 0；三表
   非零场景由单测覆盖）。
8. **记账贯通实证**：admin 真跑一轮 direct chat（LLM 真调用）→
   `token_usage` 落账 `p22test | 732`——contextvar 从 chat 入口经 graph 迭代到
   LLMService 双写全链路生效。
9. /admin 页浏览器目检：亮/暗双主题五区渲染（统计 732/邀请码核销状态/巡查表/
   用量榜全真实数据）+ 限额行内编辑交互（80000 保存后 API 复核生效）。

### 6.4 撞坑记录（对后来者有值）

1. **langchain 导入后 from-import 子模块被劫持成空壳（最难缠）**：
   `from langchain_core.messages import ...` 之后再
   `from gewu.llm.embed import GewuEmbeddings` **非确定性失败**——错误消息
   「cannot import name 'GewuEmbeddings'. Did you mean: 'GewuEmbeddings'?」
   （同名建议！），失败现场 sys.modules 里模块属性却齐全；importtime 显示模块
   已加载完成；module-form（`import gewu.llm.embed`）与手动 importlib 两步均稳定
   成功；排除了 pyc 缓存/目录包遮蔽/循环导入/Unicode 同形字符后判定为
   langchain lazy-import 机制的竞态。**修法**：service.py 的 embed 改 module-form
   （`import gewu.llm.embed as llm_embed`）。同形字符提示（Did you mean 完全
   同名）是此类「from-import 失败但属性存在」的判别特征。
2. **contextvar 不能在 SSE sync 生成器开头一次 set**：StreamingResponse 的 sync
   迭代由线程池分派、每次 next 可能换 Context——开头 set 只活在第一次，
   且跨 Context `reset` 直接 ValueError。**修法**：`while True` 循环里**每次
   next(stream) 前 re-set**（set 随 Context 副本传播进当轮节点执行）。test 里
   第一次撞出来（ValueError: created in a different Context）。
3. **create_app 新依赖的测试连锁（P22 同款再撞）**：UsageStore 缺省连
   settings.pg_dsn（测试=5433 不可达）→ pool 后台重连+checkout 默认 30s 超时
   → chat 入口全量挂起（全套跑 272s）。**修法**：`make_usage_store` 探测式软
   降级（psycopg connect_timeout=2 探活，不可达退 None 禁用 per-user 功能），
   老测试零改动通过；池 checkout timeout 收紧 5s。
4. **P22 批量正则注入测试参数的教训重演**：六个测试文件的 usage 位置参数注入
   造成大面积调用错位（22 failed）——果断 `git checkout HEAD` 回滚，改走
   「create_app 哨兵 + 软降级」让老测试零改动。**结论：改共享工厂的参数注入
   优先考虑缺省行为兼容，而不是改所有调用点。**
5. **test_api.py 的既有 `usage: dict` 参数名冲突**：注入参数撞名（duplicate
   argument）——重命名 usage_json 解决（后来随回滚取消注入，保留更名）。
