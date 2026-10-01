"""深度研究链路（移植自 Go internal/agent/research.go）。

拆解子问题 → 逐路检索 → 证据聚合去重 → 交叉综合作答（PARITY §7）。
"""

from __future__ import annotations

from gewu.agent import events as ev
from gewu.agent.emitter import emit
from gewu.agent.prompts import NO_DATA_ANSWER, PLANNER_SYSTEM
from gewu.jsonx import json_str_slice, parse_json_object
from gewu.llm.service import LLMService
from gewu.rag.retrieve import Retriever

MAX_SUBQUESTIONS = 4  # 子问题上限
MAX_EVIDENCE = 12  # 证据条数上限，控制综合阶段的上下文长度
EVIDENCE_TEXT_LIMIT = 600


def plan(llm: LLMService, question: str) -> list[str]:
    """LLM 拆解子问题；无 key/失败时退化为原问题单路检索。"""
    if not llm.has_key():
        return [question]
    try:
        raw = llm.chat(
            [("system", PLANNER_SYSTEM), ("user", question)],
            json_mode=True,
            small=True,
            max_tokens=400,
        )
        subs = json_str_slice(parse_json_object(raw), "subquestions")
        if subs:
            return subs[:MAX_SUBQUESTIONS]
    except Exception as e:  # noqa: BLE001
        print(f"[research] 子问题拆解失败，退化为单路检索：{e}")
    return [question]


def _truncate(s: str, n: int) -> str:
    return s if len(s) <= n else s[:n]


def run_research(state: dict, llm: LLMService, retriever: Retriever) -> dict:
    """深研节点核心（事件流：status / step* → answer_delta* → [截断 status] → citations）。

    state 为 ChatState 形态 dict；返回 state 更新（answer/citations/truncated）。
    """
    from gewu.agent.graph import assemble_messages  # noqa: PLC0415 - 延迟导入避免环

    emit(ev.status_evt("正在拆解问题…"))
    subquestions = plan(llm, state["resolved"])

    pool: dict[int, dict] = {}  # chunk_id → Hit，跨子问题去重
    order: list[int] = []  # 到达顺序
    for i, sub in enumerate(subquestions):
        hits = [_hit_to_dict(h) for h in retriever.search(sub)]
        titles: list[str] = []
        seen: set[str] = set()
        for h in hits[:3]:
            if h["title"] not in seen:
                seen.add(h["title"])
                titles.append(h["title"])
        emit(ev.step_evt(i + 1, sub, titles))
        for h in hits:
            cid = h["chunk_id"]
            if cid not in pool:
                pool[cid] = h
                order.append(cid)

    if not order:
        emit(ev.answer_evt(NO_DATA_ANSWER))
        emit(ev.citations_evt([]))
        return {"answer": NO_DATA_ANSWER, "citations": []}

    blocks = []
    citations = []
    for n, cid in enumerate(order[:MAX_EVIDENCE]):
        h = pool[cid]
        evidence = _truncate(h["text"], EVIDENCE_TEXT_LIMIT)
        blocks.append(f"[{n + 1}] 《{h['title']}》（来源：{h['source']}）\n{evidence}")
        citations.append(ev.citation(n + 1, h["doc_id"], h["title"], h["source"]))

    emit(ev.status_evt(f"共检索到 {len(order)} 条证据，正在交叉验证与综合…"))

    context = "\n".join(blocks)
    messages = [
        *assemble_messages(state, context)[:-1],
        ("user", f"参考资料：\n\n{context}\n\n问题：{state['resolved']}"),
    ]
    parts: list[str] = []
    stream = llm.chat_stream(messages)
    for delta in stream:
        parts.append(delta)
        emit(ev.answer_evt(delta))
    answer = "".join(parts)
    if stream.finish_reason == "length":
        emit(ev.status_evt("回答已达长度上限，可能被截断"))
        emit(ev.citations_evt(citations))
        return {"answer": answer, "citations": citations, "truncated": True}
    emit(ev.citations_evt(citations))
    return {"answer": answer, "citations": citations}


def _hit_to_dict(h) -> dict:
    if isinstance(h, dict):
        return h
    return {
        "chunk_id": h.chunk_id,
        "doc_id": h.doc_id,
        "seq": h.seq,
        "text": h.text,
        "title": h.title,
        "source": h.source,
        "section_path": h.section_path,
    }
