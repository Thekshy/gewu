#!/usr/bin/env python3
"""检索 A/B 对照：同一查询集打两个 /api/search 端点，逐位对比 top-k。

用法（两个端点需用同一份语料各自完成入库）：
  BASE_A=http://127.0.0.1:8001 BASE_B=http://127.0.0.1:8000 \
    python3 eval/compare_search.py [--k 5] [--name bm25-only]

对比口径：命中列表按 (doc_id, seq) 逐位比对（chunk_id 在两侧存储不同，
不参与比较）。报告写入 eval/reports/（仅 basename，防路径穿越）。
"""

from __future__ import annotations

import argparse
import datetime as dt
import http.client
import json
import os
import sys
import urllib.parse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE_A = os.environ.get("BASE_A", "http://127.0.0.1:8001")  # 冻结单体
BASE_B = os.environ.get("BASE_B", "http://127.0.0.1:8000")  # 微服务网关


def search(base: str, query: str, k: int) -> list[dict]:
    parts = urllib.parse.urlsplit(base if "://" in base else "http://" + base)
    conn = http.client.HTTPConnection(parts.hostname, parts.port or 80, timeout=30)
    conn.request("POST", "/api/search", body=json.dumps({"query": query, "k": k}),
                 headers={"Content-Type": "application/json"})
    resp = conn.getresponse()
    data = json.loads(resp.read().decode("utf-8"))
    resp.close()
    conn.close()
    return data


def sig(hits: list[dict]) -> list[tuple[str, int]]:
    return [(h["doc_id"], h["seq"]) for h in hits]


def main() -> int:
    parser = argparse.ArgumentParser(description="检索 A/B 逐位对照")
    parser.add_argument("--k", type=int, default=5)
    parser.add_argument("--name", default=None, help="报告文件名（写入 eval/reports/）")
    args = parser.parse_args()

    queries = [json.loads(l) for l in
               (ROOT / "eval" / "search-queries.jsonl").read_text("utf-8").splitlines() if l.strip()]
    diff = 0
    rows = []
    for q in queries:
        try:
            a = sig(search(BASE_A, q["query"], args.k))
            b = sig(search(BASE_B, q["query"], args.k))
        except Exception as exc:  # noqa: BLE001
            rows.append((q["note"], q["query"], None, None, f"请求失败：{exc}"))
            diff += 1
            continue
        same = a == b
        if not same:
            diff += 1
        rows.append((q["note"], q["query"], a, b, None if same else "不一致"))

    lines = [
        "# 检索 A/B 对照报告（/api/search top-k 逐位）",
        "",
        f"- 时间：{dt.datetime.now().strftime('%Y-%m-%d %H:%M')}",
        f"- A（基线）：{BASE_A}（冻结单体 tag go-monolith）",
        f"- B（被测）：{BASE_B}（微服务网关）",
        f"- 查询集：{len(queries)} 条（26 题问题 + 补充政策词，eval/search-queries.jsonl），k={args.k}",
        f"- 口径：命中按 (doc_id, seq) 逐位比对",
        "",
        f"## 结论：{'全部逐位一致 ✅' if diff == 0 else f'{diff} 条不一致 ❌'}",
        "",
        "| 查询 | A 命中 | B 命中 | 结果 |",
        "| --- | --- | --- | --- |",
    ]
    for note, query, a, b, err in rows:
        if err:
            lines.append(f"| {note} | - | - | {err} |")
            continue
        fa = " ".join(f"{d}#{s}" for d, s in a) or "(空)"
        fb = " ".join(f"{d}#{s}" for d, s in b) or "(空)"
        flag = "✓" if a == b else "✗"
        lines.append(f"| {note} {query[:24]} | {fa} | {fb} | {flag} |")

    name = (args.name or f"search-ab-{dt.datetime.now().strftime('%Y%m%d-%H%M')}") + ".md"
    report_dir = (ROOT / "eval" / "reports").resolve()
    report = report_dir / name.replace("/", "_")  # 报告只落 eval/reports/
    report_dir.mkdir(parents=True, exist_ok=True)
    report.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"{'一致' if diff == 0 else f'{diff} 条不一致'}；报告已写入 {report}")
    return 0 if diff == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
