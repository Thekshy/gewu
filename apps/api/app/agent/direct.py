"""RAG 直答：单轮混合检索 → 带引用流式生成。"""

from __future__ import annotations

from collections.abc import Iterator

from .. import llm
from ..llm import has_key
from ..rag.retrieve import Retriever
from .prompts import ANSWER_SYSTEM, DEMO_MODE_NOTE, NO_DATA_ANSWER


def _numbered_context(hits) -> tuple[str, list[dict]]:
    lines: list[str] = []
    citations: list[dict] = []
    for i, h in enumerate(hits, 1):
        lines.append(f"[{i}] 《{h.title}》（来源：{h.source}）\n{h.text}")
        citations.append(
            {"n": i, "doc_id": h.doc_id, "title": h.title, "source": h.source}
        )
    return "\n\n".join(lines), citations


def answer_direct(question: str, retriever: Retriever, k: int = 6) -> Iterator[dict]:
    """产出事件流：answer_delta* → citations。"""
    hits = retriever.search(question, k=k)
    if not hits:
        yield {"type": "answer_delta", "text": NO_DATA_ANSWER}
        yield {"type": "citations", "items": []}
        return

    context, citations = _numbered_context(hits)

    if not has_key():
        excerpts = "\n\n".join(f"[{i}] 《{h.title}》：{h.text[:180]}…" for i, h in enumerate(hits[:3], 1))
        yield {"type": "answer_delta", "text": f"{DEMO_MODE_NOTE}\n\n{excerpts}"}
        yield {"type": "citations", "items": citations}
        return

    messages = [
        {"role": "system", "content": ANSWER_SYSTEM},
        {"role": "user", "content": f"参考资料：\n\n{context}\n\n问题：{question}"},
    ]
    for delta in llm.chat_stream(messages):
        yield {"type": "answer_delta", "text": delta}
    yield {"type": "citations", "items": citations}
