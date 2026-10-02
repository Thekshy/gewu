"""会话编排主图（P17 agent-first：外壳手写薄图 + create_agent 子图双底座）。

图结构：
  START → entry_gate → resolve_query → mode_dispatch
      ├─ auto/react → agent_in → agent（create_agent 子图）→ agent_done → END
      ├─ classic → route（cascade 级联）→ {refusal, factual, research, hybrid, transaction}
      ├─ direct → retrieve → answer_direct → END
      └─ research → research → END
  classic 链路的 transaction/tx_confirm/tx_gate(interrupt)/tx_resume 原样保留
  （论文对照基线）；agent 链路的写确认门在子图 HITL 中间件内，interrupt 冒泡。

done 事件在 chat 端点单点发射（Go RunChat 单点语义的等价物）。
"""

from __future__ import annotations

import re

from langchain_core.messages import HumanMessage
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command

from gewu.agent import events as ev
from gewu.agent.emitter import emit
from gewu.agent.mw import last_ai_message, partial_answer
from gewu.agent.prompts import ANSWER_SYSTEM, NO_DATA_ANSWER, QUERY_REWRITE_SYSTEM, REFUSAL_ANSWER
from gewu.agent.routing import CascadeRouter, fill_policy
from gewu.agent.state import ChatState
from gewu.config import Settings
from gewu.jsonx import json_str, parse_json_object
from gewu.llm.service import LLMService
from gewu.rag.retrieve import Retriever

# ---------- 节点 ----------


_ANAPHORA_RE = re.compile(
    r"那|这|它|他|她|也|呢|上述|刚才|前面|上面|之前|第二|这种情况|我的情况|另外"
)


def make_resolve_node(llm: LLMService, settings: Settings, memory=None):
    """上下文补全（多轮指代消解，Go ResolveQuery 全门控移植）。

    触发门控（全部满足才调 LLM，缺一即原样返回，单轮会话零成本）：
    QUERY_REWRITE 开启 / 有 key / 本会话有 episodic 历史 / 问题命中指代信号词。
    补全是增强不是依赖：解析失败/输出异常一律静默回退原问题。
    """
    from gewu.memory import MAX_FACTS_IN_CONTEXT

    def resolve_query(state: ChatState) -> dict:
        question = state["question"]
        updates: dict = {}
        if memory is not None:
            from gewu.memory import memory_block  # noqa: PLC0415

            updates["mem_block"] = memory_block(memory, state["user"], state["session_id"])
        if settings.query_rewrite == "off" or memory is None or not llm.has_key():
            return updates
        if not _ANAPHORA_RE.search(question):
            return {}
        try:
            episodes = memory.recent_episodes(state["session_id"], 6)
        except Exception:  # noqa: BLE001
            return {}
        if not episodes:
            return {}

        sb = ["已知用户信息："]
        try:
            facts = memory.recent_facts(state["user"], MAX_FACTS_IN_CONTEXT)
        except Exception:  # noqa: BLE001
            facts = []
        if facts:
            sb.extend(f"- {f.kind}/{f.key}：{f.value}" for f in facts)
        else:
            sb.append("（暂无）")
        sb.append("最近对话：")
        sb.extend(episodes)
        sb.append("")
        sb.append("本轮问题：" + question)

        try:
            raw = llm.chat(
                [("system", QUERY_REWRITE_SYSTEM), ("user", "\n".join(sb))],
                json_mode=True,
                small=True,
                max_tokens=200,
            )
            rewritten = json_str(parse_json_object(raw), "rewritten").strip()
        except Exception as e:  # noqa: BLE001
            print(f"[agent] 上下文补全失败，使用原问题：{e}")
            return {}
        if not rewritten or rewritten == question:
            return updates
        n = len(rewritten)
        if n < 2 or n > 500:
            print(f"[agent] 上下文补全输出长度异常（{n} rune），回退原问题")
            return updates
        updates["resolved"] = rewritten
        return updates

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
    """classic/direct/research：route 决策 → 链路映射（direct/research 在 route
    节点内构造 user-specified 决策包并发 route 事件——契约保持）。"""
    return state["route"]["route"]


def mode_dispatch(state: ChatState) -> str:
    """resolve 后的分派（P17 控制权收口）。

    classic/direct/research 走 route 节点（级联/用户指定，route 事件契约不变）；
    auto/react 同路进 agent 子图（guard 的两段式 route 事件接管徽章）。
    """
    return "agent_in" if state["mode"] in ("auto", "react") else "route"


def make_agent_in_node():
    """agent 子图入口：本轮问题（指代消解后）入对话历史 + citations 清零。

    citations 通道按轮计作用域：外层字段跨轮持久化（checkpointer），
    不清零则上一轮来源漏进本轮事件（P26 真跑发现的跨轮污染）。
    classic 链路 answer_direct 每轮全量覆写，无此问题。
    """

    def agent_in(state: ChatState) -> dict:
        return {
            "messages": [HumanMessage(content=state.get("resolved") or state["question"])],
            "citations": [],
        }

    return agent_in


def make_agent_done_node():
    """agent 子图出口：answer 统一发射 + 截断标记 + 轮次耗尽兜底。

    确认门中断轮不到这里（turn 悬停在子图内，摘要已由 PendingAction 发出）；
    guard 短路轮也经过这里（消息由 guard 注入，answer 单点发射不重复）。
    """

    def agent_done(state: ChatState) -> dict:
        msgs = state.get("messages") or []
        ai = last_ai_message(msgs)
        answer = ""
        if ai is not None:
            c = ai.content
            answer = (
                c
                if isinstance(c, str)
                else "".join(
                    seg.get("text", "") if isinstance(seg, dict) else str(seg) for seg in c
                )
            )
            if "Model call limits exceeded" in answer:
                answer = ""  # 轮次耗尽：换部分结论兜底
        if not answer.strip():
            answer = partial_answer(msgs) or "未能获取足够信息回答该问题，请换个说法或补充细节。"
        truncated = False
        if ai is not None:
            meta = getattr(ai, "response_metadata", None) or {}
            if str(meta.get("finish_reason", "") or "") == "length":
                truncated = True
                emit(ev.status_evt("回答已达长度上限，可能被截断"))
        citations = state.get("citations") or []
        emit(ev.answer_evt(answer))
        emit(ev.citations_evt(citations))
        return {"answer": answer, "citations": citations, "truncated": truncated}

    return agent_done


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
    """办理续轮节点：collect 阶段吸收信息 / confirm 阶段回复处理（修改/确认/取消/new_topic）。"""
    from gewu.agent.tx import (
        _CONFIRM_MODIFY_RE,
        FLOW_DEFS,
        SLOT_ORDER,
        classify_reply,
        execute_tool,
        slot_meta,
    )

    advance = make_advance(llm, business)

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

        # continue：collect 阶段吸收信息；confirm 阶段先尝试「修改」，再判确认
        if state.get("tx_phase") == "collect":
            return advance(state)
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
            return Command(
                goto="tx_confirm",
                update={"tx_slots": slots, "tx_phase": "confirm", "tx_last_asked": ""},
            )

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


def make_advance(llm: LLMService, business):
    """collect 阶段推进（Go advance）：吸收新信息 → 齐了进确认，缺则追问。

    transaction 首轮与 tx_resume 续轮共用。
    """
    from gewu.agent.tx import (
        FLOW_DEFS,
        SLOT_ORDER,
        apply_days_phrase,
        llm_extract_slots,
        missing_slots,
        normalize_slot,
        opportunistic_fill,
        slot_meta,
    )

    def advance(st: ChatState) -> dict:
        meta = slot_meta(business)
        tool = st["tx_tool"]
        slots = dict(st.get("tx_slots") or {})
        if llm.has_key():
            extracted = llm_extract_slots(llm, meta, tool, st["question"], slots)
            for slot in SLOT_ORDER:
                if slot not in extracted or slot in slots:
                    continue
                norm, ok = normalize_slot(meta, slot, extracted[slot])
                if ok:
                    slots[slot] = norm
        elif st.get("tx_last_asked"):
            m = meta.get(st["tx_last_asked"])
            if m:
                v = m["parse"](st["question"])
                if v:
                    slots[st["tx_last_asked"]] = v
        else:
            opportunistic_fill({"tx_tool": tool, "tx_slots": slots}, meta, st["question"])
        apply_days_phrase({"tx_tool": tool, "tx_slots": slots}, st["question"])
        missing = missing_slots(FLOW_DEFS[tool], slots)
        if missing:
            next_slot = missing[0]
            ask = meta[next_slot]["ask"]
            emit(ev.slot_question_evt(next_slot, ask))
            emit(ev.answer_evt(ask))
            return {
                "tx_slots": slots,
                "tx_last_asked": next_slot,
                "tx_phase": "collect",
                "tx_tool": st["tx_tool"],
                "answer": ask,
            }
        # 槽位齐 → 只返回 confirm 状态；确认摘要由 tx_confirm 节点统一发
        # （transaction/tx_resume/tx_gate 三链共用，避免重复 emit）
        return {
            "tx_slots": slots,
            "tx_phase": "confirm",
            "tx_last_asked": "",
            "tx_tool": st["tx_tool"],
        }

    return advance


def make_transaction_node(llm: LLMService, retriever: Retriever, business, tools: dict):
    """transaction 链路（Go StartFlow）：启发式+LLM 工具识别 → 读工具直执行 /
    写工具进槽位收集 / 未识别转知识库。"""
    from gewu.agent.tools import call_tool
    from gewu.agent.tx import (
        FLOW_DEFS,
        _parse_date_slot,
        detect_tool,
        is_read_tool,
        llm_extract_tool,
        slot_meta,
    )

    advance = make_advance(llm, business)

    def transaction(state: ChatState) -> dict:
        meta = slot_meta(business)
        q = state["resolved"]
        tool = detect_tool(q)
        if tool == "" and llm.has_key():
            tool = llm_extract_tool(llm, tools, business, q, state["role"], meta)
        if tool and (is_read_tool(tool) or tool not in FLOW_DEFS):
            # 读操作直接执行，不进确认流
            args: dict[str, str] = {}
            if tool == "query_venues":
                iso = _parse_date_slot(q)
                if iso:
                    args["date"] = iso
            result = call_tool(tools, business, tool, args, state["role"], state["user"])
            emit(ev.action_result_evt(tool, result.ok, result.message, result.receipt or None))
            text = result.message if result.ok else f"办理未完成：{result.message or '未知错误'}。"
            emit(ev.answer_evt(text))
            return {"answer": text}
        if tool in FLOW_DEFS:
            return advance(
                {
                    **state,
                    "tx_tool": tool,
                    "tx_phase": "collect",
                    "tx_slots": {},
                    "tx_last_asked": "",
                }
            )
        # 工具未识别 → 转知识库检索（Go fallbackKnowledge）
        emit(ev.answer_evt("这个问题我理解为你想咨询校园信息，为你转知识库检索："))
        return Command(goto="retrieve", update={"hits": []})

    return transaction


def make_research_node(llm: LLMService, retriever: Retriever):
    """research 链路：plan → 逐路检索 → 证据聚合 → 综合作答。"""
    from gewu.agent.research import run_research

    def research(state: ChatState) -> dict:
        return run_research(state, llm, retriever)

    return research


def make_hybrid_node():
    """hybrid 链路：先政策直答，再转业务办理（Go pipeline hybrid 分支）。"""

    def hybrid(state: ChatState) -> dict:
        emit(ev.status_evt("先回答你的政策问题…"))
        return Command(goto="retrieve", update={"hybrid_then_tx": True})

    return hybrid


def make_hybrid_tx_node():
    """hybrid 的第二段：政策答完后转业务办理。"""

    def hybrid_tx(state: ChatState) -> dict:
        emit(ev.status_evt("接下来为你办理业务…"))
        return Command(goto="transaction")

    return hybrid_tx


def _after_answer_direct(state: ChatState) -> str:
    """answer_direct 后：hybrid 触发 → 转办理；否则结束。"""
    return "hybrid_tx" if state.get("hybrid_then_tx") else "__end__"


def _pending_link(name: str):
    """待接入链路占位节点。"""

    def node(state: ChatState) -> dict:
        emit(ev.error_evt(f"链路 {name} 尚未接入"))
        return {}

    node.__name__ = f"node_{name}"
    return node


def make_tx_gate_node(llm: LLMService, business, tools: dict):
    """interrupt 确认门（Q4 原生机制）：本节点首个动作即 interrupt()（之前零副作用，
    resume 重放安全）。resume 值 = 用户新消息 → 分类处理：
    修改 → goto tx_confirm 重发摘要；确认 → 执行回执；取消 → 回执清状态；
    new_topic → goto route 重走正常路由。
    """
    from langgraph.types import Command, interrupt

    from gewu.agent.tx import (  # noqa: PLC0415
        _CONFIRM_MODIFY_RE,
        FLOW_DEFS,
        SLOT_ORDER,
        classify_reply,
        execute_tool,
        slot_meta,
    )

    def tx_gate(state: ChatState) -> dict:
        meta = slot_meta(business)
        tool = state["tx_tool"]
        flow = FLOW_DEFS[tool]
        payload = {"tool": tool, "label": flow["label"], "args": state.get("tx_slots") or {}}
        user_text = interrupt(payload)  # ← 图在此暂停；resume 值为用户新消息

        st = {**state, "question": user_text}
        intent = classify_reply(llm, meta, user_text, st)

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
            return {
                "tx_phase": "",
                "tx_tool": "",
                "tx_slots": {},
                "tx_last_asked": "",
                "tx_new_topic": True,
            }

        # continue：先尝试理解为「修改」
        slots = dict(state.get("tx_slots") or {})
        modified = False
        for slot in SLOT_ORDER:
            if slot in ("purpose", "reason"):
                continue
            if slot not in flow["required"] and slot not in slots:
                continue
            value = meta[slot]["parse"](user_text)
            if value and value != slots.get(slot):
                slots[slot] = value
                modified = True
        if modified:
            emit(ev.status_evt("已更新，请重新确认："))
            return Command(goto="tx_confirm", update={"tx_slots": slots, "tx_last_asked": ""})

        if _CONFIRM_MODIFY_RE.search(user_text):
            result = execute_tool(tools, business, st, st["role"], st["user"])
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
            # 失败恢复：字段级问题重新追问（collect 态由 tx_resume 续）
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

    return tx_gate


def entry_gate(state: ChatState) -> str:
    """入口分派：办理收集中走 tx_resume；confirm 态由 interrupt/resume 桥接管
    （不会从这里进——SSE 端点检测到 interrupted thread 时以 Command(resume) 恢复）。"""
    if state.get("tx_phase") == "collect":
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
    """消息分层装配（P6 阶段4，顺序固定）：system → [长期记忆] → user。

    记忆块由入口节点写入 state（mem_block），空块时与历史版本逐字一致。
    """
    msgs = [("system", ANSWER_SYSTEM)]
    if state.get("mem_block"):
        msgs.append(("system", "已知用户信息：\n" + state["mem_block"]))
    msgs.append(("user", f"参考资料：\n\n{context}\n\n问题：{state['resolved']}"))
    return msgs


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
    memory=None,
    agent=None,
):
    """装配会话编排主图。checkpointer 缺省内存版（P14-6 换 PostgresSaver）。

    tools 为业务工具表（权限矩阵）。agent 为 create_agent 子图（P17 主循环），
    缺省现场装配；测试可注入 fake 模型驱动的实例。
    """
    from gewu.agent.agent import build_agent
    from gewu.agent.tools import tools_for

    tools = tools if tools is not None else tools_for()
    if agent is None:
        agent = build_agent(settings, llm, retriever, business, tools)
    g: StateGraph = StateGraph(ChatState)
    g.add_node("resolve_query", make_resolve_node(llm, settings, memory))
    g.add_node("agent_in", make_agent_in_node())
    g.add_node("agent", agent)
    g.add_node("agent_done", make_agent_done_node())
    g.add_node("route", make_route_node(llm, settings))
    g.add_node("retrieve", make_retrieve_node(retriever))
    g.add_node("answer_direct", make_answer_node(llm))
    g.add_node("refusal", make_refusal_node())
    g.add_node("tx_confirm", make_tx_confirm_node(business))
    g.add_node("tx_gate", make_tx_gate_node(llm, business, tools))
    g.add_node("tx_resume", make_tx_resume_node(llm, business, tools))
    g.add_node("research", make_research_node(llm, retriever))
    g.add_node("transaction", make_transaction_node(llm, retriever, business, tools))
    g.add_node("hybrid", make_hybrid_node())
    g.add_node("hybrid_tx", make_hybrid_tx_node())

    g.add_conditional_edges(
        START,
        entry_gate,
        {
            "resolve_query": "resolve_query",
            "tx_resume": "tx_resume",
        },
    )
    g.add_conditional_edges(
        "resolve_query",
        mode_dispatch,
        {
            "route": "route",
            "retrieve": "retrieve",
            "research": "research",
            "agent_in": "agent_in",
        },
    )
    g.add_edge("agent_in", "agent")
    g.add_edge("agent", "agent_done")
    g.add_edge("agent_done", END)
    g.add_conditional_edges(
        "route",
        route_branch,
        {
            "factual": "retrieve",
            "refusal": "refusal",
            "research": "research",
            "hybrid": "hybrid",
            "transaction": "transaction",
        },
    )
    g.add_edge("retrieve", "answer_direct")
    g.add_conditional_edges(
        "answer_direct",
        _after_answer_direct,
        {
            "hybrid_tx": "hybrid_tx",
            "__end__": END,
        },
    )
    g.add_edge("refusal", END)
    g.add_conditional_edges(
        "tx_confirm",
        _after_tx_confirm,
        {"tx_gate": "tx_gate", "__end__": END},
    )
    g.add_conditional_edges(
        "tx_resume",
        tx_resume_branch,
        {
            "route": "route",
            "__end__": END,
        },
    )
    g.add_edge("research", END)
    g.add_edge("hybrid_tx", "transaction")
    g.add_conditional_edges(
        "transaction",
        _after_transaction,
        {"tx_confirm": "tx_confirm", "__end__": END},
    )
    g.add_conditional_edges(
        "tx_resume",
        _after_tx_resume,
        {"tx_confirm": "tx_confirm", "__end__": END},
    )
    return g.compile(checkpointer=checkpointer or MemorySaver())


def _after_transaction(state: ChatState) -> str:
    """transaction 后：槽位齐（confirm）→ tx_confirm 发摘要 + interrupt；问过槽位（collect）→ 本轮结束。"""
    return "tx_confirm" if state.get("tx_phase") == "confirm" else "__end__"


def _after_tx_resume(state: ChatState) -> str:
    """tx_resume（collect 续轮）后：槽位补齐 → tx_confirm；否则结束。"""
    return "tx_confirm" if state.get("tx_phase") == "confirm" else "__end__"


def _after_tx_confirm(state: ChatState) -> str:
    """确认摘要发出后进 interrupt 门（tx_phase 仍为 confirm）；异常兜底直 END。"""
    return "tx_gate" if state.get("tx_phase") == "confirm" else "__end__"


def _pending_link(name: str):
    """P14-5/6 待接入链路的占位节点（factual/refusal/react 链路已可用）。"""

    def node(state: ChatState) -> dict:
        emit(ev.error_evt(f"链路 {name} 尚未接入（P14-5/6 ticket）"))
        return {}

    node.__name__ = f"node_{name}"
    return node
