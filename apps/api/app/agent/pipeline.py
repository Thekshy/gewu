"""会话编排：路由 → 直答 / 深度研究 / 拒答 → 统一事件流（SSE 与评测复用同一入口）。"""

from __future__ import annotations

import time
from collections.abc import Iterator

from ..budget import BudgetExceeded
from ..config import get_settings
from . import direct, research
from .prompts import REFUSAL_ANSWER
from .router import route_question

_retriever_inst: object | None = None


def get_retriever():
    global _retriever_inst
    if _retriever_inst is None:
        from ..rag.retrieve import Retriever
        from ..rag.store import Store

        s = get_settings()
        _retriever_inst = Retriever(Store(s.index_path), k=s.retrieval_k)  # type: ignore[arg-type]
    return _retriever_inst


def run_chat(question: str, mode: str = "auto") -> Iterator[dict]:
    t0 = time.perf_counter()
    try:
        if mode in ("direct", "research"):
            route, reason, by_llm = mode, f"用户指定 {mode}", False
        else:
            r = route_question(question)
            route, reason, by_llm = r.route, r.reason, r.by_llm

        yield {"type": "route", "route": route, "reason": reason, "by_llm": by_llm}

        if route == "refusal":
            yield {"type": "answer_delta", "text": REFUSAL_ANSWER}
            yield {"type": "citations", "items": []}
        elif route == "factual":
            yield from direct.answer_direct(question, get_retriever())
        else:
            yield from research.run_research(question, get_retriever())

        yield {"type": "done", "latency_ms": int((time.perf_counter() - t0) * 1000)}
    except BudgetExceeded as exc:
        yield {"type": "error", "message": str(exc)}
        yield {"type": "done", "latency_ms": int((time.perf_counter() - t0) * 1000)}
    except Exception as exc:  # noqa: BLE001
        yield {"type": "error", "message": f"{type(exc).__name__}: {exc}"}
        yield {"type": "done", "latency_ms": int((time.perf_counter() - t0) * 1000)}
