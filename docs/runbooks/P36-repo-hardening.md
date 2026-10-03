# P36 仓库加固与文档同步：安全修复 + 死代码清理 + 文档对齐（任务书）

> **背景**：P26~P35 全量部署上线后，项目进入稳定期。按 AGENTS.md「少造轮子/
> 标准件优先」原则对仓库做一次全身体检（2026-10-03，三路并行审计：安全漏洞、
> 文档时效、依赖与死代码）。结论：**无高危**（.env 未入库、SQL 全参数化、admin
> 端点全鉴权、argon2id+session sha256、无 SSRF 面、前端无 XSS 面）；4 个中危
> （XFF 可伪造绕限流且登录无账号级限速、生产 cookie 未开 Secure、500/SSE 回传
> 异常原文、trace 落库无 TTL）+ 若干低危（FastAPI 框架文档面公开、运维脚本
> shell 拼 SQL、compose 端口绑 0.0.0.0、/api/docs 口径与 search 不一致）。
> 文档侧欠账更大：**根 README 停在 P20 时代**（compare 页/SQLite/ROUTER_MODE/
> make build 全是旧世界），architecture/01、09、10 未跟 P31 classic 退役，
> PARITY §14 配置表含三个死变量，AGENTS.md 本身未入库且核心域表述过时。
> 死代码侧：前端 6 个零引用文件（compare 遗物）、pyproject 4 条幽灵
> per-file-ignores、REACT_MODE 死配置（全仓零消费点）、compare 会话枚举残留。
> 本票一次收口。编号说明：P35（骨架重构+纯白画布，03e1409）已执行，P36 顺延。

## 0. 拍板结论

| # | 问题 | 拍板 | 依据 |
|---|------|------|------|
| Q1 | 服务器侧两项（COOKIE_SECURE、nginx XFF 核对） | **本轮一并部署上线**：仓库全绿后 rsync 直推 + systemd/pm2 restart + 线上验证 | 沿用 P34 部署流程；行为面变化（限流键/错误文案）须线上验证才算闭环 |
| Q2 | print→logging（40+ 处，集中在 mw/memory/retrieve 降级路径） | **挂账下轮** | 本轮改动面已大（安全+死代码+11 份文档），日志迁移散布在关键降级路径，混入会放大回归排查成本 |
| Q3 | /api/docs 是否收紧 | **加 require_user，与 /api/search 口径统一** | routes.py:8 原注释是「docs 保持公开」的设计决定，但语料清单枚举与 search 需登录的口径不一致；前端 console 页登录后才可见，无影响 |
| Q4 | compare 枚举处置 | **退役**（VALID_KINDS/DDL CHECK/api.ts 类型/admin 过滤项），已有 DB 行不动（CHECK 仅约束 insert，DDL 变更只影响新建库）；**ChatMode.react 保留**（run_eval.py:296 仍传该值，react=auto 别名） | P31 删 compare 页后该枚举成死值；react 是评测工具兼容面，不能一刀切 |
| Q5 | 行为面变化的契约口径 | 全部走 **PARITY §0.10 正式注记**（500/SSE 错误文案、限流键 XFF 末段、login 429、/api/docs 需登录、kind=compare 422、REACT_MODE 删除、API_DOCS 开关） | 本票核心域零改动（路由语义/评测集/灰度机制不动），但触及错误体与鉴权边界，须冻结名下演进 |
| Q6 | XFF 取段方向 | **取末段**。nginx `$proxy_add_x_forwarded_for` 是「请求带入值 + $remote_addr」追加式，末段=连上 nginx 的真实来源；首段是浏览器可伪造值 | 单层 nginx + Certbot 拓扑（P34 部署形态）下末段语义成立；部署时同步核对 nginx 配置写死此假设 |

## 1. 目标 / 非目标

**目标**
- **安全修复 7 项**（§2.1）：错误信息泄漏收口、XFF 限流键修正、login 账号级
  限速、/api/docs 收紧、FastAPI 框架文档面开关化、trace-query.sh 参数化、
  compose 端口绑回环。
- **死代码与卫生 5 项**（§2.2）：前端 6 死文件、pyproject 幽灵项+重复依赖、
  REACT_MODE 死配置删除、compare 枚举退役、工具状态文件 untrack + AGENTS.md 入库。
- **文档同步 11 项**（§2.3）：根 README 重写、architecture/01/09/10 跟齐 P31+、
  PARITY §0.10/§14/§3、roadmap 补 P34~P36、docs/README 索引、.env.example
  全集重写、DESIGN.md 版本晋级、AGENTS.md 核心域表述更新。
- 部署上线 + 线上验证（COOKIE_SECURE、XFF、/docs 404、登录限速）。

**非目标**
- 不动核心域：路由行为语义、评测集、行为开关+灰度门禁机制本身零改动。
- print→logging 迁移（Q2 挂账）；trace TTL / 会话配额（P27 B 期既有挂账）。
- run_eval 认证适配、危险词黑名单丰富化（P31 既有挂账）。
- 不升级依赖版本（审计确认无老旧项，仅清理声明）。

## 2. 设计与实现

### 2.1 安全修复（代码侧）

1. **错误信息泄漏收口**：`api/routes.py` 5 处 `raise HTTPException(500,
   detail=str(e))` 收口为模块内 `_internal(e)` helper——原文+traceback print
   留日志，对外统一 `detail="服务内部错误，请稍后再试"`；`api/chat.py` SSE
   error 事件 `ev.error_evt(str(e))` 同改笼统文案（原文已有 turn_log 落档）。
2. **XFF 末段**：`middleware.py client_ip` 取 `split(",")[-1]`，注释写明
   「追加式反代拓扑下末段=真实来源」的假设与 Q6 依据。
3. **login 账号级限速**：`api/auth.py` 复用 `middleware.RateLimiter`，模块级
   双 limiter（`email:` 键 10 次/分、`ip:` 键 30 次/分），超限 429
   「登录尝试过于频繁，请稍后再试」；register 不加（有邀请码门槛，暴力面小）。
4. **/api/docs 收紧**：加 `require_user`（Q3 拍板），模块头注释同步。
5. **API_DOCS 开关**：config 加 `api_docs: bool = False`（行为开关风格参照
   STREAM_ANSWER），`app.py` 条件传 `docs_url/redoc_url/openapi_url=None`；
   本地调试 `.env` 加 `API_DOCS=1` 可开。
6. **scripts/trace-query.sh**：5 处内插 SQL 改 `psql -v` 变量引用。
7. **docker-compose.yml**：web 端口 `"3100:3100"` → `"127.0.0.1:3100:3100"`。

### 2.2 死代码与仓库卫生

1. 删 `apps/web/components/eventStream.tsx`（163 行 compare 遗物）+
   `components/ui/{dropdown-menu,scroll-area,separator,skeleton,textarea}.tsx`
   （五件零引用 shadcn 原语）。
2. `apps/server/pyproject.toml`：删 4 条幽灵 per-file-ignores
   （agent/tx.py、agent/graph.py、agent/routing_prompts.py、
   tests/test_routing.py——文件均已不存在）+ dev 组去重 httpx。
3. **REACT_MODE 删除**：config.py 两行 + .env.example 注释行
   （architecture/08:108 已宣布退役，代码收口）。
4. **compare 枚举退役**：`session/store.py` VALID_KINDS + DDL CHECK、
   `lib/api.ts` SessionKind、`admin/page.tsx` 过滤项、message-parts.tsx 注释。
5. 卫生：`git rm --cached apps/web/.impeccable/config.json`、
   `.mimosa/hook-state/sess_*.json`；.gitignore 补 `.impeccable/` 与根
   `.venv/`；**AGENTS.md 入库**（原则文件不在版本库=克隆即丢）。

### 2.3 文档同步（对齐 P35）

1. **根 README.md 重写**：单循环架构（PG 全库、无路由级联）、邀请制+五页面
   （chat/console/memory/admin/login）、`make run`/`make ingest` 快速开始、
   API 概览指向 08 契约（27 端点、follow_ups）、评测节补
   MEMORY_CONSOLIDATE=off 与认证前提、部署节新增 COOKIE_SECURE/XFF/API_DOCS、
   文档地图更新（runbooks P6~P36、architecture 01~11）。
2. `architecture/01-overview.md`：模块地图改现存文件（graph/routing/tx/state
   四文件已删）、tests 273、web dev 3100、删 classic 基线与双底座句。
3. `architecture/09-cross-cutting.md`：配置表按 config.py 全集重写、删
   QUERY_REWRITE、flash 承担清单收窄、目录速查补 txmeta/emitter/followups。
4. `architecture/10-evaluation.md`：删必败命令 `--mode classic`、口径改 agent-only。
5. architecture 小修：README 索引（外壳图/classic 句、§0.9）、05:7 嵌套外壳
   开头段、07 classic 兜底措辞+state.py 引用、08/09 `CORS_ALLOW_ORIGINS`→
   `CORS_ORIGINS` 笔误。
6. `PARITY.md`：§0.10 注记（Q5 全清单）+ §14 配置表重写（删 CORPUS_DIR/
   INDEX_PATH/MAX_QUESTION_CHARS、RATE_LIMIT 缺省 600、补 ~20 现行变量）+
   §3 注记 tool 字段可为 flow_id（P33 尾巴）。
7. `roadmap.md`：补 P34/P35/P36、修 M4「用户体系 [ ]」与 P21~P23 已勾的矛盾、
   classic 收口措辞更新。
8. `docs/README.md` 索引更新；`.env.example` 对照 config.py 重写；
   `DESIGN.md` version P25→P35 + compare 残留三处；`AGENTS.md` 核心域表述
   更新为 P31 后形态 + 失效开关示例换 RERANK_MODE/STREAM_ANSWER/CHUNK_STRATEGY。

## 3. 风险与守卫

- **XFF 末段 vs 多层代理**：若未来站点前面再加 CDN/二层反代，末段=CDN 出口
  而非真实客户端，限流粒度变粗（退化方向是「多人共享限流桶」，安全侧偏保守，
  可接受）。nginx 配置核对写入部署清单，拓扑变化时须回头改。
- **login 限流误伤**：进程内固定窗口，重启即清零（与全局限流同语义）；正常
  用户 10 次/分/账号足够；测试连跑登录的用例注意夹具窗口隔离。
- **compare 退役 vs 存量数据**：已入库 compare 会话行 SELECT 不受影响
  （CHECK 仅约束 insert/update）；新库 DDL 不再含 compare；admin 列表过滤项
  删除后存量 compare 会话仍在「全部」里可见。
- **错误文案变化 vs 前端**：前端对 500/SSE error 均按通用错误 toast 渲染，
  不解析 detail 原文，改文案无前端联动需求（执行时 grep 复核）。
- **API_DOCS 缺省关闭**：本地开发失去 /docs 交互调试面——.env 加 API_DOCS=1
  即恢复；生产保持关。

## 4. 验收门禁

- 新增/调整测试：client_ip 末段语义、login 双键限速 429、500 笼统 detail
  （grep 现有断言 str(e) 的测试同步改）、kind=compare 422、API_DOCS 开关。
- 全绿：ruff + pytest（PG_DSN 显式指本地）+ `npm run build`。
- 本地真跑：起服 curl 验证 500 文案、login 连续失败 429、未登录 /api/docs
  401、/docs 404。
- 线上验证：/api/health 健康、登录+对话渲染、Set-Cookie 带 Secure、
  nginx XFF 透传方式确认（`$proxy_add_x_forwarded_for` 追加式或
  `$remote_addr` 覆盖式均可，记录实际形态）。

## 5. 遗留与后续

- print→logging 40+ 处迁移（Q2 拍板挂账，建议 P37）。
- trace TTL 30 天清理、admin trace 可视化页（P27 B 期既有挂账）。
- 会话创建配额（低危，观察后再议）。
- run_eval 认证适配、危险词黑名单丰富化（P31 既有挂账）。

## 6. 执行记录

（待回填）
