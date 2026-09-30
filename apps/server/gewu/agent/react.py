"""通用单主体 ReAct 引擎（LangGraph 子图，移植自 Go internal/agent/react.go）。

与 workflow 并存不替代：模型每轮通过原生 tool_calls 协议决定调用哪个工具，
无调用即终止（唯一判据）。防护四件套：唯一终止判据；相同 (name,args) 指纹
第 3 次被拒；maxTurns 上限 + 到顶强制收敛兜底；工具错误回填 observation 不断链。
截断防御（P10 铁律）实现为 agent→execute 之间的条件路由：finish_reason=length
且带 tool_calls 时走 truncated 分支——不执行工具、合成错误 observation 回填重发
（Pi 式，不记 seen 指纹）。写操作不直接执行——必填参数齐时转确认流（主图
tx_confirm 节点接管，pending_action → 用户确认 → 执行 → 回执）。
"""

from __future__ import annotations

import json
from typing import Any, TypedDict

from langchain_core.messages import AIMessage, BaseMessage, SystemMessage, ToolMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from gewu.agent import events as ev
from gewu.agent.emitter import emit
from gewu.agent.prompts import react_system_prompt
from gewu.agent.tools import Tool, call_tool, has_role

REACT_MAX_TURNS = 8  # 轮次上限（预算熔断之外的第二道闸）
REACT_REPEAT_LIMIT = 2  # 同一 (name,args) 指纹允许的执行次数；第 3 次拒绝
OBSERVATION_LIMIT = 1500  # 单条 observation 长度上限（控制上下文膨胀）
BUILTIN_TOOLS = ("search_knowledge", "parse_date")


class ReactState(TypedDict, total=False):
    """ReAct 子图状态。msgs 直接持有 langchain 消息对象；ReactContext 经 config.configurable 传入（不进 checkpoint）。"""

    question: str
    role: str
    user: str
    session_id: str
    toolset: list[str]
    mem_block: str
    msgs: list[BaseMessage]
    turn: int
    seen: dict[str, int]
    observations: list[str]
    citations: list[dict]
    final: str
    stop: str  # "" | "confirm"（写操作已转确认流）
    tx_tool: str  # 写操作转确认流时回传主图
    tx_slots: dict
    pending_ai: dict  # agent 节点产出（dict 形态：content/tool_calls/finish_reason）


# ---------- 工具 schema ----------


def biz_tool_schema(name: str, spec: Tool) -> dict:
    """业务工具 → 原生 tools 参数（字段名与 workflow 槽位一致，便于确认流共用归一）。"""

    def prop(desc: str) -> dict:
        return {"type": "string", "description": desc}

    def obj(props: dict, required: list[str] | None = None) -> dict:
        return {"type": "object", "properties": props, "required": required or []}

    if name == "query_venues":
        params = obj({"date": prop("查询日期 YYYY-MM-DD，缺省今天")})
    elif name in ("my_bookings", "pending_leaves"):
        params = obj({})
    elif name == "leave_status":
        params = obj({"ticket_id": prop("请假单号，如 LV-0001")}, ["ticket_id"])
    elif name == "book_venue":
        params = obj(
            {
                "venue": prop("场馆名称，如 羽毛球馆/篮球场/研讨间301"),
                "date": prop("预约日期 YYYY-MM-DD"),
                "slot": prop("时段，如 08:00-10:00/19:00-21:00"),
                "purpose": prop("用途（可选）"),
            },
            ["venue", "date", "slot"],
        )
    elif name == "cancel_booking":
        params = obj({"booking_id": prop("预约单号，如 VE-0001")}, ["booking_id"])
    elif name == "submit_leave":
        params = obj(
            {
                "leave_type": prop("假别：事假/病假/销假"),
                "start_date": prop("开始日期 YYYY-MM-DD"),
                "end_date": prop("结束日期 YYYY-MM-DD"),
                "reason": prop("请假事由"),
            },
            ["leave_type", "start_date", "end_date", "reason"],
        )
    elif name == "approve_leave":
        params = obj({"ticket_id": prop("请假单号")}, ["ticket_id"])
    else:
        params = obj({})
    return {
        "type": "function",
        "function": {"name": name, "description": spec.description, "parameters": params},
    }


SEARCH_SCHEMA = {
    "type": "function",
    "function": {
        "name": "search_knowledge",
        "description": "混合检索校园政策知识库（转专业/保研/奖学金/图书馆/宿舍/校历等），返回带来源的条款原文",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "自包含的检索问题"},
                "k": {"type": "integer", "description": "返回条数 1~10，默认 5"},
            },
            "required": ["query"],
        },
    },
}

DATE_SCHEMA = {
    "type": "function",
    "function": {
        "name": "parse_date",
        "description": '把"明天/下周三/9月2日"等中文日期表述解析为 YYYY-MM-DD',
        "parameters": {
            "type": "object",
            "properties": {"text": {"type": "string", "description": "日期表述原文"}},
            "required": ["text"],
        },
    },
}


def _truncate(s: str, n: int) -> str:
    return s if len(s) <= n else s[:n]


class ReactContext:
    """一次 ReAct 运行的环境（工具表 + 检索器 + 业务系统）。"""

    def __init__(
        self, retriever, business, tools: dict[str, Tool], role: str, user: str, toolset: list[str]
    ) -> None:
        self.retriever = retriever
        self.business = business
        self.tools = tools
        self.role = role
        self.user = user
        allowed = set(toolset or [])
        self.schemas: list[dict] = [SEARCH_SCHEMA, DATE_SCHEMA]
        self.available: set[str] = set(BUILTIN_TOOLS)
        for name, spec in tools.items():
            if has_role(spec.roles, role) and (not allowed or name in allowed):
                self.schemas.append(biz_tool_schema(name, spec))
                self.available.add(name)

    def is_write_tool(self, name: str) -> bool:
        spec = self.tools.get(name)
        return spec is not None and not spec.read_only

    def run_read_tool(self, name: str, args: dict[str, Any], citations: list[dict]) -> str:
        """内置读工具（search_knowledge / parse_date）执行；search 顺带累积引用。"""
        if name == "search_knowledge":
            query = str(args.get("query", "")).strip()
            if not query:
                raise ValueError("缺少参数 query")
            k = args.get("k", 5)
            k = int(k) if isinstance(k, (int, float)) and 1 <= int(k) <= 10 else 5
            hits = [_as_hit_dict(h) for h in self.retriever.search(query, k)]
            if not hits:
                return "未检索到相关资料。"
            _accumulate_citations(citations, hits)
            lines = []
            for i, h in enumerate(hits):
                lines.append(
                    f"[{i + 1}]《{h['title']}》（来源：{h['source']}）\n{_truncate(h['text'], 600)}\n"
                )
            return "\n".join(lines).strip()
        if name == "parse_date":
            from gewu.dates import parse  # noqa: PLC0415

            text = str(args.get("text", "")).strip()
            if not text:
                raise ValueError("缺少参数 text")
            dt = parse(text)
            return f"{text} → {dt.isoformat()}" if dt else f"无法识别日期表述：{text}"
        raise ValueError(f"工具不存在：{name}")

    def run_biz_tool(self, name: str, args: dict[str, Any]) -> str:
        """业务工具经 CallTool 单一出口执行（未知工具/越权/缺参在业务系统前拦截）。"""
        str_args = {k: (v if isinstance(v, str) else str(v)) for k, v in args.items()}
        result = call_tool(self.tools, self.business, name, str_args, self.role, self.user)
        return result.message  # 业务失败也是有效 observation（缺参/越权），交模型决策


def _as_hit_dict(h) -> dict:
    """Retriever 命中（Hit dataclass）→ dict（state 通行形态）。"""
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


def _accumulate_citations(citations: list[dict], hits: list[dict]) -> None:
    """检索命中 → 引用累积去重（doc_id+title 键；编号每次检索从 1 起，对齐 Go）。"""
    have = {c["doc_id"] + c["title"] for c in citations}
    for i, h in enumerate(hits):
        key = h["doc_id"] + h["title"]
        if key not in have:
            have.add(key)
            citations.append(ev.citation(i + 1, h["doc_id"], h["title"], h["source"]))


def _fingerprint(name: str, args: dict) -> str:
    """(name,args) 规范化指纹：json.dumps 排序 key，同一调用必有同一指纹。"""
    try:
        return name + "|" + json.dumps(args or {}, ensure_ascii=False, sort_keys=True)
    except (TypeError, ValueError):
        return name + "|?"


def _ai_dict(ai: AIMessage) -> dict:
    """AIMessage → state 可存 dict（保留 tool_calls 协议配对所需字段）。"""
    return {
        "content": ai.content if isinstance(ai.content, str) else str(ai.content),
        "tool_calls": [
            {"id": c.get("id", ""), "name": c.get("name", ""), "args": c.get("args", {})}
            for c in (ai.tool_calls or [])
        ],
        "finish_reason": (ai.response_metadata or {}).get("finish_reason", ""),
    }


def _assistant_pair(ai_dict: dict, observations: dict[str, str] | None = None) -> list[BaseMessage]:
    """assistant（content+tool_calls）原样回填 + 逐 call 的 tool 消息（协议配对完整）。"""
    ai = AIMessage(
        content=ai_dict["content"],
        tool_calls=[
            {"id": c["id"], "name": c["name"], "args": c["args"], "type": "function"}
            for c in ai_dict["tool_calls"]
        ],
    )
    tools = [
        ToolMessage(content=(observations or {}).get(c["id"], ""), tool_call_id=c["id"])
        for c in ai_dict["tool_calls"]
    ]
    return [ai, *tools]


# ---------- 子图节点 ----------


def _make_agent_node(llm):
    def agent(state: ReactState, config) -> dict:
        ctx: ReactContext = config["configurable"]["react_ctx"]
        msgs = list(
            state.get("msgs")
            or [
                SystemMessage(content=react_system_prompt(state.get("mem_block", ""))),
                _human(state["question"]),
            ]
        )
        ai = llm.chat_with_tools(msgs, ctx.schemas, max_tokens=1200)
        return {"msgs": msgs, "pending_ai": _ai_dict(ai)}

    return agent


def _route_after_agent(state: ReactState) -> str:
    """截断防御铁律：length 先于工具解析（P10）。"""
    ai = state["pending_ai"]
    if ai["finish_reason"] == "length" and ai["tool_calls"]:
        return "truncated"
    return "execute" if ai["tool_calls"] else "finalize"


def _make_truncated_node():
    def truncated(state: ReactState) -> dict:
        ai = state["pending_ai"]
        pair = _assistant_pair(
            ai,
            {
                c[
                    "id"
                ]: "输出达到 token 上限被截断，参数可能不完整，本次未执行。请重新发起完整调用。"
                for c in ai["tool_calls"]
            },
        )
        return {"msgs": list(state["msgs"]) + pair, "turn": state.get("turn", 0) + 1}

    return truncated


def _tx_ready(ctx: ReactContext, name: str, args: dict) -> tuple[bool, list[str], dict[str, str]]:
    """写调用参数 → 确认流槽位（与 workflow 相同的归一口径）；缺失返回清单。"""
    from gewu.agent.tx import FLOW_DEFS, normalize_slot, slot_meta  # noqa: PLC0415

    flow = FLOW_DEFS.get(name)
    meta = slot_meta(ctx.business)
    slots: dict[str, str] = {}
    for k, v in args.items():
        raw = v if isinstance(v, str) else str(v)
        norm, ok = normalize_slot(meta, k, raw)
        slots[k] = norm if ok else raw  # 归一失败保留原值，业务层校验兜底
    if flow is None:
        return True, [], slots
    missing = [s for s in flow["required"] if s not in slots]
    return (not missing), missing, slots


def _make_execute_node():
    def execute_tools(state: ReactState, config) -> dict:
        ctx: ReactContext = config["configurable"]["react_ctx"]
        ai = state["pending_ai"]
        msgs = list(state["msgs"])
        seen = dict(state.get("seen", {}))
        observations = list(state.get("observations", []))
        citations = list(state.get("citations", []))
        turn = state.get("turn", 0)

        msgs.extend(_assistant_pair(ai))
        for call in ai["tool_calls"]:
            name, args, cid = call["name"], call["args"], call["id"]
            if name not in ctx.available:
                out = f"工具不存在：{name}。可用工具：{'、'.join(sorted(ctx.available))}"
            elif seen.get(_fingerprint(name, args), 0) >= REACT_REPEAT_LIMIT:
                out = f"该调用已重复执行 {REACT_REPEAT_LIMIT} 次，结果不会变化。请换思路，或直接给出最终回答。"
            elif ctx.is_write_tool(name):
                # 写操作：必填参数齐 → 转确认流；参数不全 → 提示 agent 先向用户收集
                ready, missing, norm_slots = _tx_ready(ctx, name, args)
                if ready:
                    seen[_fingerprint(name, args)] = seen.get(_fingerprint(name, args), 0) + 1
                    emit(ev.status_evt("已整理办理信息，等待确认…"))
                    return {
                        "msgs": msgs,
                        "seen": seen,
                        "turn": turn + 1,
                        "stop": "confirm",
                        "tx_tool": name,
                        "tx_slots": norm_slots,
                    }
                out = (
                    "缺少必填参数："
                    + "、".join(missing)
                    + "。请先向用户收集这些信息（直接提问，不调用工具），信息齐全后再发起调用。"
                )
            else:
                seen[_fingerprint(name, args)] = seen.get(_fingerprint(name, args), 0) + 1
                emit(ev.status_evt(f"调用工具 {name}…"))
                try:
                    if name in BUILTIN_TOOLS:
                        out = ctx.run_read_tool(name, args, citations)
                    else:
                        out = ctx.run_biz_tool(name, args)
                except Exception as e:  # noqa: BLE001 - 错误回填为 observation，不断链
                    out = f"工具执行失败：{e}"
            observations.append(f"{name}：{_truncate(out, OBSERVATION_LIMIT)}")
            msgs.append(ToolMessage(content=_truncate(out, OBSERVATION_LIMIT), tool_call_id=cid))
        return {
            "msgs": msgs,
            "seen": seen,
            "observations": observations,
            "citations": citations,
            "turn": turn + 1,
        }

    return execute_tools


def _make_finalize_node():
    def finalize(state: ReactState) -> dict:
        ai = state["pending_ai"]
        final = ai["content"].strip()
        if not final:
            final = partial_answer(state.get("observations", []))
        if not final:
            final = "未能获取足够信息回答该问题，请换个说法或补充细节。"
        return {"final": final}

    return finalize


def _make_converge_node(llm):
    """到顶兜底：强制收敛一次；失败用已累积观察组织部分结论（不留裸错误）。"""

    def converge(state: ReactState) -> dict:
        msgs = list(state["msgs"]) + [
            _human("已达到最大工具调用轮次。请基于已有结果直接给出最终回答，不要再调用工具。")
        ]
        try:
            ai = llm.chat_with_tools(msgs, [], max_tokens=1200)
            final = (ai.content if isinstance(ai.content, str) else str(ai.content)).strip()
        except Exception:  # noqa: BLE001
            final = ""
        if not final:
            final = partial_answer(state.get("observations", []))
        return {"final": final}

    return converge


def partial_answer(observations: list[str]) -> str:
    lines = ["已达到最大工具调用轮次，暂未完全办成。已查到的信息："]
    for obs in observations[:5]:
        lines.append("- " + _truncate(obs, 200))
    return "\n".join(lines)


def _after_execute(state: ReactState) -> str:
    if state.get("stop") == "confirm":
        return "__end__"
    if state.get("turn", 0) >= REACT_MAX_TURNS:
        return "converge"
    return "agent"


def _human(content: str):
    from langchain_core.messages import HumanMessage  # noqa: PLC0415

    return HumanMessage(content=content)


def build_react_subgraph(llm) -> CompiledStateGraph:
    """装配 ReAct 子图（ReactContext 经 config.configurable 注入——运行时对象不进 checkpoint）。"""
    g: StateGraph = StateGraph(ReactState)
    g.add_node("agent", _make_agent_node(llm))
    g.add_node("execute", _make_execute_node())
    g.add_node("truncated", _make_truncated_node())
    g.add_node("finalize", _make_finalize_node())
    g.add_node("converge", _make_converge_node(llm))

    g.add_edge(START, "agent")
    g.add_conditional_edges(
        "agent",
        _route_after_agent,
        {
            "truncated": "truncated",
            "execute": "execute",
            "finalize": "finalize",
        },
    )
    g.add_edge("truncated", "agent")
    g.add_conditional_edges(
        "execute",
        _after_execute,
        {
            "__end__": END,
            "converge": "converge",
            "agent": "agent",
        },
    )
    g.add_edge("finalize", END)
    g.add_edge("converge", END)
    return g.compile()
