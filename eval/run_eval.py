#!/usr/bin/env python3
"""离线评测：在 eval/dataset.jsonl 上跑问答管线，产出 Markdown 报告。

两类题型：
- 单轮（factual / multi_hop / refusal）：关键词命中、引用召回、拒答正确性
- 多轮（transaction / hybrid）：驱动完整对话，断言业务库真实状态
  （预约/请假单是否生成、冲突是否恢复、权限是否拦截）

用法：python eval/run_eval.py [--type ...] [--limit N]
不配置 LLM_API_KEY 时以检索演示模式运行，交易链路走确定性解析，全部可跑。
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import time
from dataclasses import dataclass, field
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
from app.agent.session import sessions  # noqa: E402
from app.agent.tools import business  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.dates import parse as parse_date  # noqa: E402


def _norm(s: str) -> str:
    """忽略空白差异：中文排版常在数字前后加空格（如「15 元」）。"""
    return "".join(s.split())


@dataclass
class RunAgg:
    routes: set = field(default_factory=set)
    answer: str = ""
    cited: set = field(default_factory=set)
    asked_slot: bool = False
    pending_tools: set = field(default_factory=set)
    results: list = field(default_factory=list)
    errors: list = field(default_factory=list)
    latency_ms: int = 0


def _run_turns(sid: str, turns: list[str], role: str) -> RunAgg:
    agg = RunAgg()
    t0 = time.perf_counter()
    for turn in turns:
        for ev in run_chat(turn, "auto", session_id=sid, role=role, user="eval-user"):
            et = ev["type"]
            if et == "route":
                agg.routes.add(ev["route"])
            elif et == "answer_delta":
                agg.answer += ev.get("text", "")
            elif et == "citations":
                agg.cited.update(c["doc_id"] for c in ev.get("items", []))
            elif et == "slot_question":
                agg.asked_slot = True
            elif et == "pending_action":
                agg.pending_tools.add(ev["tool"])
            elif et == "action_result":
                agg.results.append(ev)
            elif et == "error":
                agg.errors.append(ev.get("message", ""))
            elif et == "done":
                agg.latency_ms += int(ev.get("latency_ms", 0))
    agg.latency_ms = agg.latency_ms or int((time.perf_counter() - t0) * 1000)
    return agg


def _expect_ok(exp: dict, agg: RunAgg) -> bool:
    checks = []

    if "route" in exp:
        checks.append(exp["route"] in agg.routes)
    if "asked_slot" in exp:
        checks.append(agg.asked_slot == exp["asked_slot"])
    if "pending_tool" in exp:
        checks.append(exp["pending_tool"] in agg.pending_tools)
    if "success" in exp:
        checks.append(any(r["success"] for r in agg.results) == exp["success"])
    if "denied" in exp:
        checks.append(
            any((not r["success"]) and "无权" in r.get("message", "") for r in agg.results)
            == exp["denied"]
        )
    if "conflict_recovered" in exp:
        checks.append(
            any(not r["success"] for r in agg.results) and any(r["success"] for r in agg.results)
        )

    if "booking" in exp:
        want = exp["booking"]
        date = parse_date(want["date_text"]).isoformat() if want.get("date_text") else want.get("date")
        matches = [
            b
            for b in business.all_bookings()
            if want.get("venue_contains", "") in b["venue"]
            and (not date or b["date"] == date)
            and (not want.get("slot") or b["slot"] == want["slot"])
        ]
        checks.append(bool(matches))
    if "bookings_count" in exp:
        checks.append(len(business.all_bookings()) == exp["bookings_count"])

    if "ticket" in exp:
        want = exp["ticket"]
        tickets = business.all_tickets()
        last = tickets[-1] if tickets else None
        checks.append(
            last is not None
            and want.get("days", last["days"]) == last["days"]
            and want.get("approver", last["approver"]) == last["approver"]
        )

    if "citations_include" in exp:
        checks.append(set(exp["citations_include"]) <= agg.cited)
    if "answer_contains" in exp:
        checks.append(all(w in agg.answer for w in exp["answer_contains"]))

    return all(checks) if checks else False


def score_single(item: dict, agg: RunAgg) -> dict:
    if item["type"] == "refusal":
        refused = "refusal" in agg.routes or "只能回答" in agg.answer
        return {"pass": refused, "kw": None, "cite": None}
    kws = item.get("gold_keywords", [])
    kw_hit = any(_norm(k) in _norm(agg.answer) for k in kws) if kws else None
    expected = set(item.get("expected_docs", []))
    cite_hit = bool(expected & agg.cited) if expected else None
    passed = (kw_hit is not False) and (cite_hit is not False)
    return {"pass": passed, "kw": kw_hit, "cite": cite_hit}


def main() -> int:
    parser = argparse.ArgumentParser(description="格物离线评测")
    parser.add_argument("--type", dest="type_", choices=["factual", "multi_hop", "refusal", "transaction", "hybrid"])
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
        sid = f"eval-{item['id']}"
        sessions.clear(sid)
        multi = "turns" in item
        if multi:
            business.reset()
            agg = _run_turns(sid, item["turns"], item.get("role", "student"))
            s = {"pass": _expect_ok(item.get("expect", {}), agg) and not agg.errors, "kw": None, "cite": None}
        else:
            agg = _run_turns(sid, [item["question"]], "student")
            s = score_single(item, agg)

        rows.append({"item": item, "agg": agg, "score": s, "multi": multi})
        flag = "✓" if s["pass"] else "✗"
        extra = f" routes={sorted(agg.routes)}" if multi else ""
        print(f"  {flag} {item['id']:<12} {agg.latency_ms:>5}ms{extra}")

    def sel(t: str) -> list:
        return [r for r in rows if r["item"]["type"] == t]

    def rate(rs) -> str:
        return f"{sum(1 for r in rs if r['score']['pass'])}/{len(rs)}" if rs else "-"

    def kw_rate(rs) -> str:
        vals = [r["score"]["kw"] for r in rs if r["score"]["kw"] is not None]
        return f"{sum(vals)}/{len(vals)}" if vals else "-"

    def cite_rate(rs) -> str:
        vals = [r["score"]["cite"] for r in rs if r["score"]["cite"] is not None]
        return f"{sum(vals)}/{len(vals)}" if vals else "-"

    def avg_latency(rs) -> str:
        return f"{sum(r['agg'].latency_ms for r in rs) / len(rs):.0f}ms" if rs else "-"

    types = ["factual", "multi_hop", "refusal", "transaction", "hybrid"]
    lines = [
        "# 评测报告",
        "",
        f"- 时间：{dt.datetime.now().strftime('%Y-%m-%d %H:%M')}",
        f"- 模型：{settings.llm_model}（LLM {'启用' if llm.has_key() else '未启用（离线确定性链路）'}）",
        f"- 数据集：{len(rows)} 题（" + "，".join(f"{t} {len(sel(t))}" for t in types if sel(t)) + "）",
        "",
        "| 类型 | 通过率 | 关键词命中 | 引用召回 | 平均延迟 |",
        "| --- | --- | --- | --- | --- |",
    ]
    label = {"transaction": "transaction（办理）", "hybrid": "hybrid（问答+办理）"}
    for t in types:
        rs = sel(t)
        if not rs:
            continue
        kw, cite = ("-", "-") if t in ("refusal", "transaction", "hybrid") else (kw_rate(rs), cite_rate(rs))
        lines.append(f"| {label.get(t, t)} | {rate(rs)} | {kw} | {cite} | {avg_latency(rs)} |")

    lines += ["", "## 明细", "", "| ID | 类型 | 多轮 | 通过 | 延迟 | 说明 |", "| --- | --- | --- | --- | --- | --- |"]
    for r in rows:
        it = r["item"]
        note = []
        if r["multi"]:
            note.append(f"routes={','.join(sorted(r['agg'].routes)) or '-'}")
        if r["agg"].errors:
            note.append(f"错误：{r['agg'].errors[0][:40]}")
        lines.append(
            f"| {it['id']} | {it['type']} | {'✓' if r['multi'] else '-'} | "
            f"{'✓' if r['score']['pass'] else '✗'} | {r['agg'].latency_ms}ms | {'；'.join(note)} |"
        )

    report_dir = ROOT / "eval" / "reports"
    report_dir.mkdir(exist_ok=True)
    out = report_dir / f"report-{dt.datetime.now().strftime('%Y%m%d-%H%M')}.md"
    out.write_text("\n".join(lines) + "\n", "utf-8")
    print(f"\n报告已写入：{out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
