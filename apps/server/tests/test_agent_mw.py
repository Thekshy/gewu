"""中间件与桥翻译单测（P17-3/4/5）。

截断防御 / effective route 合成 / 写调用就绪判定 / 槽位门 / resume 桥翻译 /
citations 合并 reducer。事件发射在图外静默（emitter 契约），这里断言返回值。
"""

from __future__ import annotations

from langchain.agents.middleware.types import ModelRequest, ModelResponse, ToolCallRequest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from gewu.agent.mw import (
    TruncationDefenseMiddleware,
    WriteSlotGateMiddleware,
    _merge_citations,
    effective_route,
    write_call_ready,
)
from gewu.agent.resume import hitl_decisions
from gewu.business.db import Business
from tests.agent_fakes import FakeAgentLLM


def _tool_call(name: str, args: dict, cid: str = "c1") -> dict:
    return {"name": name, "args": args, "id": cid, "type": "function"}


def _ai(tool_calls: list[dict] | None = None, content: str = "", finish: str = "") -> AIMessage:
    return AIMessage(
        content=content,
        tool_calls=tool_calls or [],
        response_metadata={"finish_reason": finish} if finish else {},
    )


# ---------- 截断防御（P10 铁律平移） ----------


def test_truncation_defense_refires_without_executing():
    calls: list[list] = []

    def handler(request):
        calls.append(list(request.messages))
        if len(calls) == 1:
            return ModelResponse(
                result=[_ai([_tool_call("search_knowledge", {"query": "q"})], finish="length")]
            )
        return ModelResponse(result=[_ai(content="重发后的完整回答")])

    req = ModelRequest(model=None, messages=[HumanMessage(content="问题")])
    resp = TruncationDefenseMiddleware().wrap_model_call(req, handler)
    assert resp.result[-1].content == "重发后的完整回答"
    assert len(calls) == 2
    # 重发请求里带回了 assistant + 合成错误 observation（工具未执行）
    kinds = [type(m).__name__ for m in calls[1]]
    assert kinds.count("ToolMessage") == 1
    assert "截断" in calls[1][-1].content


def test_truncation_defense_passes_normal_response():
    def handler(request):
        return ModelResponse(result=[_ai(content="正常")])

    req = ModelRequest(model=None, messages=[HumanMessage(content="q")])
    resp = TruncationDefenseMiddleware().wrap_model_call(req, handler)
    assert resp.result[-1].content == "正常"


def test_truncation_defense_caps_refires():
    boom = [_ai([_tool_call("search_knowledge", {"query": "q"})], finish="length")] * 5

    def handler(request):
        return ModelResponse(result=[boom.pop(0)])

    req = ModelRequest(model=None, messages=[HumanMessage(content="q")])
    TruncationDefenseMiddleware().wrap_model_call(req, handler)
    assert len(boom) == 5 - 1 - TruncationDefenseMiddleware.MAX_REFIRE  # 首调+重发有上限


# ---------- effective route 合成 ----------


def _msgs(*xs):
    return list(xs)


def test_effective_route_matrix():
    h = HumanMessage(content="q")
    assert effective_route(_msgs(h, _ai(content="你好")))[0] == "chitchat"
    assert (
        effective_route(
            _msgs(h, _ai(), ToolMessage(content="x", name="search_knowledge", tool_call_id="t1"))
        )[0]
        == "factual"
    )
    assert (
        effective_route(
            _msgs(h, _ai(), ToolMessage(content="x", name="deep_research", tool_call_id="t1"))
        )[0]
        == "research"
    )
    assert (
        effective_route(
            _msgs(h, _ai(), ToolMessage(content="x", name="book_venue", tool_call_id="t1"))
        )[0]
        == "transaction"
    )
    both = _msgs(
        h,
        _ai(),
        ToolMessage(content="x", name="search_knowledge", tool_call_id="t1"),
        ToolMessage(content="x", name="submit_leave", tool_call_id="t1"),
    )
    assert effective_route(both)[0] == "hybrid"


# ---------- 写调用就绪与槽位门 ----------


def test_write_call_ready(tmp_path):
    b = Business(tmp_path / "b.db")
    assert write_call_ready(
        b,
        _tool_call(
            "book_venue", {"venue": "羽毛球馆", "date": "2026-10-02", "slot": "19:00-21:00"}
        ),
    )
    assert not write_call_ready(
        b, _tool_call("book_venue", {"date": "2026-10-02", "slot": "19:00-21:00"})
    )
    assert not write_call_ready(
        b,
        _tool_call(
            "submit_leave",
            {"leave_type": "事假", "start_date": "", "end_date": "2026-10-03", "reason": "x"},
        ),
    )
    # 非写工具不设门
    assert write_call_ready(b, _tool_call("search_knowledge", {}))


def test_slot_gate_returns_guidance_without_execution(tmp_path):
    b = Business(tmp_path / "b.db")
    mw = WriteSlotGateMiddleware(b)
    req = ToolCallRequest(
        tool_call=_tool_call("book_venue", {"date": "2026-10-02", "slot": "19:00-21:00"}),
        tool=None,
        state={"messages": []},
        runtime=None,
    )
    executed = []

    def handler(r):
        executed.append(1)
        return ToolMessage(content="done", tool_call_id="c1")

    out = mw.wrap_tool_call(req, handler)
    assert not executed  # 缺 venue：不执行
    assert "缺少必填参数 venue" in out.content
    assert out.name == "book_venue"


# ---------- resume 桥翻译 ----------


def _payload(args: dict) -> dict:
    return {
        "action_requests": [{"name": "book_venue", "args": args, "description": "d"}],
        "review_configs": [
            {"action_name": "book_venue", "allowed_decisions": ["approve", "reject", "respond"]}
        ],
    }


_FULL = {"venue": "羽毛球馆", "date": "2026-10-02", "slot": "14:00-16:00"}


def test_resume_confirm_maps_approve(tmp_path):
    dec = hitl_decisions(_payload(_FULL), "确认", FakeAgentLLM(), Business(tmp_path / "b.db"))
    assert dec["decisions"] == [{"type": "approve"}]


def test_resume_cancel_maps_reject(tmp_path):
    dec = hitl_decisions(_payload(_FULL), "算了不约了", FakeAgentLLM(), Business(tmp_path / "b.db"))
    assert dec["decisions"][0]["type"] == "reject"
    assert "取消" in dec["decisions"][0]["message"]


def test_resume_modify_maps_respond(tmp_path):
    dec = hitl_decisions(
        _payload(_FULL), "改成晚上七点吧", FakeAgentLLM(), Business(tmp_path / "b.db")
    )
    d = dec["decisions"][0]
    assert d["type"] == "respond" and "重新发起调用" in d["message"]


def test_resume_new_topic_maps_respond(tmp_path):
    dec = hitl_decisions(
        _payload(_FULL), "图书馆几点开门", FakeAgentLLM(), Business(tmp_path / "b.db")
    )
    d = dec["decisions"][0]
    assert d["type"] == "respond" and "别的事" in d["message"]


def test_resume_ambiguous_asks_restate(tmp_path):
    dec = hitl_decisions(_payload(_FULL), "嗯嗯", FakeAgentLLM(), Business(tmp_path / "b.db"))
    d = dec["decisions"][0]
    assert d["type"] == "respond" and "确认" in d["message"]


# ---------- citations 合并 reducer ----------


def test_merge_citations_dedupes_and_renumbers():
    a = [{"n": 1, "doc_id": "d1", "title": "t1", "source": "s"}]
    b = [
        {"n": 1, "doc_id": "d1", "title": "t1", "source": "s"},
        {"n": 2, "doc_id": "d2", "title": "t2", "source": "s"},
    ]
    merged = _merge_citations(a, b)
    assert [(c["doc_id"], c["n"]) for c in merged] == [("d1", 1), ("d2", 2)]
    assert _merge_citations(None, None) == []


def test_resume_reason_supplement_maps_respond(tmp_path):
    """回归（tx-005 失败根因）：确认轮补充事由 = 修改，不是「不明确」。"""
    payload = {
        "action_requests": [
            {
                "name": "submit_leave",
                "args": {
                    "leave_type": "病假",
                    "start_date": "2026-12-01",
                    "end_date": "2026-12-10",
                    "reason": "生病请假（病假）",
                },
                "description": "d",
            }
        ],
        "review_configs": [],
    }
    dec = hitl_decisions(payload, "发烧需要休息", FakeAgentLLM(), Business(tmp_path / "b.db"))
    d = dec["decisions"][0]
    assert d["type"] == "respond" and "reason" in d["message"]


def test_resume_confirm_word_beats_soft_supplement(tmp_path):
    """「确认」不能被自由文本 parse 吞掉（短且含确认词 → approve）。"""
    payload = {
        "action_requests": [
            {
                "name": "submit_leave",
                "args": {
                    "leave_type": "病假",
                    "start_date": "2026-12-01",
                    "end_date": "2026-12-10",
                    "reason": "生病请假（病假）",
                },
                "description": "d",
            }
        ],
        "review_configs": [],
    }
    dec = hitl_decisions(payload, "确认", FakeAgentLLM(), Business(tmp_path / "b.db"))
    assert dec["decisions"] == [{"type": "approve"}]
