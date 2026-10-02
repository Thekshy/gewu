#!/usr/bin/env bash
# gewu 线上日志体检（在服务器上运行；本地入口 make log-report）。
# 聚合 journalctl 里 [chat]/[rag]/[routing]/[llm] 四层埋点 → 一页优化向报告：
# 轮次结局分布 / 路由分布 / 延迟分位 / 空命中语料缺口 / 上下文规模。
# 用法：log-report.sh [小时数，默认 24]
set -euo pipefail
python3 - "${1:-24}" <<'PY'
import json
import re
import subprocess
import sys
from collections import Counter

hours = sys.argv[1]
out = subprocess.run(
    ["journalctl", "-u", "gewu-api", "--since", f"-{hours}h", "--no-pager", "-o", "cat"],
    capture_output=True,
    text=True,
).stdout

chats, empty_q, rag_lines, ctx_chars = [], [], [], []
for line in out.splitlines():
    if line.startswith("[chat] "):
        try:
            chats.append(json.loads(line[7:]))
        except json.JSONDecodeError:
            pass
    elif line.startswith("[rag] ") and "空命中" in line:
        m = re.search(r"q='([^']*)", line)
        if m:
            empty_q.append(m.group(1))
    elif line.startswith("[rag] "):
        rag_lines.append(line)
    elif line.startswith("[llm] "):
        m = re.search(r"chars=(\d+)", line)
        if m:
            ctx_chars.append(int(m.group(1)))

if not chats and not rag_lines:
    print("窗口内无埋点日志（服务刚启动或时段无流量）")
    sys.exit(0)

print(f"═ 概览：{len(chats)} 轮对话 ═")
if chats:
    print("结局分布：", dict(Counter(c.get("reason", "?") for c in chats)))
    print("路由分布：", dict(Counter(c.get("route") or "?" for c in chats)))
    ms = sorted(c.get("ms", 0) for c in chats)
    if ms:
        p95 = ms[min(len(ms) - 1, int(len(ms) * 0.95))]
        print(f"延迟：均值 {sum(ms) // len(ms)}ms / P95 {p95}ms / 最慢 {ms[-1]}ms")
    slow = sorted(chats, key=lambda c: -c.get("ms", 0))[:3]
    print("最慢三轮：")
    for c in slow:
        print(f"  {c.get('ms')}ms  {c.get('reason')}  q={c.get('q', '')[:40]}")
    multi = [c for c in chats if c.get("steps", 0) > 0]
    print(f"多步研究轮：{len(multi)}/{len(chats)}")

print(f"═ 检索：{len(rag_lines)} 次命中调用，{len(empty_q)} 次空命中 ═")
if empty_q:
    print("空命中问题（= 语料缺口清单，补语料的第一优先级）：")
    for q in empty_q:
        print(f"  - {q}")

if ctx_chars:
    print(f"═ 上下文规模：{len(ctx_chars)} 次 LLM 调用，chars 均值 "
          f"{sum(ctx_chars) // len(ctx_chars)} / 最大 {max(ctx_chars)} ═")
PY
