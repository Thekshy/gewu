"""Deep Research 链路：拆解子问题 → 逐路检索 → 证据聚合去重 → 交叉综合作答。"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator

from .. import llm
from ..llm import has_key
from ..rag.retrieve import Retriever
from .prompts import ANSWER_SYSTEM, DEMO_MODE_NOTE, NO_DATA_ANSWER, PLANNER_SYSTEM

MAX_SUBQUESTIONS = 4
MAX_EVIDENCE = 12       # 证据条数上限，控制综合阶段的上下文长度
EVIDENCE_TEXT_LIMIT = 600

logger = logging.getLogger(__name__)


def _plan(question: str) -> list[str]:
    """LLM 拆解子问题；无 key 时退化为原问题单路检索。"""
    if not has_key():
        return [question]
    try:
        raw = llm.chat(
            [
                {"role": "system", "content": PLANNER_SYSTEM},
                {"role": "user", "content": question},
            ],
            json_mode=True,
            temperature=0.0,
            max_tokens=400,
        )
        subs = json.loads(raw).get("subquestions")
        if isinstance(subs, list) and subs:
            return [str(s) for s in subs if str(s).strip()][:MAX_SUBQUESTIONS]
    except Exception as exc:  # noqa: BLE001
        logger.warning("子问题拆解失败，退化为单路检索：%s", exc)
    return [question]


def run_research(question: str, retriever: Retriever, k: int = 5) -> Iterator[dict]:
    """产出事件流：status / step* → answer_delta* → citations。"""
    yield {"type": "status", "text": "正在拆解问题…"}
    subquestions = _plan(question)

    pool: dict[int, object] = {}   # chunk_id → Hit，跨子问题去重
    order: list[int] = []
    for i, sub in enumerate(subquestions, 1):
        hits = retriever.search(sub, k=k)
        titles = list(dict.fromkeys(h.title for h in hits[:3]))
        yield {"type": "step", "index": i, "subquestion": sub, "sources": titles}
        for h in hits:
            if h.chunk_id not in pool:
                pool[h.chunk_id] = h
                order.append(h.chunk_id)

    if not order:
        yield {"type": "answer_delta", "text": NO_DATA_ANSWER}
        yield {"type": "citations", "items": []}
        return

    blocks: list[str] = []
    citations: list[dict] = []
    for n, cid in enumerate(order[:MAX_EVIDENCE], 1):
        h = pool[cid]
        blocks.append(f"[{n}] 《{h.title}》（来源：{h.source}）\n{h.text[:EVIDENCE_TEXT_LIMIT]}")
        citations.append({"n": n, "doc_id": h.doc_id, "title": h.title, "source": h.source})

    yield {"type": "status", "text": f"共检索到 {len(order)} 条证据，正在交叉验证与综合…"}

    if not has_key():
        head = "\n\n".join(blocks[:3])
        yield {
            "type": "answer_delta",
            "text": (
                f"{DEMO_MODE_NOTE}\n\n"
                f"围绕 {len(subquestions)} 个子问题共检索到 {len(order)} 条相关段落，节选：\n\n{head}"
            ),
        }
        yield {"type": "citations", "items": citations}
        return

    messages = [
        {"role": "system", "content": ANSWER_SYSTEM},
        {
            "role": "user",
            "content": (
                f"参考资料：\n\n{chr(10).join(blocks)}\n\n"
                f"问题：{question}"
            ),
        },
    ]
    for delta in llm.chat_stream(messages):
        yield {"type": "answer_delta", "text": delta}
    yield {"type": "citations", "items": citations}
