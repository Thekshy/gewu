#!/usr/bin/env bash
# P20 设计门禁（DESIGN.md 契约的机械执行）：
#   1) impeccable detect 三页扫描 —— 必须跑生产构建：dev server 会间歇性吐出
#      未编译完的空 Tailwind CSS，扫描结果不稳定（P20-4 实测）
#   2) grep 规则：衬线域泄漏 / h-screen / 冷色残留
# exit 0 = 全绿；非 0 = 有 finding（逐条修或在 DESIGN.md 白名单区记录理由）。
# 注：引擎 4.x 对零 finding 的页面不打印文本，脚本按退出码显式报 PASS/FAIL。
set -u
cd "$(dirname "$0")/.."

WEB=apps/web
PORT=3200
BASE="http://localhost:$PORT"
fail=0

cleanup() {
  # 只清理本脚本自己起的 server，不动外部已在跑的实例
  if [ "${started:-0}" = "1" ]; then
    lsof -ti tcp:"$PORT" 2>/dev/null | xargs kill 2>/dev/null || true
  fi
}
trap cleanup EXIT

# --- 生产构建：默认复用 .next，REBUILD=1 强制重建 ---
if [ ! -f "$WEB/.next/BUILD_ID" ] || [ "${REBUILD:-0}" = "1" ]; then
  echo "[design-lint] 构建生产包（next build）…"
  (cd "$WEB" && npm run build) || { echo "[design-lint] FAIL: next build 失败"; exit 1; }
fi

# --- 生产 server：没起就临时起 ---
started=0
if ! curl -sf --noproxy '*' -o /dev/null "$BASE"; then
  echo "[design-lint] 生产 server 不在，临时启动 :${PORT} ..."
  (cd "$WEB" && exec env NODE_OPTIONS="--localstorage-file=/tmp/gewu-web-ls" npx next start -p "$PORT") \
    >/tmp/gewu-design-lint-server.log 2>&1 &
  started=1
  ok=0
  for _ in $(seq 1 30); do
    if curl -sf --noproxy '*' -o /dev/null "$BASE"; then ok=1; break; fi
    sleep 1
  done
  if [ "$ok" != "1" ]; then
    echo "[design-lint] FAIL: server 起不来，日志见 /tmp/gewu-design-lint-server.log"
    exit 1
  fi
fi

# --- impeccable detect 四页（exit 0=clean / 2=有 finding / 1=扫描失败）---
# 必须在 apps/web 下执行：检测器从 cwd 读取 .impeccable/config.json 白名单
# P21 起 /login 入检测清单（登录/注册表单页）
for path in "" "/compare" "/console" "/login"; do
  name=${path:-/}
  if (cd "$WEB" && npx -y impeccable detect "$BASE$path" >/tmp/gewu-design-lint-detect.log 2>&1); then
    echo "[design-lint] PASS detect $name"
  else
    echo "[design-lint] FAIL detect ${name}："
    cat /tmp/gewu-design-lint-detect.log
    fail=1
  fi
done

# --- grep 规则 ---
echo "[design-lint] grep 规则：衬线域 / h-screen / 冷色"

serif_bad=$(grep -rl "font-display" "$WEB/app" "$WEB/components" --include="*.tsx" --include="*.ts" 2>/dev/null \
  | grep -vE "app/layout\.tsx$|app/page\.tsx$|components/answer\.tsx$")
if [ -n "$serif_bad" ]; then
  echo "FAIL 衬线域泄漏：font-display 只允许 layout.tsx(品牌)/page.tsx(空态)/answer.tsx(内容时刻)"
  echo "$serif_bad"
  fail=1
fi

hs=$(grep -rn "h-screen" "$WEB/app" "$WEB/components" --include="*.tsx" 2>/dev/null)
if [ -n "$hs" ]; then
  echo "FAIL 视口稳定性：使用 h-screen（应 min-h/h-dvh，iOS Safari 地址栏跳动）"
  echo "$hs"
  fail=1
fi

cold=$(grep -rniE "(cyan|teal|indigo|sky|violet|fuchsia)-[0-9]{2,3}" \
  "$WEB/app" "$WEB/components" --include="*.tsx" --include="*.css" 2>/dev/null)
if [ -n "$cold" ]; then
  echo "FAIL 冷色残留（P18 起退役，两头都是「默认脸」，契约见 DESIGN.md）"
  echo "$cold"
  fail=1
fi

if [ "$fail" = "0" ]; then
  echo "[design-lint] 全绿"
fi
exit "$fail"
