#!/usr/bin/env python3
"""离线评测：在 eval/dataset.jsonl 上跑问答管线，产出 Markdown 报告。

指标：
- factual / multi_hop：关键词命中（gold_keywords 出现在回答中）、引用召回（expected_docs 命中引用）
- refusal：是否正确拒答
- 延迟：按问题类型统计

用法：python eval/run_eval.py [--type factual|multi_hop|refusal] [--limit N]
不配置 LLM_API_KEY 时以检索演示模式运行，指标仅反映检索层。
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load_dotenv() -> None:
    env = ROOT / ".env"
    if not env.exists():
        return
    for line in env.read_text("utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, val = line.partition("=")
            os.environ.setdefault(key.strip(), val.strip())


_load_dotenv()
sys.path.insert(0, str(ROOT / "apps" / "api"))

from app import llm  # noqa: E402
from app.agent.pipeline import run_chat  # noqa: E402
from app.config import get_settings  # noqa: E402


def run_one(item: dict) -> dict:
    t0 = time.perf_counter()
    events = list(run_chat(item["question"], item.get("mode", "auto")))
    route = next((e["route"] for e in events if e["type"] == "route"), None)
    answer = "".join(e["text"] for e in events if e["type"] == "answer_delta")
    cited = sorted({c["doc_id"] for e in events if e["type"] == "citations" for c in e["items"]})
    latency_ms = next(
        (e["latency_ms"] for e in reversed(events) if e["type"] == "done"), None
    )
    return {
        "route": route,
        "answer": answer,
        "cited": cited,
        "latency_ms": latency_ms if latency_ms is not None else int((time.perf_counter() - t0) * 1000),
    }


def _norm(s: str) -> str:
    """忽略空白差异：中文排版常在数字前后加空格（如「15 元」）。"""
    return "".join(s.split())


def score(item: dict, result: dict) -> dict:
    if item["type"] == "refusal":
        refused = result["route"] == "refusal" or "只能回答" in result["answer"]
        return {"pass": refused, "kw": None, "cite": None}
    kws = item.get("gold_keywords", [])
    kw_hit = any(_norm(k) in _norm(result["answer"]) for k in kws) if kws else None
    expected = set(item.get("expected_docs", []))
    cite_hit = bool(expected & set(result["cited"])) if expected else None
    passed = (kw_hit is not False) and (cite_hit is not False)
    return {"pass": passed, "kw": kw_hit, "cite": cite_hit}


def main() -> int:
    parser = argparse.ArgumentParser(description="格物离线评测")
    parser.add_argument("--type", dest="type_", choices=["factual", "multi_hop", "refusal"])
    parser.add_argument("--limit", type=int)
    args = parser.parse_args()

    settings = get_settings()
    dataset_path = ROOT / "eval" / "dataset.jsonl"
    items = [json.loads(line) for line in dataset_path.read_text("utf-8").splitlines() if line.strip()]
    if args.type_:
        items = [it for it in items if it["type"] == args.type_]
    if args.limit:
        items = items[: args.limit]

    rows = []
    for item in items:
        result = run_one(item)
        s = score(item, result)
        rows.append({"item": item, "result": result, "score": s})
        flag = "✓" if s["pass"] else "✗"
        print(f"  {flag} {item['id']:<12} route={result['route']:<8} {result['latency_ms']:>6}ms")

    def rate(sel) -> str:
        vals = [r["score"]["pass"] for r in sel]
        return f"{sum(vals)}/{len(vals)}" if vals else "-"

    def kw_rate(sel) -> str:
        vals = [r["score"]["kw"] for r in sel if r["score"]["kw"] is not None]
        return f"{sum(vals)}/{len(vals)}" if vals else "-"

    def cite_rate(sel) -> str:
        vals = [r["score"]["cite"] for r in sel if r["score"]["cite"] is not None]
        return f"{sum(vals)}/{len(vals)}" if vals else "-"

    def avg_latency(sel) -> str:
        vals = [r["result"]["latency_ms"] for r in sel]
        return f"{sum(vals) / len(vals):.0f}ms" if vals else "-"

    factual = [r for r in rows if r["item"]["type"] == "factual"]
    multi = [r for r in rows if r["item"]["type"] == "multi_hop"]
    refu = [r for r in rows if r["item"]["type"] == "refusal"]

    lines = [
        "# 评测报告",
        "",
        f"- 时间：{dt.datetime.now().strftime('%Y-%m-%d %H:%M')}",
        f"- 模型：{settings.llm_model}（LLM {'启用' if llm.has_key() else '未启用（检索演示模式）'}）",
        f"- 数据集：{len(rows)} 题（factual {len(factual)} / multi_hop {len(multi)} / refusal {len(refu)}）",
        "",
        "| 类型 | 通过率 | 关键词命中 | 引用召回 | 平均延迟 |",
        "| --- | --- | --- | --- | --- |",
        f"| factual | {rate(factual)} | {kw_rate(factual)} | {cite_rate(factual)} | {avg_latency(factual)} |",
        f"| multi_hop | {rate(multi)} | {kw_rate(multi)} | {cite_rate(multi)} | {avg_latency(multi)} |",
        f"| refusal | {rate(refu)} | - | - | {avg_latency(refu)} |",
        "",
        "## 明细",
        "",
        "| ID | 问题 | 路由 | 通过 | 关键词 | 引用 | 延迟 |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for r in rows:
        it, res, sc = r["item"], r["result"], r["score"]
        kw = "-" if sc["kw"] is None else ("✓" if sc["kw"] else "✗")
        cite = "-" if sc["cite"] is None else ("✓" if sc["cite"] else "✗")
        q = it["question"].replace("|", "\\|")[:30]
        lines.append(
            f"| {it['id']} | {q} | {res['route']} | {'✓' if sc['pass'] else '✗'} | {kw} | {cite} | {res['latency_ms']}ms |"
        )

    report_dir = ROOT / "eval" / "reports"
    report_dir.mkdir(exist_ok=True)
    out = report_dir / f"report-{dt.datetime.now().strftime('%Y%m%d-%H%M')}.md"
    out.write_text("\n".join(lines) + "\n", "utf-8")
    print(f"\n报告已写入：{out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
