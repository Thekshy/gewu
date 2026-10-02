"""PARITY §3 SSE 事件构造器（移植自 Go internal/agent/events.go）。

事件为 dict（Python 3.7+ 保持插入序，对齐 Go struct 字段序）；字段集与 JSON
形状是前端与评测的契约，逐字段对照实现。items/args 空时必须是 []/{} 而非 null。
"""

from __future__ import annotations

from typing import Any


def route_evt(route: str, reason: str, by_llm: bool) -> dict:
    return {"type": "route", "route": route, "reason": reason, "by_llm": by_llm}


def route_decision_evt(dec: dict) -> dict:
    """决策包 → route 事件（级联模式带 layer/confidence）。"""
    ev: dict[str, Any] = {
        "type": "route",
        "route": dec["route"],
        "reason": dec["reason"],
        "by_llm": dec["by_llm"],
    }
    layer = dec.get("layer", "")
    if layer:
        ev["layer"] = layer
        ev["confidence"] = dec.get("confidence", 0.0)
    return ev


def status_evt(text: str) -> dict:
    return {"type": "status", "text": text}


def step_evt(index: int, subquestion: str, sources: list[str]) -> dict:
    return {"type": "step", "index": index, "subquestion": subquestion, "sources": sources or []}


def answer_evt(text: str) -> dict:
    return {"type": "answer_delta", "text": text}


def answer_reset_evt() -> dict:
    """P30：流式撤回——本轮已发 delta 聚合出 tool_calls（中间轮），前端清空已显示
    文本并转存为一条 step。事件形状只做加法，既有契约不动。"""
    return {"type": "answer_reset"}


def citations_evt(items: list[dict] | None) -> dict:
    """items 必须是数组（空也要 []，不能是 null）。"""
    return {"type": "citations", "items": items or []}


def citation(n: int, doc_id: str, title: str, source: str) -> dict:
    return {"n": n, "doc_id": doc_id, "title": title, "source": source}


def slot_question_evt(slot: str, question: str) -> dict:
    return {"type": "slot_question", "slot": slot, "question": question}


def pending_action_evt(tool: str, label: str, args: dict) -> dict:
    return {"type": "pending_action", "tool": tool, "label": label, "args": args or {}}


def action_result_evt(tool: str, success: bool, message: str, receipt: str | None = None) -> dict:
    return {
        "type": "action_result",
        "tool": tool,
        "success": success,
        "message": message,
        "receipt": receipt,
    }


def error_evt(message: str) -> dict:
    return {"type": "error", "message": message}


def done_evt(latency_ms: int, reason: str = "") -> dict:
    """done 事件唯一构造器（一次 chat 恰一个 done，单点发射）。

    reason: completed | max_tokens | error | aborted（P10，缺省省略向后兼容）。
    """
    ev: dict[str, Any] = {"type": "done", "latency_ms": latency_ms}
    if reason:
        ev["reason"] = reason
    return ev


def follow_ups_evt(items: list[str]) -> dict:
    """P25：done 之后追发的建议追问（america.gov 同款模式；生成失败/超时不发）。"""
    return {"type": "follow_ups", "items": items or []}
