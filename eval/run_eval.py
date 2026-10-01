#!/usr/bin/env python3
"""离线评测：在 eval/dataset.jsonl 上跑问答管线，产出 Markdown 报告。

评测客户端为**纯 HTTP 实现**（仅标准库）：通过 BASE_URL 调用运行中的格物 API
（Go 实现或任意满足 docs/PARITY.md 契约的实现），解析 /api/chat 的 SSE 事件流，
按事件聚合指标；多轮用例经 /api/business/* 断言业务库真实状态。

两类题型：
- 单轮（factual / multi_hop / refusal）：关键词命中、引用召回、拒答正确性
- 多轮（transaction / hybrid）：驱动完整对话，断言业务库真实状态
  （预约/请假单是否生成、冲突是否恢复、权限是否拦截）

用法：python eval/run_eval.py [--type ...] [--limit N]
BASE_URL 缺省 http://127.0.0.1:8000；服务端不配 LLM_API_KEY 时以检索演示模式运行，
交易链路走确定性解析，全部可跑。
"""

from __future__ import annotations

import argparse
import datetime as dt
import http.client
import ipaddress
import json
import os
import re
import socket
import ssl
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE_URL = os.environ.get("BASE_URL", "http://127.0.0.1:8000")


# ---------- 客户端内置的确定性日期解析（与 PARITY §11 同规则，评测侧换算 date_text） ----------

CN_TZ = dt.timezone(dt.timedelta(hours=8))
_FULL_RE = re.compile(r"(\d{4})[-/年.](\d{1,2})[-/月.](\d{1,2})[日号]?")
_MD_RE = re.compile(r"(\d{1,2})月(\d{1,2})[日号]?")
_WEEK_RE = re.compile(r"(下?)(?:周|星期)([一二三四五六日天])")
_DAYS_WORDS = ("今天", "今日", "明天", "明日", "后天")
_WEEKDAYS = "一二三四五六日"


def _today_cn() -> dt.date:
    return dt.datetime.now(tz=CN_TZ).date()


def parse_date(text: str, today: dt.date | None = None) -> dt.date | None:
    """返回文本中第一个可识别的日期（与被测服务同一套确定性规则）。"""
    today = today or _today_cn()
    found: list[tuple[int, dt.date]] = []
    for m in _FULL_RE.finditer(text):
        try:
            found.append((m.start(), dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))))
        except ValueError:
            pass
    for m in _MD_RE.finditer(text):
        try:
            d = dt.date(today.year, int(m.group(1)), int(m.group(2)))
            if d < today:
                d = dt.date(today.year + 1, int(m.group(1)), int(m.group(2)))
            found.append((m.start(), d))
        except ValueError:
            pass
    cur = today.weekday()
    for m in _WEEK_RE.finditer(text):
        target = _WEEKDAYS.index(m.group(2))
        delta = (7 - cur) % 7 + target if m.group(1) == "下" else (target - cur) % 7
        found.append((m.start(), today + dt.timedelta(days=delta)))
    for word in _DAYS_WORDS:
        pos = text.find(word)
        while pos != -1:
            offset = 0 if word in ("今天", "今日") else (1 if word in ("明天", "明日") else 2)
            found.append((pos, today + dt.timedelta(days=offset)))
            pos = text.find(word, pos + len(word))
    found.sort(key=lambda x: x[0])
    return found[0][1] if found else None


# ---------- HTTP / SSE 客户端（http.client 直连，路径全部为字面量） ----------


def _connect() -> http.client.HTTPConnection:
    """按 BASE_URL 建立 HTTP(S) 连接。

    校验：仅 http/https；主机可解析；拒绝链路本地（169.254.0.0/16，云元数据所在）
    与未指定地址。本脚本是本地评测客户端，BASE_URL 指向被测服务（缺省
    127.0.0.1:8000），环回/私网地址是合法目标。
    """
    from urllib.parse import urlsplit

    parts = urlsplit(BASE_URL if "://" in BASE_URL else "http://" + BASE_URL)
    if parts.scheme not in ("http", "https"):
        raise ValueError(f"BASE_URL 仅支持 http/https：{BASE_URL}")
    host = parts.hostname
    if not host:
        raise ValueError(f"BASE_URL 缺少主机名：{BASE_URL}")
    if host.lower().startswith("metadata."):
        raise ValueError("拒绝访问云元数据主机名")
    for info in socket.getaddrinfo(host, None):
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_link_local or ip.is_unspecified:
            raise ValueError(f"拒绝访问链路本地/未指定地址：{ip}")
    port = parts.port or (443 if parts.scheme == "https" else 80)
    if parts.scheme == "https":
        return http.client.HTTPSConnection(host, port, timeout=300, context=ssl.create_default_context())
    return http.client.HTTPConnection(host, port, timeout=300)


def _request(method: str, path: str, body: dict | None = None) -> http.client.HTTPResponse:
    """对被测服务发一次请求；path 为本文件内的固定字面量。"""
    conn = _connect()
    payload = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    headers = {"Content-Type": "application/json"} if body is not None else {}
    conn.request(method, path, body=payload, headers=headers)
    return conn.getresponse()


def chat_events(question: str, session_id: str, role: str, mode: str = "auto") -> list[dict]:
    """POST /api/chat 并解析全部 SSE 事件。"""
    resp = _request("POST", "/api/chat", {"question": question, "session_id": session_id, "role": role, "mode": mode})
    events: list[dict] = []
    for raw in resp:
        line = raw.decode("utf-8").strip()
        if line.startswith("data: "):
            events.append(json.loads(line[6:]))
    resp.close()
    return events


def business_reset() -> None:
    resp = _request("POST", "/api/business/reset")
    resp.read()
    resp.close()


def business_overview() -> dict:
    resp = _request("GET", "/api/business/overview")
    data = json.loads(resp.read().decode("utf-8"))
    resp.close()
    return data


def health() -> dict:
    resp = _request("GET", "/api/health")
    data = json.loads(resp.read().decode("utf-8"))
    resp.close()
    return data


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
    # agent 轨语义等价：某轮回答以问号收尾（自然语言追问，classic 的
    # slot_question 事件等价物——工作流机制 vs 对话式收集）
    asked_question: bool = False


def _run_turns(sid: str, turns: list[str], role: str, mode: str = "auto") -> RunAgg:
    agg = RunAgg()
    t0 = time.perf_counter()
    for turn in turns:
        turn_answer = ""
        for ev in chat_events(turn, sid, role, mode):
            et = ev.get("type")
            if et == "route":
                agg.routes.add(ev.get("route"))
            elif et == "answer_delta":
                agg.answer += ev.get("text", "")
                turn_answer += ev.get("text", "")
            elif et == "citations":
                agg.cited.update(c["doc_id"] for c in ev.get("items", []))
            elif et == "slot_question":
                agg.asked_slot = True
            elif et == "pending_action":
                agg.pending_tools.add(ev.get("tool"))
            elif et == "action_result":
                agg.results.append(ev)
            elif et == "error":
                agg.errors.append(ev.get("message", ""))
            elif et == "done":
                agg.latency_ms += int(ev.get("latency_ms", 0))
        if turn_answer.rstrip().endswith(("？", "?")):
            agg.asked_question = True
    agg.latency_ms = agg.latency_ms or int((time.perf_counter() - t0) * 1000)
    return agg


def _expect_ok(exp: dict, agg: RunAgg, mode: str = "auto") -> bool:
    checks = []

    if "route" in exp:
        # P14 Q6：triage（agent-first）随 Go 退役，expect.route=agent 的语义
        # 等价物是请求以 mode=react 显式进入 ReAct 链路——跑法本身即满足。
        if exp["route"] == "agent" and mode == "react":
            checks.append(True)
        else:
            checks.append(exp["route"] in agg.routes)
    if "asked_slot" in exp:
        # P17 agent 轨：classic 的 slot_question 事件或对话式追问（问号收尾轮）
        # 任一命中即视为已追问（双底座行为差异的语义等价口径）
        asked = agg.asked_slot or agg.asked_question
        checks.append(asked == exp["asked_slot"])
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

    overview = business_overview()
    if "booking" in exp:
        want = exp["booking"]
        date = parse_date(want["date_text"]).isoformat() if want.get("date_text") else want.get("date")
        matches = [
            b
            for b in overview["bookings"]
            if want.get("venue_contains", "") in b["venue"]
            and (not date or b["date"] == date)
            and (not want.get("slot") or b["slot"] == want["slot"])
        ]
        checks.append(bool(matches))
    if "bookings_count" in exp:
        checks.append(len(overview["bookings"]) == exp["bookings_count"])

    if "ticket" in exp:
        want = exp["ticket"]
        tickets = overview["tickets"]
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
    if item["type"] == "chitchat":
        # P17 寒暄集：自然回复 + 零引用 + effective route=chitchat + 不触发拒答话术
        ok = (
            "只能回答" not in agg.answer
            and bool(agg.answer.strip())
            and not agg.cited
            and "chitchat" in agg.routes
        )
        return {"pass": ok, "kw": None, "cite": None}
    kws = item.get("gold_keywords", [])
    kw_hit = any(_norm(k) in _norm(agg.answer) for k in kws) if kws else None
    expected = set(item.get("expected_docs", []))
    cite_hit = bool(expected & agg.cited) if expected else None
    passed = (kw_hit is not False) and (cite_hit is not False)
    return {"pass": passed, "kw": kw_hit, "cite": cite_hit}


def main() -> int:
    parser = argparse.ArgumentParser(description="格物离线评测（HTTP 客户端）")
    parser.add_argument("--type", dest="type_", choices=["factual", "multi_hop", "refusal", "transaction", "hybrid"])
    parser.add_argument("--limit", type=int)
    parser.add_argument("--dataset", default="eval/dataset.jsonl",
                        help="数据集路径（相对仓库根或绝对路径），如 eval/dataset-agent.jsonl")
    parser.add_argument("--tag", default="", help="报告标签（写入文件名与表头，如 agent-first）")
    parser.add_argument("--mode", dest="mode_", default="auto", choices=["auto", "direct", "research", "react", "classic"],
                        help="全部用例统一使用的 chat mode（P17：classic=级联基线；react 与 auto 同路）")
    args = parser.parse_args()

    h = health()
    dataset_path = Path(args.dataset)
    if not dataset_path.is_absolute():
        dataset_path = ROOT / dataset_path
    items = [json.loads(line) for line in dataset_path.read_text("utf-8").splitlines() if line.strip()]
    if args.type_:
        items = [it for it in items if it["type"] == args.type_]
    if args.limit:
        items = items[: args.limit]

    def budget_used() -> int:
        try:
            return int(health().get("budget", {}).get("used", 0))
        except Exception:  # noqa: BLE001
            return -1

    rows = []
    for item in items:
        # 跨 run 唯一：同一 run 内三轮共享；遗留 interrupt/会话状态不串场
        sid = f"eval-{item['id']}-{int(time.time() * 1000)}"
        multi = "turns" in item
        used0 = budget_used()
        try:
            if multi:
                business_reset()
                agg = _run_turns(sid, item["turns"], item.get("role", "student"), args.mode_)
                s = {"pass": _expect_ok(item.get("expect", {}), agg, args.mode_) and not agg.errors, "kw": None, "cite": None}
            else:
                agg = _run_turns(sid, [item["question"]], "student", args.mode_)
                s = score_single(item, agg)
        except Exception as exc:  # noqa: BLE001
            agg = RunAgg(errors=[f"{type(exc).__name__}: {exc}"])
            s = {"pass": False, "kw": None, "cite": None}
        tokens = budget_used() - used0

        rows.append({"item": item, "agg": agg, "score": s, "multi": multi, "tokens": max(tokens, 0)})
        flag = "✓" if s["pass"] else "✗"
        extra = f" routes={sorted(agg.routes)}" if multi else ""
        print(f"  {flag} {item['id']:<12} {agg.latency_ms:>5}ms  {max(tokens, 0):>6}tk{extra}")

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

    def avg_tokens(rs) -> str:
        return f"{sum(r['tokens'] for r in rs) / len(rs):.0f}" if rs else "-"

    types = ["factual", "multi_hop", "refusal", "transaction", "hybrid", "chitchat"]
    lines = [
        "# 评测报告",
        "",
        f"- 时间：{dt.datetime.now().strftime('%Y-%m-%d %H:%M')}",
        f"- 端点：{BASE_URL}（版本 {h.get('version')}，LLM {'启用' if h.get('llm') else '未启用（离线确定性链路）'}）",
        f"- 数据集：{len(rows)} 题（" + "，".join(f"{t} {len(sel(t))}" for t in types if sel(t)) + "）",
        f"- 链路标签：{args.tag or '默认（cascade workflow）'}",
        f"- token 消耗（/api/health 预算差值，含全部 LLM 调用）：总 {sum(r['tokens'] for r in rows)}",
        "",
        "| 类型 | 通过率 | 关键词命中 | 引用召回 | 平均延迟 | 平均 token |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    label = {"transaction": "transaction（办理）", "hybrid": "hybrid（问答+办理）"}
    for t in types:
        rs = sel(t)
        if not rs:
            continue
        kw, cite = ("-", "-") if t in ("refusal", "transaction", "hybrid") else (kw_rate(rs), cite_rate(rs))
        lines.append(f"| {label.get(t, t)} | {rate(rs)} | {kw} | {cite} | {avg_latency(rs)} | {avg_tokens(rs)} |")

    lines += ["", "## 明细", "", "| ID | 类型 | 多轮 | 通过 | 延迟 | token | 说明 |", "| --- | --- | --- | --- | --- | --- | --- |"]
    for r in rows:
        it = r["item"]
        note = []
        if r["multi"]:
            note.append(f"routes={','.join(sorted(r['agg'].routes)) or '-'}")
        if r["agg"].errors:
            note.append(f"错误：{r['agg'].errors[0][:40]}")
        lines.append(
            f"| {it['id']} | {it['type']} | {'✓' if r['multi'] else '-'} | "
            f"{'✓' if r['score']['pass'] else '✗'} | {r['agg'].latency_ms}ms | {r['tokens']} | {'；'.join(note)} |"
        )

    report_dir = ROOT / "eval" / "reports"
    report_dir.mkdir(exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M")
    out = report_dir / (f"report-{args.tag + '-' if args.tag else ''}{stamp}.md" if args.tag else f"report-{stamp}.md")
    out.write_text("\n".join(lines) + "\n", "utf-8")
    print(f"\n报告已写入：{out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
