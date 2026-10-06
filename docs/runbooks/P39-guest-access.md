# P39 游客开放通道：免登录直接用 + 特殊能力登录解锁（任务书）

> **背景**：gewu 已定位个人毕设展示项目（75ce56e），需要对外放人体验；但 P21
> 起注册是邀请码封闭制（内测），答辩/评委/简历访客的注册摩擦不可接受。
> 需求口径（2026-10-06 用户拍板）：**默认先不登录、直接可用**，能做的都开放
> （含业务办理与联网搜索）；记忆、控制台这类「登录后解锁」；访客在 UI 上要能
> 感知自己是游客状态。编号说明：P38 已被评测体系候选占号，本票顺延 P39。

## 0. 拍板结论

| # | 问题 | 拍板 | 依据 |
|---|------|------|------|
| Q1 | 形态：demo 落地页 vs 渐进式鉴权 | **渐进式鉴权**：未登录访客进站由前端自动领取游客身份，无独立落地页 | 用户口径「默认就是先不用登录，可以直接使用」；落地页方案多一步点击仍是摩擦 |
| Q2 | 游客身份的实现载体 | **影子用户行**（`role=guest` + `email=guest-<hex>@guest.local`） | 全链路身份锚点是 email 字符串（下游表无 FK），一个 users 行即贯通会话/配额/用量/台账/反馈，零 schema 改动；对比「匿名 token 不建行」要侵入全部鉴权语义 |
| Q3 | 开后端平行 `/api/guest/chat` 路由？ | **不开** | 整条 chat 链路（SSE/会话归属/预算闸/配额闸/trace）复用同一实现；能力收窄走 `call_tool` 单一出口的 `ToolSpec.roles`，不产生第二套权限真相源 |
| Q4 | 能力面 | **学生同集**：检索问答/深研/联网（IQS）/业务办理（含 HITL 确认）全开；`pending_leaves`/`approve_leave`（counselor 专属）与 admin 面不开 | 用户「能做的都可以开给它」；业务办理是论文 HITL 展示点且 mock 库可 reset；联网搜索明确开放（复用 web_search 日限闸） |
| Q5 | 「登录后解锁」面 | `/api/docs`、`/api/search`、`/api/memory/*` 三处 403（`require_member` 守卫）；`/api/sessions`/`chat`/`feedback`/`business/overview` 照常 | 用户点名排除记忆与控制台；后端 403 是真边界，前端守卫只是体验层 |
| Q6 | 游客记忆 | **chat 尾部 consolidate 跳过**（不写 facts/episodic）；读侧注入空 | 无 `/api/memory` 管理面；7 天硬过期数据不值得固化成本（flash 模型调用） |
| Q7 | 防滥用 | 签发端点 IP 双闸 5 次/分 + 20 次/天（`RateLimiter` 加 `window_sec` 扩展）+ 游客配额 50k/天（落 `users.daily_token_limit`，per-user 闸照常）+ `make guest-prune` 过期回收 | 进程内限流语义与 login/P36 同款；展示站流量级足够 |
| Q8 | 游客会话生命周期 | **7 天硬过期、不滑动续期**（对比正式用户 30d 滑动）；游客可停留 `/login` 登录升级（不顶回） | 短 TTL 控制 users 行积累；「已登录顶回」若不排除游客，游客将永远进不了登录页 |

## 1. 目标 / 非目标

**目标**
- 后端：`POST /api/auth/guest`（GUEST_MODE 缺省关→404）、`create_guest`
  store、CHECK 约束幂等加 `guest`、`require_member` 守卫、工具矩阵加
  guest、游客跳 consolidate、`RateLimiter.window_sec`、`gewu/maintenance.py`
  + `scripts/guest_prune.py` + `make guest-prune`。
- 前端：`useRequireUser` autoGuest（签发期间不闪跳、404 回退跳登录）、
  `useRequireMember`、游客徽章/提示条/导航隐藏、login 页顶回修复、
  UserMenu 游客态「登录升级」。
- 契约登记：PARITY §0.11 + §10 矩阵 guest 列 + §14 配置表三键；
  `.env.example` 登记；本任务书。
- 真跑验收 + 部署上线。

**非目标**
- 游客数据迁移到正式账号（登录即切换身份，不搬数据）。
- admin 用户列表的游客行过滤 UI（role 列可见 guest，巡查有价值）。
- run_eval 认证适配、MEMORY_CONSOLIDATE 迁 Settings（既有挂账）。
- 不动核心域：单循环决策语义、评测集、行为开关机制本身。

## 2. 设计与实现

### 2.1 身份贯通（为什么不造第二套）

全链路（chat_sessions/token_usage/memory/business 台账/message_feedback）
的用户键都是 TEXT email、无 FK——影子用户行让游客**无 schema 改动**贯通：
会话归属校验（sessions.get(email, sid)）、per-user 限额闸
（daily_limit(email)）、用量记账（current_user contextvar）、trace 归属
全部按 email 工作。两个实现细节：

- **email 用小写 hex**（`token_hex(6)`）：`login`/`daily_limit`/
  `update_user` 都按 `lower(email)` 查询，`token_urlsafe` 的大写会失配
  ——真跑前的 store 测试抓到（否则游客配额闸会静默回退全局 200k）。
- **不滑动续期**：`user_for_token` 对 `role=guest` 跳过续期 UPDATE，
  7 天硬过期由 expires_at 直接判定。

### 2.2 能力闸（单一真相源）

`ToolSpec.roles` 学生集六工具加 `"guest"`（query_venues/my_bookings/
leave_status/book_venue/cancel_booking/submit_leave）；counselor 专属两
工具不加。`query_flows`/`run_flow` 动态解析流程 spec 后走同一
`has_role` 判定，清单展示自动一致；`role_label` 加 guest→「游客」（越权
回执文案）。`VALID_ROLES` **不含** guest：admin 改角色面不可把账号设成
游客（`update_user` 校验拒绝）；admin 仍可 `status=disabled` 停用游客
（现有行为=删其全部 session）。

### 2.3 签发端点与限流

`POST /api/auth/guest`：`GUEST_MODE` 关→404（不暴露开关存在）；开→IP
双闸（分钟 5 次 + 日 20 次，`RateLimiter(per_minute=N, window_sec=86400)`
——通用件扩展，缺省 60s 行为零变化）→ `create_guest(ttl=7d,
daily_token_limit=50k)` → set-cookie（max_age 对齐 TTL）→ 返回体同
`/api/auth/me`。

### 2.4 前端渐进式鉴权

- `useRequireUser({autoGuest=true})`：未登录先 `ensureGuest()`（**只去重
  在途 Promise**——成功也清缓存，cookie 过期后的再进入仍能重新签发）；
  签发期间 loading 保持 true（页面骨架不闪跳）；404（开关关）回退现状跳
  `/login`。`records`/`page` 零调用点改动（默认开）。
- `useRequireMember()`：未登录**或游客**→ `/login`（游客是合法登录态，
  不能只判空）。console/memory 两页换用。
- login 页顶回条件改「非 guest 才顶回」+ 游客态文案与「返回对话」出口；
  UserMenu 游客态=徽章+「登录」升级入口（登出对一次性身份无意义）。
- nav 游客隐藏「系统」组（console/memory/admin）；聊天页游客提示条
  （静音 Alert；**10-06 降噪**：封闭内测访客无注册路径，「登录解锁」
  引导对访客是死路——文案只陈述事实「长期记忆与控制台暂不可用」，
  不引导登录；登录入口收到侧栏角落的静音图标钮，服务站点主人评测
  登录）+ composer「游客身份」徽章（ROLE_LABEL 加
  guest）。

### 2.5 清理（maintenance）

`gewu/maintenance.py prune_guests(dsn, days)`：按 sessions 删除端点的
连带顺序（checkpointer 三表 → episodic → 业务行 → 会话行）做批量版，
再删 users 行（auth_sessions FK 级联）；checkpoints 表未建时容忍跳过。
CLI `scripts/guest_prune.py` + `make guest-prune [DAYS=N]`（缺省取
GUEST_SESSION_TTL_DAYS）。展示站手动节奏即可（答辩前/每周），可选
systemd timer。

## 3. 测试与验收

- **pytest 14 例新增**（tests/test_guest.py）：store 层（shape/配额/不可
  登录/不续期/admin 不可设 guest）、RateLimiter 自定义窗口、API 层
  （开关关 404/签发+me/限流 429/logout/会话流+归属/member 面 403 对照/
  overview 200/admin 403）、工具矩阵断言、prune 级联+对照组。
  test_registry.py 超集断言按 P39 语义更新（book_venue 角色列表含
  guest、approve_leave 对 guest 拒绝）。**全量 306 passed**。
- **真跑（本地 8001/3101，GUEST_MODE=1）**：
  - curl 级：签发→me=guest；docs/search/memory/admin=403；overview/
    sessions=200；SSE 问答完整事件流（route=factual、136 帧
    answer_delta、citations、follow_ups、done）；
  - 库级：游客 memory_fact/episodic **零行**（consolidate 跳过生效）、
    token_usage 归属 8584/50000；
  - UI 级（IAB 无 cookie 全流程）：T1 直达首页自动游客（徽章+提示条+
    导航只剩对话/我的办理，见
    [assets/p39/10-home-guest.png](assets/p39/10-home-guest.png)）；
    T2 `/memory`→重定向 `/login`；T3 登录页可停留（游客文案+返回对话）；
    T4 任务卡深研问答完整（4 子问题、引用溯源、反馈按钮、28.2s 延迟
    徽标，见 [assets/p39/20-guest-answer.png](assets/p39/20-guest-answer.png)）。
- **验收中发现并修复**：① 游客 email 大写失配（改 token_hex，§2.1）；
  ② `ensureGuest` 成功缓存导致 cookie 丢失后不重签（改在途去重，§2.4）；
  ③ 本地共享 `.env` 的 `RATE_LIMIT_PER_MINUTE=20`（历史调试残留）在
  next 代理单 IP 拓扑下触发 429——**非本票缺陷**，验收用
  `RATE_LIMIT_PER_MINUTE=600` 覆盖；部署时须核对线上值。

## 4. 部署

1. server `.env`：确认 `RATE_LIMIT_PER_MINUTE` 为默认 600（勿沿用本地
   调试值 20）+ 加 `GUEST_MODE=1` → `systemctl restart gewu-api`。
2. web：build + rsync + `pm2 restart`（前端有新守卫逻辑）。
3. 线上验证：无痕窗口直达 gewu.mrpwn.top → 游客徽章+提示条；深研问答；
   `/memory` 导去登录；`/api/auth/guest` 在旧 cookie 下不受影响。
4. 清理节奏：`make guest-prune` 手动（答辩前跑一次）；可选 systemd
   timer 周跑。

## 5. 论文叙事顺带收获

鉴权演进三阶段成完整故事线，可写进安全/部署章节：P21 前「无鉴权公开
演示」（user_id 写死 demo-{role}）→ P21 邀请码封闭内测（session 表+
argon2id+限速）→ P39 受限访客通道（角色能力闸+短 TTL+配额+清理）。
「用角色声明能力边界，而不是复制一条平行链路」是 agent-first 架构在
安全面的自然延伸。
