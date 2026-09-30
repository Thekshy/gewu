"""会话编排主图（移植自 Go internal/agent/pipeline.go 的分发结构）。

图结构（P14 分 ticket 扩展；条件边结构按最终形态一次搭好）：
  START → resolve_query → route → {refusal, factual, research, hybrid, transaction, react}
  factual → retrieve → answer_direct → END
  其余分支随 P14-4~6 落位。

done 事件在 chat 端点单点发射（Go RunChat 单点语义的等价物）。
"""

from __future__ import annotations

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

from gewu.agent import events as ev
from gewu.agent.emitter import emit
from gewu.agent.prompts import ANSWER_SYSTEM, NO_DATA_ANSWER, REFUSAL_ANSWER
from gewu.agent.routing import CascadeRouter, fill_policy
from gewu.agent.state import ChatState
from gewu.config import Settings
from gewu.llm.service import LLMService
from gewu.rag.retrieve import Retriever

# ---------- 节点 ----------


def make_resolve_node(_llm: LLMService, _settings: Settings):
    """上下文补全（多轮指代消解）。P14-2 占位：P14-6 会话/记忆接通后填 ResolveQuery。"""

    def resolve_query(state: ChatState) -> dict:
        return {"resolved": state["question"]}

    return resolve_query


def make_route_node(llm: LLMService, settings: Settings):
    """路由决策包产出：用户指定 direct/research 直接构造；其余走 cascade 级联。"""
    router = CascadeRouter(llm)

    def route(state: ChatState) -> dict:
        mode = state["mode"]
        if mode in ("direct", "research"):
            route_name = "factual" if mode == "direct" else "research"
            dec = fill_policy(
                {
                    "route": route_name,
                    "confidence": 1.0,
                    "layer": "user-specified",
                    "reason": f"用户指定 {mode}",
                    "by_llm": False,
                    "pre_rag": False,
                    "toolset": [],
                    "model_tier": "small",
                }
            )
        else:
            dec = router.route(state["resolved"])
        emit(ev.route_decision_evt(dec))
        return {"route": dec}

    return route


def route_branch(state: ChatState) -> str:
    """route → 各链路条件边。"""
    return state["route"]["route"]


def _pending_link(name: str):
    """P14-4/5/6 待接入链路的占位节点（真跑 refusal/factual 不受影响）。"""

    def node(state: ChatState) -> dict:
        emit(ev.error_evt(f"链路 {name} 尚未接入（P14-4/5/6 ticket）"))
        return {}

    node.__name__ = f"node_{name}"
    return node


def make_retrieve_node(retriever: Retriever):
    def retrieve(state: ChatState) -> dict:
        hits = retriever.search(state["resolved"], retriever.k)
        return {"hits": [_hit_dict(h) for h in hits]}

    return retrieve


def make_answer_node(llm: LLMService):
    """RAG 直答（Go AnswerDirect）：answer_delta* → [截断 status] → citations。"""

    def answer_direct(state: ChatState) -> dict:
        hits = state.get("hits") or []
        if not hits:
            emit(ev.answer_evt(NO_DATA_ANSWER))
            emit(ev.citations_evt([]))
            return {"answer": NO_DATA_ANSWER, "citations": []}

        context, citations = numbered_context(hits)
        messages = assemble_messages(state, context)
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

    return answer_direct


def make_refusal_node():
    def refusal(state: ChatState) -> dict:
        emit(ev.answer_evt(REFUSAL_ANSWER))
        emit(ev.citations_evt([]))
        return {"answer": REFUSAL_ANSWER, "citations": []}

    return refusal


# ---------- 装配 ----------


def assemble_messages(state: ChatState, context: str) -> list[tuple[str, str]]:
    """消息分层装配（P6 阶段4，顺序固定）：system → [记忆] → user（P14-6 接记忆）。"""
    return [
        ("system", ANSWER_SYSTEM),
        ("user", f"参考资料：\n\n{context}\n\n问题：{state['resolved']}"),
    ]


def numbered_context(hits: list[dict]) -> tuple[str, list[dict]]:
    """命中列表 → 编号上下文与引用列表（Go direct.go 逐字对照）。"""
    lines = []
    citations = []
    for i, h in enumerate(hits):
        lines.append(f"[{i + 1}] 《{h['title']}》（来源：{h['source']}）\n{h['text']}")
        citations.append(ev.citation(i + 1, h["doc_id"], h["title"], h["source"]))
    return "\n\n".join(lines), citations


def _hit_dict(h) -> dict:
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


def build_graph(settings: Settings, retriever: Retriever, llm: LLMService, checkpointer=None):
    """装配会话编排主图。checkpointer 缺省内存版（P14-6 换 PostgresSaver）。"""
    g: StateGraph = StateGraph(ChatState)
    g.add_node("resolve_query", make_resolve_node(llm, settings))
    g.add_node("route", make_route_node(llm, settings))
    g.add_node("retrieve", make_retrieve_node(retriever))
    g.add_node("answer_direct", make_answer_node(llm))
    g.add_node("refusal", make_refusal_node())
    for name in ("research", "hybrid", "transaction", "react"):
        g.add_node(name, _pending_link(name))

    g.add_edge(START, "resolve_query")
    g.add_edge("resolve_query", "route")
    g.add_conditional_edges(
        "route",
        route_branch,
        {
            "factual": "retrieve",
            "refusal": "refusal",
            "research": "research",
            "hybrid": "hybrid",
            "transaction": "transaction",
            "react": "react",
        },
    )
    g.add_edge("retrieve", "answer_direct")
    g.add_edge("answer_direct", END)
    g.add_edge("refusal", END)
    for name in ("research", "hybrid", "transaction", "react"):
        g.add_edge(name, END)
    return g.compile(checkpointer=checkpointer or MemorySaver())
