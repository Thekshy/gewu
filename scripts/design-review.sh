#!/usr/bin/env bash
# P34-3 视觉评审回路（DESIGN.md workflow ⑤ 的固化入口）
# 作用：build 产物起临时服务 → 输出截图清单（亮暗 × 关键页）→ 会话内
#       chrome-devtools MCP 按清单截图存档 → 对照参照参数评审 → 差距回修。
# 截图存 docs/runbooks/assets/p34/review-<ts>/，评审结论形容词无效（参数化）。
set -euo pipefail
cd "$(dirname "$0")/.."

PORT="${DESIGN_REVIEW_PORT:-3210}"
TS="$(date +%Y%m%d-%H%M%S)"
OUT="docs/runbooks/assets/p34/review-${TS}"
PAGES=("/" "/console" "/memory" "/admin")

echo "[design-review] build 产物起临时服务 :${PORT}（${OUT} 留档）"
mkdir -p "${OUT}"

(cd apps/web && npx next start -p "${PORT}" &) 
sleep 2
if ! curl -s --noproxy '*' -o /dev/null "http://localhost:${PORT}/"; then
  echo "[design-review] 服务未起，先跑 apps/web: npm run build" >&2
  exit 1
fi

cat > "${OUT}/CHECKLIST.md" << EOF
# 视觉评审清单 ${TS}

对照 DESIGN.md referencing 节参数逐项判（有/无/部分）——形容词结论无效。

| 页面 | 亮色 | 暗色 | 评审要点 |
|------|------|------|----------|
| /    | ☐    | ☐    | 空态三时刻（BlurText 断行/任务卡/氛围光）、消息流、composer focus 微光 |
| /console | ☐ | ☐ | 工具页零改原则（P34 拨盘分层） |
| /memory  | ☐ | ☐ | 同上 |
| /admin   | ☐ | ☐ | 同上 |

动效复核：入场 180ms expo-out；交互 120-180ms；循环呼吸类不提速。
动效检查须真跑一轮剧本（首答 stagger / 回执 spring / 轮换 placeholder）。
EOF

echo "[design-review] 服务已起 http://localhost:${PORT}（结束后手动停掉 :${PORT} 进程）"
echo "[design-review] 清单与截图落 ${OUT}/CHECKLIST.md"
echo "[design-review] 下一步：chrome-devtools 逐页截图（亮=默认，暗=html 加 .dark 类）存 ${OUT}/"
