"""P33-2 run_flow / query_flows 全链流测试。

覆盖：读流程经 run_flow（事件 tool=flow_id）/ 写流程经 run_flow 两轮确认
（Q3 PARITY：确认卡片与专属路径同形）/ 缺参槽位门 / 未知与缺失 flow_id
回执 / 越权回执 / query_flows 角色过滤与 q 匹配 / 已退役专属工具幻觉调用
优雅降级。事件经 custom 流收集，回执断言走 ToolMessage（get_state）。
"""

from __future__ import annotations

from datetime import date, timedelta

from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from gewu.agent.agent import build_agent
from gewu.agent.resume import find_hitl_payload, hitl_decisions
from gewu.agent.tools import tools_for
from gewu.config import Settings
from tests.agent_fakes import FakeAgentLLM, FakeRetriever, make_hit


def _ai_text(content: str) -> AIMessage:
    return AIMessage(content=content)


def _ai_call(name: str, args: dict, cid: str = "call_1") -> AIMessage:
    return AIMessage(
        content="", tool_calls=[{"name": name, "args": args, "id": cid, "type": "function"}]
    )


def _tomorrow() -> str:
    return (date.today() + timedelta(days=1)).isoformat()


def make_graph(tmp_path, biz, script, *, role: str = "student"):
    settings = Settings(llm_api_key="k", embed_api_key="e", data_dir=tmp_path)
    llm = FakeAgentLLM(script=script)
    graph = build_agent(
        settings, llm, FakeRetriever([make_hit()]), biz, tools_for(), checkpointer=MemorySaver()
    )
    return graph, llm


def run_turn(
    graph, sid: str, question: str = "", *, role: str = "student", resume=None
) -> list[dict]:
    cfg = {"configurable": {"thread_id": sid}}
    if resume is not None:
        inp: object = Command(resume=resume)
    else:
        inp = {
            "messages": [HumanMessage(content=question)],
            "question": question,
            "mode": "auto",
            "session_id": sid,
            "role": role,
            "user": "demo-student",
            "citations": [],
            "answer_streamed": "",
            "truncated": False,
            "answer": "",
        }
    events: list[dict] = []
    for chunk in graph.stream(inp, cfg, stream_mode="custom"):
        events.append(chunk)
    return events


def _tool_message(graph, cfg, name: str) -> str:
    """终态消息里找指定工具的回执文本（query_flows 清单/unknown 回执断言用）。"""
    msgs = (graph.get_state(cfg).values or {}).get("messages") or []
    for m in reversed(msgs):
        if getattr(m, "name", "") == name:
            return str(m.content)
    return ""


def test_read_flow_via_run_flow_keeps_event_parity(tmp_path, biz):
    """样板路径：leave_status 经 query 清单语义的 run_flow 直查，事件 tool=flow_id。"""
    biz.submit_leave("demo-student", "事假", _tomorrow(), _tomorrow(), "家里有事")
    script = [
        _ai_call("run_flow", {"flow_id": "leave_status", "slots": {"ticket_id": "LV-0001"}}),
        _ai_text("你的请假单正在辅导员审批中。"),
    ]
    graph, _ = make_graph(tmp_path, biz, script)
    cfg = {"configurable": {"thread_id": "rf1"}}
    events = run_turn(graph, "rf1", "帮我查下请假单 LV-0001 的进度")
    results = [e for e in events if e["type"] == "action_result"]
    assert results and results[0]["success"] and results[0]["tool"] == "leave_status"
    assert "事假" in results[0]["message"]
    assert "事假" in _tool_message(graph, cfg, "run_flow")  # 回执 ToolMessage.name=run_flow
    assert not any(e["type"] == "pending_action" for e in events)  # 读流程无确认门
    assert "factual" in {e["route"] for e in events if e["type"] == "route"}


def test_write_flow_via_run_flow_interrupts_and_executes(tmp_path, biz):
    """Q3/Q5：写流程经 run_flow 两轮确认——pending_action 与专属路径同形。"""
    script = [
        _ai_call(
            "run_flow",
            {
                "flow_id": "submit_leave",
                "slots": {
                    "leave_type": "事假",
                    "start_date": _tomorrow(),
                    "end_date": _tomorrow(),
                    "reason": "家里有事",
                },
            },
        ),
        _ai_text("请假申请已提交。"),
    ]
    graph, llm = make_graph(tmp_path, biz, script)
    cfg = {"configurable": {"thread_id": "rf2"}}
    events = run_turn(graph, "rf2", "帮我请一天事假，明天，家里有事")
    pas = [e for e in events if e["type"] == "pending_action"]
    assert pas and pas[0]["tool"] == "submit_leave" and pas[0]["label"] == "请假申请"
    snap = graph.get_state(cfg)
    assert snap.next, "run_flow 写流程必须停在确认门"
    payload = find_hitl_payload(snap)
    assert payload["action_requests"][0]["name"] == "run_flow"  # 中断载荷是 run_flow 形态

    resume_events = run_turn(graph, "rf2", resume=hitl_decisions(payload, "确认", llm, biz))
    results = [e for e in resume_events if e["type"] == "action_result"]
    assert results and results[0]["success"] and results[0]["tool"] == "submit_leave"
    assert any(t["user"] == "demo-student" for t in biz.pending_leaves())
    assert "transaction" in {e["route"] for e in resume_events if e["type"] == "route"}


def test_write_flow_via_run_flow_modification_round(tmp_path, biz):
    """写流程经 run_flow 的修改轮：resume 桥解包载荷后槽位修改被识别，重新确认。"""
    script = [
        _ai_call(
            "run_flow",
            {
                "flow_id": "book_venue",
                "slots": {"venue": "羽毛球馆", "date": _tomorrow(), "slot": "19:00-21:00"},
            },
        ),
        _ai_call(
            "run_flow",
            {
                "flow_id": "book_venue",
                "slots": {"venue": "羽毛球馆", "date": _tomorrow(), "slot": "14:00-16:00"},
            },
        ),
        _ai_text("已按新时段预约。"),
    ]
    graph, llm = make_graph(tmp_path, biz, script)
    cfg = {"configurable": {"thread_id": "rf3"}}
    run_turn(graph, "rf3", "帮我预约明天晚上的羽毛球馆")
    payload = find_hitl_payload(graph.get_state(cfg))
    # 用户要求改时段：respond 让模型按新参数重发（run_flow 形态载荷解包后走同一套解析）
    decisions = hitl_decisions(payload, "改成明天中午", llm, biz)
    dec = decisions["decisions"][0]
    assert dec["type"] == "respond" and "修改" in dec["message"]
    resume_events = run_turn(graph, "rf3", resume=decisions)
    assert any(e["type"] == "pending_action" for e in resume_events)  # 二次确认卡片
    payload2 = find_hitl_payload(graph.get_state(cfg))
    assert payload2["action_requests"][0]["args"]["slots"]["slot"] == "14:00-16:00"
    run_turn(graph, "rf3", resume=hitl_decisions(payload2, "确认", llm, biz))
    assert any(b["slot"] == "14:00-16:00" for b in biz.my_bookings("demo-student"))


def test_write_flow_via_run_flow_missing_args_slot_gate(tmp_path, biz):
    script = [
        _ai_call(
            "run_flow",
            {"flow_id": "book_venue", "slots": {"date": _tomorrow(), "slot": "19:00-21:00"}},
        ),
        _ai_text("想预约哪个场馆？"),
    ]
    graph, _ = make_graph(tmp_path, biz, script)
    events = run_turn(graph, "rf4", "帮我约个明晚七点的场地")
    slots = [e for e in events if e["type"] == "slot_question"]
    assert slots and slots[0]["slot"] == "venue"
    assert not any(e["type"] == "pending_action" for e in events)
    assert biz.my_bookings("demo-student") == []
    assert not graph.get_state({"configurable": {"thread_id": "rf4"}}).next  # 未中断


def test_run_flow_unknown_or_missing_flow_id_receipt(tmp_path, biz):
    """未知/缺失 flow_id：闸放行不中断，执行层 unknown 语义回执兜底。"""
    for i, call_args in enumerate(({"flow_id": "nope"}, {"args": {}}, {})):
        script = [_ai_call("run_flow", call_args), _ai_text("这个我办不了。")]
        graph, _ = make_graph(tmp_path, biz, script)
        sid = f"rf5{i}"
        events = run_turn(graph, sid, "办一下那个流程")
        results = [e for e in events if e["type"] == "action_result"]
        assert results and not results[0]["success"], call_args
        assert "未知流程" in results[0]["message"]
        assert "query_flows" in results[0]["message"]
        assert not graph.get_state({"configurable": {"thread_id": sid}}).next
        assert biz.my_bookings("demo-student") == []


def test_run_flow_permission_denied_receipt(tmp_path, biz):
    """学生经 run_flow 调 approve_leave：确认门先拦（写流程与入口形态正交），
    确认后执行层权限矩阵拦截，收越权回执。"""
    biz.submit_leave("someone-else", "事假", _tomorrow(), _tomorrow(), "家里有事")
    script = [
        _ai_call("run_flow", {"flow_id": "approve_leave", "slots": {"ticket_id": "LV-0001"}}),
        _ai_text("抱歉，审批需要辅导员身份。"),
    ]
    graph, llm = make_graph(tmp_path, biz, script)
    cfg = {"configurable": {"thread_id": "rf6"}}
    events = run_turn(graph, "rf6", "帮我批准请假单 LV-0001")
    pas = [e for e in events if e["type"] == "pending_action"]
    assert pas and pas[0]["tool"] == "approve_leave"  # 确认卡片 label 与专属路径同形
    payload = find_hitl_payload(graph.get_state(cfg))
    resume_events = run_turn(graph, "rf6", resume=hitl_decisions(payload, "确认", llm, biz))
    results = [e for e in resume_events if e["type"] == "action_result"]
    assert results and not results[0]["success"] and "无权" in results[0]["message"]
    assert results[0]["tool"] == "approve_leave"


def test_query_flows_role_filter_and_q_match(tmp_path, biz):
    """清单按角色过滤；q 对 id/名称/说明/触发词做包含匹配。"""
    script = [_ai_call("query_flows", {}), _ai_text("可以查请假进度、约场馆等。")]
    graph, _ = make_graph(tmp_path, biz, script)
    cfg = {"configurable": {"thread_id": "qf1"}}
    run_turn(graph, "qf1", "我都能办什么业务")
    listing = _tool_message(graph, cfg, "query_flows")
    assert "leave_status" in listing and "submit_leave" in listing
    assert "approve_leave" not in listing  # 辅导员专属，学生清单不可见
    assert "run_flow(flow_id" in listing

    script2 = [_ai_call("query_flows", {"q": "请假"}), _ai_text("好。")]
    graph2, _ = make_graph(tmp_path, biz, script2)
    cfg2 = {"configurable": {"thread_id": "qf2"}}
    run_turn(graph2, "qf2", "请假相关的有哪些", role="counselor")
    counselor_listing = _tool_message(graph2, cfg2, "query_flows")
    assert "approve_leave" in counselor_listing  # 辅导员可见
    assert "book_venue" not in counselor_listing  # q=请假 过滤掉预约域

    script3 = [_ai_call("query_flows", {"q": "不存在的词"}), _ai_text("没有这个流程。")]
    graph3, _ = make_graph(tmp_path, biz, script3)
    run_turn(graph3, "qf3", "有没有奇怪的流程")
    empty = _tool_message(graph3, {"configurable": {"thread_id": "qf3"}}, "query_flows")
    assert "没有匹配" in empty


def test_hallucinated_retired_tool_degrades_gracefully(tmp_path, biz):
    """风险守卫：模型幻觉调用已退役的 leave_status 专属名——图不崩，零执行。"""
    biz.submit_leave("demo-student", "事假", _tomorrow(), _tomorrow(), "家里有事")
    script = [
        _ai_call("leave_status", {"ticket_id": "LV-0001"}),
        _ai_text("请用流程查询功能再试一次。"),
    ]
    graph, _ = make_graph(tmp_path, biz, script)
    events = run_turn(graph, "rf7", "查一下 LV-0001")
    assert not any(e["type"] == "action_result" for e in events)  # 业务层零执行
    assert not any(e["type"] == "error" for e in events)  # 主链路不崩
    assert "请用流程查询功能再试一次" in "".join(
        e["text"] for e in events if e["type"] == "answer_delta"
    )
