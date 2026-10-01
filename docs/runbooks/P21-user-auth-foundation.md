# P21 用户体系·认证地基：邀请码封闭注册 + role 坐实 + SQLite 全迁 PG（任务书）

> **背景**：P17 编排重构与 P18~P20 前端三票已收口，roadmap 剩余主线为 M4
> 公网部署，而当前服务**无任何用户概念**：CORS `*` 全开是有意保留的公开
> demo 契约（`docs/history/go-notes.md:142`），session_id 客户端自报、
> user_id 服务端写死 `demo-{role}`（`api/chat.py:70`），role 是请求参数——
> 任何人自称 counselor 即可拿辅导员工具（`tools_for()` 权限矩阵实为君子
> 协定），`/api/business/reset` 任何人可点。用户体系是部署前最大缺口，
> 也是「用户级记忆」的前置：`gewu/memory.py` 的 fact/episodic 双表早已按
> user_id 隔离运转，真实账号接上即点亮（本票不涉记忆前端可见性，P22 承接）。
> 无鉴权→鉴权属范式变更，按惯例正式立项。

## 0. 拍板结论

| # | 问题 | 拍板 | 依据 |
|---|------|------|------|
| Q1 | 认证形态 | **邀请码封闭注册（内测）**：邮箱+密码+有效邀请码；邮箱仅作唯一标识不验证（零 SMTP 依赖） | 用户拍板 2026-10-01：暂为内测环境，封闭注册防配额滥用 |
| Q2 | 切票 | **三票制**：P21 认证地基+PG 收口 / P22 会话与记忆管理 / P23 管理后台 | 与既有 P 系列节奏一致，每票独立门禁+真跑+留档 |
| Q3 | business/memory 两 SQLite 库 | **P21 顺手全迁 PG**（闭 P12 §5.3 留下的 P13 遗留） | 用户拍板；部署形态最干净；单进程下无数据模型变化，接口签名不动只换驱动 |
| Q4 | 会话凭证 | **服务端 session 表 + httpOnly cookie**（`auth_sessions` 存 token 的 sha256 摘要，30d 滑动过期），不用 JWT | 可撤销（登出即失效）、无签名密钥管理；自写轻量不引第三方托管（免费无阉割偏好） |
| Q5 | 密码哈希 | **argon2id（argon2-cffi）** | 现代默认、免费无阉割；fastapi-users 等全家桶不引入（自写，学习与面试叙事完整） |
| Q6 | 对外用户键 | **users.id (BIGSERIAL) 内部代理键 + email UNIQUE；台账/记忆/agent 链路等既有 TEXT user 列统一存 email**（唯一、人类可读，台账展示直出） | id 不对外暴露（防枚举由「对外只见 email + 随机 session token」承担，故无需 UUID）；与 chunks BIGSERIAL 惯例一致；代理键把「改邮箱」的级联限制在 users 一行；内测换取 agent 工具链与 memory 双表零类型改动 |
| Q7 | role 权威 | **users.role 唯一权威，服务端取**；请求体 role 参数废弃（PARITY 契约变更注记） | 权限矩阵坐实的前提；越权拦截从约定变强制 |
| Q8 | 跨域形态 | **前端同源代理**：next.config 加 `/api/:path*` rewrite → `127.0.0.1:8000`；CORS 从 `*` 收紧为 env 白名单（默认空=同源-only） | cookie 跨域（3000→8000）在 http dev 下走不通（SameSite=None 需 Secure）；同源后 Lax 即可，也与 M4 Caddy 同域规划（/api→8000）一致 |

## 1. 目标 / 非目标

**目标**

- PG 三新表（幂等 DDL，沿用 `rag/schema.py` 惯例）：`users` /
  `auth_sessions` / `invite_codes`；认证四端点 register / login / logout /
  me（FastAPI dependency `get_current_user`）；邀请码原子核销。
- argon2id 哈希 + httpOnly cookie（SameSite=Lax；`Secure` 由 env 控，
  M4 https 后开）；注册成功即建 session（注册即登录）。
- 邀请码/管理员发放：`make invite USES=10 NOTE=...`（生成随机码打印）、
  `make admin EMAIL=...`（scripts 直连 PG 简单脚本；后台 UI 留 P23）。
- **business / memory 全量迁 PG**：`sqlite3` → `psycopg`（沿用 rag 域
  pool/autocommit 惯例），函数签名、返回形状、权限语义（台账仅本人可查/
  取消）零变化；SQLite 文件退役归档，P13 遗留闭线。
- 范围收紧：`/api/chat` 必须登录、user_id=登录 email、role 服务端化；
  `/api/business/overview` 本人视图（admin 可 `?all=1`）；
  `/api/business/reset` admin-only；`/api/search` 登录即可；
  health/docs 保持公开。
- 前端最小集：`/login` 新页（登录+注册双卡，过 DESIGN.md + design-lint）、
  nav 登录态+登出、chat 页 role 下拉退役（只读身份徽章）、401 全局拦截
  跳登录、next rewrite 同源代理（SSE 真跑断言无缓冲）。

**非目标**

- 不做邮箱验证 / 找回密码 / OAuth（公测前评估）。
- 不做会话归属表、会话列表/改名/删除、多会话侧边栏（P22）。
- 不做管理后台页面、per-user token 预算（P23；内测封闭注册下全局预算闸
  兜底够用）。
- 不做 memory_fact 用户可见性（roadmap 遗留由 P22 承接）。
- 不改 SSE 事件形状与编排链路（身份注入点只在 chat.py 装配处）。

## 2. 设计与实现

### 2.1 auth 域（P21-1，新包 `gewu/auth/`）

分层遵守 lint-arch：域包自持存储与幂等 DDL（store.py / schema.py），HTTP
层挂 `gewu/api/auth.py`，main 只装配。

```
users(id BIGSERIAL PK, email TEXT UNIQUE NOT NULL, password_hash TEXT NOT NULL,
      display_name TEXT, role TEXT CHECK(role IN('student','counselor','admin'))
        NOT NULL DEFAULT 'student', status TEXT NOT NULL DEFAULT 'active',
      created_at TIMESTAMPTZ, last_login_at TIMESTAMPTZ)
auth_sessions(token_hash TEXT PK, user_id BIGINT FK→users, created_at,
      expires_at, last_seen_at)        -- cookie 存原始 token，库存 sha256
invite_codes(code TEXT PK, max_uses INT, used_count INT DEFAULT 0,
      expires_at, note, created_at, created_by)
```

- 注册事务：单条语句原子核销
  `UPDATE invite_codes SET used_count=used_count+1 WHERE code=%s AND
  used_count<max_uses AND (expires_at IS NULL OR expires_at>now()) RETURNING
  used_count`——成功才建 user + session；码失效/超限 400、email 冲突 409
  （封闭注册无存在性泄露顾虑，错误可区分）。
- login 失败统一 401「邮箱或密码错误」；argon2 verify 恒时比较。
- cookie：`gewu_session`，httpOnly，SameSite=Lax，Max-Age 30d；滑动续期
  （剩余 <15d 时滚动 expires_at）。
- `get_current_user`：cookie → sha256 查 session（join user；过期/非
  active 401）——受保护端点统一 dependency 注入。
- 单测：注册（码失效/超限/并发核销只成功一次/email 冲突）、login 错密码
  401、登出后 token 立即失效、过期 session 401、role CHECK 约束。

### 2.2 business / memory 迁 PG（P21-2，闭 P13 遗留）

- `business/db.py` 与 `memory.py`：`sqlite3` → `psycopg`，**函数签名、
  返回形状、权限语义零变化**；`AUTOINCREMENT`→`BIGSERIAL`、`?`→`%s`、
  时间戳统一 `timestamptz`；`MEMORY_CONSOLIDATE` 开关与 consolidate 管线
  原样（只换底座）。
- user 键统一 email（Q6）：chat.py 注入真实 email 后，agent `call_tool
  (user=...)`、memory 读写、台账归属全链路零类型改动；既有 `demo-{role}` /
  `eval-user` 历史数据随库重建消亡（评测集 jsonl 文件制不受影响）。
- 数据不迁移：内测 demo 数据无保留价值，SQLite 文件移入 `data/` 归档，
  建表重建 + `make demo` 重放种子；runbook 留档此决策。
- 既有 business/memory 单测随驱动替换全量回归（`make test` 已含 pg-up，
  PG 依赖对测试无新增成本）。
- grep 守卫：`sqlite3` import 清零（归档 scripts 除外）。

### 2.3 范围收紧与 PARITY 契约变更（P21-3）

| 端点 | 变更 |
|---|---|
| POST /api/chat | 未登录 401；请求体 `role` **废弃**（users.role 权威）；user_id=登录 email |
| GET /api/business/overview | 登录者=本人台账；admin 可 `?all=1` |
| POST /api/business/reset | admin-only（非 admin 403） |
| POST /api/search | 登录即可（console 检索调试） |
| GET /api/health、/api/docs | 保持公开（无敏感信息） |
| CORS | `allow_origins=["*"]` → env `CORS_ORIGINS` 白名单（默认空=同源-only）+ `allow_credentials=True` |
| 新增 | POST /api/auth/register / login / logout、GET /api/auth/me |

- 评测不受影响：eval harness 直调编排层（不走 HTTP /api/chat），
  `make eval` 双轨照旧。
- PARITY.md 增「认证契约」一节（四端点、cookie 契约、role 废弃注记），
  旧契约「公开演示无鉴权」条目改写并指向本票。
- chat.py 装配点：`user_id=current_user.email`、`role=current_user.role`
  （`demo-{role}` 写死退役）；mem_block 注入与 consolidate 管线不动。

### 2.4 前端与同源代理（P21-4，硬点）

- `next.config.mjs`：rewrites `/api/:path*` →
  `${API_PROXY_TARGET:-http://127.0.0.1:8000}/api/:path*`；`lib/api.ts`
  API_BASE 改相对 `/api`（保留 `NEXT_PUBLIC_API_BASE` 覆盖口给直连调试）。
- **硬点：SSE 过 rewrite 的流式透传**——Next 15 rewrite 对流式响应一般可
  透传，但必须真跑断言（首包延迟、done 前无整段缓冲）；**退路**：手写
  `app/api/[...path]/route.ts` route handler（fetch + ReadableStream 手动
  透传 + 显式 `text/event-stream` 头），compare 页双轨 SSE 同验证。
  （联调顺序提示：此件可提前到 P21-1 浏览器联调时落地，再收紧 CORS。）
- `/login` 新页：登录/注册（含邀请码栏）双卡切换；DESIGN.md 契约下设计
  （表单对比度 ≥4.5、无冷色），纳入 design-lint 检测页清单；注册成功即
  登录跳 `/`。
- nav 登录态：display_name + 登出；三页未登录跳 /login（前端守卫 + 后端
  401 双保险）；console 的 reset 按钮对非 admin 隐藏（后端 403 兜底）。
- chat 页 role 下拉退役 → 只读身份徽章（student/counselor/admin）；
  api.ts 移除 role 传参；fetch 统一包装 401 拦截跳登录。

### 2.5 门禁与文档（P21-5）

- 全绿：ruff / pytest（auth 新增 + 迁移回归）/ lint-arch / tsc / next
  build / design-lint（/login 入检测清单）；argon2-cffi 锁入 uv.lock。
- 真跑剧本（§6 留档）：`make invite` 发码 → 浏览器注册（码核销、即登录）
  → 登出重登 → chat SSE 对话（经同源代理，流式无缓冲）→ 台账本人可见 →
  student 调 reset 403 → 无 cookie curl /api/chat 401 → 杀进程重启
  session 仍有效（PG 持久化验证）。
- 文档：architecture 新增认证篇（表/流程/cookie 契约/迁移决策）、PARITY
  契约变更节、roadmap 勾选、P13 遗留闭线注记。

## 3. 风险与守卫

| 风险 | 守卫 |
|---|---|
| SSE 过 Next rewrite 被缓冲/延迟 | 真跑断言首包延迟 + done 事件时序；退路手写流式透传 route handler |
| 迁移语义差异（占位符/自增/事务/rowcount） | 接口签名零变化 + business/memory 单测全量回归；SQL 改动分小 hunk Edit（Mimosa 钩子对带参写语句敏感） |
| 邀请码并发核销竞态 | 单条 UPDATE ... RETURNING 原子核销 + 并发单测 |
| cookie 经 http 明文传输（公网部署前） | `COOKIE_SECURE` env；M4 Caddy https 前置后开启；dev 同源 http 可接受 |
| session_id 仍客户端自报（跨用户猜 uuid） | uuid 不可枚举，短期可接受；归属表 P22 收口（明确非本票范围） |
| 评测/演示流程被登录态破坏 | eval 直调不受影响；真跑剧本含完整登录路径；PARITY 注记旧调用方迁移 |
| argon2-cffi 平台轮子 | macOS arm64 官方轮子存在；uv.lock 锁版本 |
| Node25/前端环境坑复发 | dev script 已固化（P16 经验）；SSE 验证用 dev + prod build 双跑 |

## 4. 验收门禁

- [ ] 单测全绿（auth 域新增 + business/memory PG 回归 + 越权/过期/原子核销）
- [ ] ruff + lint-arch 全绿；argon2-cffi 锁入 uv.lock
- [ ] `make eval` 双轨回归不受影响（classic 28/28 与 agent/chitchat 基线保持）
- [ ] 前端 tsc + next build + design-lint（/login 新页）全绿
- [ ] 真跑剧本八步全过（§2.5）
- [ ] `sqlite3` import grep 清零、SQLite 文件归档；P13 遗留闭线注记
- [ ] PARITY / roadmap / architecture 认证篇更新

## 5. 遗留与后续

- **P22 会话与记忆管理**：chat_sessions 归属表（session↔user）+ 会话列表/
  改名/删除 API + 前端多会话侧边栏（刷新不丢、过 design-lint）+
  memory_fact 查看/编辑/删除面板——闭 roadmap「长期记忆仅注入不可管理」
  遗留项。
- **P23 管理后台**：admin 页（用户列表/用量/会话巡查/邀请码发放 UI）+
  per-user token 预算（429 教训的治本）+（可选）敏感操作审计日志。
- 邮箱验证 / 找回密码 / 改邮箱（涉台账 email 级联更新）：公测前评估。
- HTTPS 与 Secure cookie：随 M4 Caddy 部署开启。

## 6. 执行记录（2026-10-01，当日完成）

实现序：P21-0（摸底 + argon2-cffi 25.1.0 入 uv.lock）→ P21-1（auth 域三件：
store/api/auth_tool）→ P21-2（business/memory 换 psycopg）→ P21-3（chat/routes
收紧 + config 两键 + app 工厂接线）→ 测试改造（conftest 三夹具 + 登录客户端
助手 + 8 文件适配 + test_auth 新增）→ P21-4 前端（rewrite/api.ts/login 页/
UserMenu/role 徽章/console 守卫）→ 文档四件。

**真跑剧本（八步全过，经 next 同源代理 :3101 → :8000）**：

| # | 步骤 | 结果 |
| --- | --- | --- |
| 1 | `make invite USES=3` | 码 bf48b6ccd3 生成 |
| 2 | 经代理注册 | 200 + cookie 下发 + 注册即登录（码核销） |
| 3 | `/api/auth/me` | 200 {email, display_name, role:student} |
| 4 | chat SSE（mode=direct） | **硬点通过**：route 事件 0.17s 首达、93 事件增量透传（answer_delta 分批到达）、done 4.35s——rewrite 无整段缓冲，无需退路 route handler |
| 5 | overview | scope=mine（本人视图） |
| 6 | 权限 | student reset 403 → `make admin` 提权 → reset 200 + ?all=1 scope=all |
| 7 | 登出 | logout 200 → me 401（服务端 session 删除） |
| 8 | 重启持久化 | 杀 uvicorn 重启 → 同 cookie me 200（PG 会话存活） |

**门禁**：pytest 191 全绿（174 → +17：test_auth 12 + overview/reset/search 收紧 5）；
ruff check + format 全绿；lint-arch 全绿（auth 域并入支撑域规则）；tsc + next build
绿（/login 6.73kB）；design-lint 四页全绿（/login 新入检测清单，零 finding）。
评测未重跑：eval harness 进程内直调编排层，不受 HTTP 认证影响（PARITY §0.5
注记；chat.py 装配点只换身份来源，图与提示词零改动）。

**拍板转实现决策**（任务书未预见，实现中定）：
- `user` 是 PG 保留字——bookings/leave_tickets 列定义与全部 SQL 引用双引号
  `"user"`（SQLite 时代无此约束，迁移首坑）；
- reset() 与 wipe() 语义分离：SQLite AUTOINCREMENT 在 DELETE 后计数不归零，
  reset 沿用（PARITY 行为不变），wipe（DELETE + setval）是测试专用保证 VE-0001
  可断言；
- business 表 date/slot/created_at 维持 TEXT（任务书草案写 timestamptz，实现
  时降级：ISO 串字典序比较即正确语义，避免无谓转换）；memory 表 created_at/
  updated_at 用 timestamptz（有排序需求）；
- role 参数废弃采用「忽略不报错」（非 422）：旧调用方（compare 页/脚本）带
  role 字段仍可跑，减一刀迁移摩擦；前端同票移除传参；
- 邀请码过期测试不靠 days=0 边界（时钟偏差会翻车），测试直改 SQL 造过期。

**撞坑记录**：
- design-lint 四页「Navigation timeout」假失败：3200 端口挂着 P20 会话残留的
  旧生产服（13:55 起，旧构建无 /login 无 rewrite），杀掉后干净全绿——脚本
  cleanup 只管自己起的实例，跨会话残留要人工识别；
- bash3.2 全角字符黏变量名复发：design-lint.sh `$name：` 在 FAIL 分支首次执行
  时炸 unbound variable（P20 同款坑，此前只在 FAIL 分支才触发所以漏网），
  改 `${name}：`；
- curl cookie jar 的 `#HttpOnly_` 前缀行 MozillaCookieJar 解析不了（当注释跳过），
  SSE 验证脚本手拼 Cookie 头绕过。

**提交**：后端检查点（auth 域 + 迁移 + 收紧 + 测试）与前端/文档各一票，
pathspec 限定避开并行会话的 log-report/icon.svg。SQLite 文件归档 data/archive/
（business.db/memory.db/sessions.db），运行库重建。
