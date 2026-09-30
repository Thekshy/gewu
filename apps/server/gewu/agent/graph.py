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
from gewu.agent.routing import CascadeRouter, fill_policy, react_plan_signal
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
    """route → 各链路条件边（mode=react 显式 / REACT_MODE=on 信号转 ReAct，对齐 Go pipeline）。"""
    if state["mode"] == "react":
        return "react"
    route = state["route"]["route"]
    if state["mode"] == "auto" and route == "transaction" and react_plan_signal(state["resolved"]):
        return "react"
    return route


def make_react_node(llm: LLMService, retriever: Retriever, business, tools: dict):
    """主图 react 节点：跑 ReAct 子图；写操作转确认时把 tx 状态写回主 state。"""
    from gewu.agent.react import ReactContext, build_react_subgraph

    sub = build_react_subgraph(llm)

    def react(state: ChatState) -> dict:
        ctx = ReactContext(
            retriever,
            business,
            tools,
            state["role"],
            state["user"],
            state["route"].get("toolset", []),
        )
        result = sub.invoke(
            {
                "question": state["resolved"],
                "role": state["role"],
                "user": state["user"],
                "session_id": state["session_id"],
                "toolset": state["route"].get("toolset", []),
                "mem_block": state.get("mem_block", ""),
                "turn": 0,
            },
            config={"configurable": {"react_ctx": ctx}},  # 运行时对象不进 checkpoint
        )
        if result.get("stop") == "confirm":
            return {
                "tx_tool": result["tx_tool"],
                "tx_slots": result["tx_slots"],
                "tx_phase": "confirm",
            }
        final = result.get("final", "")
        emit(ev.answer_evt(final))
        emit(ev.citations_evt(result.get("citations", [])))
        return {"answer": final, "citations": result.get("citations", [])}

    return react


def make_tx_confirm_node(business):
    """确认摘要节点：pending_action + 确认文案（PARITY §9.3.3）。"""
    from gewu.agent.tx import build_confirm

    def tx_confirm(state: ChatState) -> dict:
        pa, text, _note = build_confirm(state, business)
        emit(pa)
        emit(ev.answer_evt(text))
        return {"answer": text}

    return tx_confirm


def make_tx_resume_node(llm: LLMService, business, tools: dict):
    """办理续轮节点：confirm 阶段回复的处理（修改/确认/取消/new_topic）。

    collect 阶段（workflow 槽位收集）随 P14-6 落位；本节点 P14-4 先接 ReAct
    转来的 confirm 确认流。
    """
    from gewu.agent.tx import (
        _CONFIRM_MODIFY_RE,
        FLOW_DEFS,
        SLOT_ORDER,
        build_confirm,
        classify_reply,
        execute_tool,
        slot_meta,
    )

    def tx_resume(state: ChatState) -> dict:
        meta = slot_meta(business)
        intent = classify_reply(llm, meta, state["question"], state)

        if intent == "cancel":
            emit(ev.answer_evt("好的，已取消本次办理。有别的事随时找我。"))
            return {
                "tx_phase": "",
                "tx_tool": "",
                "tx_slots": {},
                "tx_last_asked": "",
                "answer": "好的，已取消本次办理。有别的事随时找我。",
            }

        if intent == "new_topic":
            # 切换新话题：放弃流程，清状态后转正常路由（route 分支重走）
            return {
                "tx_phase": "",
                "tx_tool": "",
                "tx_slots": {},
                "tx_last_asked": "",
                "tx_new_topic": True,
            }

        # continue：confirm 阶段先尝试理解为「修改」，再判确认
        tool = state["tx_tool"]
        flow = FLOW_DEFS[tool]
        slots = dict(state.get("tx_slots") or {})
        modified = False
        for slot in SLOT_ORDER:
            if slot in ("purpose", "reason"):
                continue
            if slot not in flow["required"] and slot not in slots:
                continue
            value = meta[slot]["parse"](state["question"])
            if value and value != slots.get(slot):
                slots[slot] = value
                modified = True
        if modified:
            emit(ev.status_evt("已更新，请重新确认："))
            state = {**state, "tx_slots": slots}
            pa, text, _note = build_confirm(state, business)
            emit(pa)
            emit(ev.answer_evt(text))
            return {"tx_slots": slots, "tx_phase": "confirm", "tx_last_asked": "", "answer": text}

        if _CONFIRM_MODIFY_RE.search(state["question"]):
            result = execute_tool(tools, business, state, state["role"], state["user"])
            if result.ok:
                emit(ev.action_result_evt(tool, True, result.message, result.receipt or None))
                receipt = f"（凭证号：{result.receipt}）" if result.receipt else ""
                text = f"办理成功：{result.message}{receipt}"
                emit(ev.answer_evt(text))
                return {
                    "tx_phase": "",
                    "tx_tool": "",
                    "tx_slots": {},
                    "tx_last_asked": "",
                    "answer": text,
                }
            # 失败恢复：字段级问题重新追问该字段，其余失败结束流程并说明
            if result.field:
                m = meta.get(result.field)
                if m:
                    slots.pop(result.field, None)
                    question = m["ask"]
                    if result.alternatives:
                        question = (
                            question + "可选时段：" + "、".join(result.alternatives)
                        ).strip()
                    emit(ev.action_result_evt(tool, False, result.message, None))
                    emit(ev.slot_question_evt(result.field, question))
                    msg = result.message or "执行失败"
                    text = f"{msg}。{question}"
                    emit(ev.answer_evt(text))
                    return {
                        "tx_slots": slots,
                        "tx_phase": "collect",
                        "tx_last_asked": result.field,
                        "answer": text,
                    }
            emit(ev.action_result_evt(tool, False, result.message, None))
            text = f"办理未完成：{result.message or '未知错误'}。如需继续请重新发起。"
            emit(ev.answer_evt(text))
            return {
                "tx_phase": "",
                "tx_tool": "",
                "tx_slots": {},
                "tx_last_asked": "",
                "answer": text,
            }

        text = "没太听懂——请回复「确认」提交，或「取消」放弃，也可以直接告诉我需要修改的日期、时段等信息。"
        emit(ev.answer_evt(text))
        return {"answer": text}

    return tx_resume


def entry_gate(state: ChatState) -> str:
    """入口分派（Go「会话优先解释续轮」语义）：办理流程进行中走 tx_resume。"""
    if state.get("tx_phase") in ("collect", "confirm"):
        return "tx_resume"
    return "resolve_query"


def tx_resume_branch(state: ChatState) -> str:
    """tx_resume 后：切话题 → 重走路由；否则结束本轮。"""
    return "route" if state.get("tx_new_topic") else "__end__"


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


def build_graph(
    settings: Settings,
    retriever: Retriever,
    llm: LLMService,
    business=None,
    tools: dict | None = None,
    checkpointer=None,
):
    """装配会话编排主图。checkpointer 缺省内存版（P14-6 换 PostgresSaver）。

    tools 为业务工具表（权限矩阵）；react/tx 链路依赖，P14-4 起。
    """
    from gewu.agent.tools import tools_for

    tools = tools if tools is not None else tools_for()
    g: StateGraph = StateGraph(ChatState)
    g.add_node("resolve_query", make_resolve_node(llm, settings))
    g.add_node("route", make_route_node(llm, settings))
    g.add_node("retrieve", make_retrieve_node(retriever))
    g.add_node("answer_direct", make_answer_node(llm))
    g.add_node("refusal", make_refusal_node())
    g.add_node("react", make_react_node(llm, retriever, business, tools))
    g.add_node("tx_confirm", make_tx_confirm_node(business))
    g.add_node("tx_resume", make_tx_resume_node(llm, business, tools))
    for name in ("research", "hybrid", "transaction"):
        g.add_node(name, _pending_link(name))

    g.add_conditional_edges(
        START,
        entry_gate,
        {
            "resolve_query": "resolve_query",
            "tx_resume": "tx_resume",
        },
    )
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
    g.add_conditional_edges(
        "react",
        _after_react,
        {
            "tx_confirm": "tx_confirm",
            "__end__": END,
        },
    )
    g.add_edge("tx_confirm", END)
    g.add_conditional_edges(
        "tx_resume",
        tx_resume_branch,
        {
            "route": "route",
            "__end__": END,
        },
    )
    for name in ("research", "hybrid", "transaction"):
        g.add_edge(name, END)
    return g.compile(checkpointer=checkpointer or MemorySaver())


def _after_react(state: ChatState) -> str:
    """react 后：写操作已转确认 → tx_confirm 发确认摘要；否则结束。"""
    return "tx_confirm" if state.get("tx_phase") == "confirm" else "__end__"


def _pending_link(name: str):
    """P14-5/6 待接入链路的占位节点（factual/refusal/react 链路已可用）。"""

    def node(state: ChatState) -> dict:
        emit(ev.error_evt(f"链路 {name} 尚未接入（P14-5/6 ticket）"))
        return {}

    node.__name__ = f"node_{name}"
    return node
