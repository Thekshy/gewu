# P19 compare 对照语义收口 + P18 真跑补验（任务书 + 执行记录）

> **背景**：P18（Claude 暖编辑风，commit bdfb06d）收尾时发现两件遗留：① compare
> 页 A/B 轨的用户可见文案仍是 P17 之前的旧语义（A 轨写「级联路由+固定 workflow」、
> B 轨写「ReAct 自主组合工具」），而代码实际 A=mode=auto（P17 起=agent-first
> 单循环）、B=mode=classic（级联基线）——双底座对照是毕设答辩演示面，文案必须与
> 行为一致；② P18 验收被 LLM key 5 小时限额（429，13:47:52 重置）拦住直答成功态，
> 当时以临时预览页组件矩阵代验，需配额恢复后真跑补验。

## 0. 拍板结论

| # | 问题 | 拍板 | 依据 |
|---|------|------|------|
| Q1 | 范围 | 文案收口（前端零逻辑改动）+ 真跑补验，不新开功能面 | 两者同为 P18 收尾，合一任务书 |
| Q2 | chat 页 mode=react 选项 | **保留不动** | 后端 `mode_dispatch`：auto/react 同路进 agent 子图（graph.py:149），react 是历史别名；P17 拍板「前端零重构」，枚举语义收口留后_backlog |
| Q3 | 补验时点 | 定时 13:55（配额 13:47:52 重置 + 8min 缓冲）自动化执行 | 用户要求直接执行，不等人在场 |

## 1. 目标 / 非目标

**目标**
- compare 页用户可见文案全面对齐 P17 双底座语义：A=agent-first 单循环（主路）、
  B=classic 级联（论文对照基线）；涉及副标题、两轨 title/note、差异表表头与路由判定行。
- P18 补验：配额恢复后真跑①直答（流式回答+引用行+DoneMeta）②办理确认流
  （槽位→ConfirmCard→回执）亮色截图目检；验后 `/api/business/reset` 还原演示数据。
- `tsc` + `next build` 绿；pathspec 限定提交。

**非目标**
- 不动 mode 枚举与后端（git diff apps/server 必须为空）；不动 SSE 契约。
- 不改 eventStream.tsx 结构（TrackPanel 文案由 props 传入，改 compare 页传参即可）。

## 2. 改动盘点

| 文件 | 动作 |
|---|---|
| `apps/web/app/compare/page.tsx` | 副标题/A/B 轨 title+note/差异表表头与 B 轨路由单元格文案 |
| `docs/runbooks/P19-*.md` | 本任务书 + 执行记录 |

## 3. 真跑补验清单（定时任务执行）

1. 前置：:8000 API 与 :3100 web 在跑（不在则按 Makefile 口径拉起）。
2. 直答：对话页发「转专业之后原课程绩点还算吗？会影响保研吗？」→ 截图（流式
   回答/引用来源/DoneMeta/RouteBadge，亮色）。
3. 办理流：发「帮我预约明天晚上的羽毛球馆打班级比赛」→ 槽位追问/ConfirmCard →
   确认 → 回执截图；**验后调 `/api/business/reset`**。
4. 回填本任务书 §4 执行记录，pathspec 限定 commit（仅 docs/runbooks/P19*）。
5. 若仍 429：如实记录并结束（不重试循环），由用户后续手动触发。

## 4. 执行记录（2026-10-01 晚 ~ 10-02 中午补验完成）

**P19-1（已提交 6df13ce）**：compare 页文案四处收口，tsc+build 绿，详见 commit。

**P19-2 补验结果：契约级全通过，视觉级被设计演进取代（如实记录）**：

- **配额恢复确认**（10-01 13:50 后）：直答 SSE 零 429。
- **直答全链路**（curl SSE，session p19-verify-direct-1）：route×2（两段式）+
  status×2 + step×4 + answer_delta×N + citations + done，事件序列完整，引用
  命中转专业办法/学分认定/推免细则三篇。
- **办理确认流**（session p19-verify-tx-1 两轮）：首轮一步收齐四槽位 →
  pending_action（book_venue，2026-10-02 19:00-21:00 羽毛球馆）→「确认」→
  action_result success，回执 **VE-0270** 落库；验后 `/api/business/reset`
  还原（bookings/tickets 双空确认）。
- **视觉目检未按原计划执行**：补验窗口期间项目并行推进 P20~P28（P25 经用户
  复拍板将 P18 暖编辑风整体换肤为 america.gov 机构蓝，DESIGN.md 已改 P25 版，
  且 P25 自带完整浏览器真跑留档），P18 形态的直答成功态截图已无当代意义；
  P18 时点的成功态视觉已由当日的临时预览页组件矩阵（亮暗双主题）覆盖。
- **环境坑三条**（后续会撞）：① 本机 shell 挂 127.0.0.1:7897 代理且 no_proxy
  为空，localhost curl 必须加 `--noproxy '*'` 否则 502/000 假象；② dev server
  与 next build 共用 .next 互踩（P18 已记，本轮再犯一次：build 后 dev 全页
  chunks 404，rm -rf .next 重启即愈）；③ MCP chrome profile 残留实例占用需
  pkill 后重连，本轮发生两次。
