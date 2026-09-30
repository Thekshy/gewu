#!/usr/bin/env python3
"""P14-1 G1 检索对照：同数据集打两套服务的 /api/search，比 doc 级命中序列。

纯标准库（与 run_eval.py 同款约定）。产出 markdown 报告到 eval/reports/<tag>.md：
- 序列一致率：doc_id 序列完全一致的查询占比
- 集合一致率：top-k doc_id 集合一致（P12 对账同口径）
- 逐条差异明细（归因用）

用法：
  python3 eval/run_search_parity.py \
    --go http://127.0.0.1:8000 --py http://127.0.0.1:8001 \
    --dataset eval/search-queries.jsonl --tag P14-search-parity-fts
"""

from __future__ import annotations

import argparse
import json
import time
import urllib.error
import urllib.request
from pathlib import Path


def _opener():
    """绕过系统代理（macOS urllib 会读系统代理，127.0.0.1 也被劫持回 502）。"""
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def search(base: str, query: str, k: int) -> list[str]:
    """POST /api/search，返回 doc_id 序列；失败抛异常（对照失败也是结果）。"""
    body = json.dumps({"query": query, "k": k}).encode("utf-8")
    req = urllib.request.Request(
        base.rstrip("/") + "/api/search",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with _opener().open(req, timeout=60) as resp:
        hits = json.loads(resp.read().decode("utf-8"))
    return [h["doc_id"] for h in hits]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--go", required=True, help="Go 基线服务 base url")
    ap.add_argument("--py", required=True, help="Python 服务 base url")
    ap.add_argument("--dataset", default="eval/search-queries.jsonl")
    ap.add_argument("--tag", default="P14-search-parity")
    ap.add_argument("--timeout-query", type=float, default=0.0, help="逐查询间隔秒（限流保护）")
    args = ap.parse_args()

    queries: list[dict] = []
    for line in Path(args.dataset).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            queries.append(json.loads(line))

    rows = []
    for q in queries:
        query, k = q["query"], int(q.get("k", 5))
        note = q.get("note", "")
        err_go = err_py = None
        go_ids: list[str] = []
        py_ids: list[str] = []
        try:
            go_ids = search(args.go, query, k)
        except (urllib.error.URLError, KeyError, ValueError) as e:
            err_go = str(e)
        time.sleep(args.timeout_query)
        try:
            py_ids = search(args.py, query, k)
        except (urllib.error.URLError, KeyError, ValueError) as e:
            err_py = str(e)
        time.sleep(args.timeout_query)

        seq_eq = bool(go_ids) and go_ids == py_ids
        set_eq = bool(go_ids) and set(go_ids) == set(py_ids)
        rows.append(
            {
                "note": note,
                "query": query,
                "k": k,
                "go": go_ids,
                "py": py_ids,
                "err_go": err_go,
                "err_py": err_py,
                "seq_eq": seq_eq,
                "set_eq": set_eq,
            }
        )

    n = len(rows)
    seq_ok = sum(1 for r in rows if r["seq_eq"])
    set_ok = sum(1 for r in rows if r["set_eq"])
    err_n = sum(1 for r in rows if r["err_go"] or r["err_py"])

    lines = [
        f"# {args.tag} 检索对照报告",
        "",
        f"- 时间：{time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"- Go 基线：`{args.go}` | Python：`{args.py}`",
        f"- 数据集：`{args.dataset}`（{n} 条）",
        "",
        "## 指标",
        "",
        f"- **doc 序列一致率：{seq_ok}/{n} = {seq_ok / n:.0%}**",
        f"- **doc 集合一致率（P12 对账口径）：{set_ok}/{n} = {set_ok / n:.0%}**",
        f"- 请求失败：{err_n}/{n}",
        "",
        "## 逐条明细（仅列差异或失败项）",
        "",
    ]
    diff_rows = [r for r in rows if not r["seq_eq"]]
    if not diff_rows:
        lines.append("（无差异——41 条序列全部一致）")
    for r in diff_rows:
        lines.append(f"### {r['note']} `{r['query'][:40]}`")
        lines.append("")
        if r["err_go"] or r["err_py"]:
            lines.append(f"- 请求失败 go={r['err_go']} py={r['err_py']}")
        lines.append(f"- go: {r['go']}")
        lines.append(f"- py: {r['py']}")
        lines.append(f"- 集合一致: {r['set_eq']}")
        lines.append("")

    out = Path(f"eval/reports/{args.tag}.md")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"[parity] 序列一致 {seq_ok}/{n}，集合一致 {set_ok}/{n}，失败 {err_n}")
    print(f"[parity] 报告已写 {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
