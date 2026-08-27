"""会话编排：路由 → 直答 / 深度研究 / 拒答 / 业务办理 / 混合 → 统一事件流。

SSE 接口与评测复用同一入口。业务办理（transaction/hybrid）带跨轮状态：
槽位收集与确认阶段，用户下一条消息优先按流程回复解释，切话题则自动放弃流程。
"""

from __future__ import annotations

import time
from collections.abc import Iterator

from ..budget import BudgetExceeded
from ..config import get_settings
from . import direct, research, transaction
from .prompts import REFUSAL_ANSWER
from .router import route_question
from .session import sessions

_retriever_inst: object | None = None


def get_retriever():
    global _retriever_inst
    if _retriever_inst is None:
        from ..rag.retrieve import Retriever
        from ..rag.store import Store

        s = get_settings()
        _retriever_inst = Retriever(Store(s.index_path), k=s.retrieval_k)  # type: ignore[arg-type]
    return _retriever_inst


def _done(t0: float) -> dict:
    return {"type": "done", "latency_ms": int((time.perf_counter() - t0) * 1000)}


def run_chat(
    question: str,
    mode: str = "auto",
    session_id: str = "default",
    role: str = "student",
    user: str | None = None,
) -> Iterator[dict]:
    t0 = time.perf_counter()
    user = user or f"demo-{role}"
    try:
        # 1) 办理流程进行中：优先把消息解释为对流程的回应
        sess = sessions.get(session_id)
        if sess and sess.phase in ("collect", "confirm"):
            intent = transaction.classify_reply(question, sess)
            if intent == "continue":
                yield {
                    "type": "route",
                    "route": "transaction",
                    "reason": f"继续办理：{transaction.FLOW_DEFS.get(sess.tool, {}).get('label', sess.tool)}",
                    "by_llm": False,
                }
                yield from transaction.handle_reply(sess, question)
                yield _done(t0)
                return
            if intent == "cancel":
                sessions.clear(session_id)
                yield {"type": "route", "route": "transaction", "reason": "用户取消办理", "by_llm": False}
                yield {"type": "answer_delta", "text": "好的，已取消本次办理。有别的事随时找我。"}
                yield _done(t0)
                return
            sessions.clear(session_id)  # 切换新话题：放弃流程，走正常路由

        # 2) 路由
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
        elif route == "research":
            yield from research.run_research(question, get_retriever())
        elif route == "hybrid":
            yield {"type": "status", "text": "先回答你的政策问题…"}
            yield from direct.answer_direct(question, get_retriever())
            yield {"type": "status", "text": "接下来为你办理业务…"}
            yield from transaction.start_flow(question, role, user, session_id)
        elif route == "transaction":
            yield from transaction.start_flow(question, role, user, session_id)
        else:
            yield {"type": "error", "message": f"未知路由：{route}"}

        yield _done(t0)
    except BudgetExceeded as exc:
        yield {"type": "error", "message": str(exc)}
        yield _done(t0)
    except Exception as exc:  # noqa: BLE001
        yield {"type": "error", "message": f"{type(exc).__name__}: {exc}"}
        yield _done(t0)
