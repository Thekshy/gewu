"""中间件与桥翻译单测（P17-3/4/5）。

截断防御 / effective route 合成 / 写调用就绪判定 / 槽位门 / resume 桥翻译 /
citations 合并 reducer。事件发射在图外静默（emitter 契约），这里断言返回值。
"""

from __future__ import annotations

from langchain.agents.middleware.types import ModelRequest, ModelResponse, ToolCallRequest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from gewu.agent.mw import (
    SearchQueryGuardMiddleware,
    TruncationDefenseMiddleware,
    UsageRecordMiddleware,
    WebSearchBudgetMiddleware,
    WriteSlotGateMiddleware,
    _merge_citations,
    effective_route,
    write_call_ready,
)
from gewu.agent.prompts import agent_system_prompt
from gewu.agent.resume import hitl_decisions
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


def test_write_call_ready(biz):
    b = biz
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


def test_slot_gate_returns_guidance_without_execution(biz):
    b = biz
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


def test_resume_confirm_maps_approve(biz):
    dec = hitl_decisions(_payload(_FULL), "确认", FakeAgentLLM(), biz)
    assert dec["decisions"] == [{"type": "approve"}]


def test_resume_cancel_maps_reject(biz):
    dec = hitl_decisions(_payload(_FULL), "算了不约了", FakeAgentLLM(), biz)
    assert dec["decisions"][0]["type"] == "reject"
    assert "取消" in dec["decisions"][0]["message"]


def test_resume_modify_maps_respond(biz):
    dec = hitl_decisions(_payload(_FULL), "改成晚上七点吧", FakeAgentLLM(), biz)
    d = dec["decisions"][0]
    assert d["type"] == "respond" and "重新发起调用" in d["message"]


def test_resume_new_topic_maps_respond(biz):
    dec = hitl_decisions(_payload(_FULL), "图书馆几点开门", FakeAgentLLM(), biz)
    d = dec["decisions"][0]
    assert d["type"] == "respond" and "别的事" in d["message"]


def test_resume_ambiguous_asks_restate(biz):
    dec = hitl_decisions(_payload(_FULL), "嗯嗯", FakeAgentLLM(), biz)
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


def test_resume_reason_supplement_maps_respond(biz):
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
    dec = hitl_decisions(payload, "发烧需要休息", FakeAgentLLM(), biz)
    d = dec["decisions"][0]
    assert d["type"] == "respond" and "reason" in d["message"]


def test_resume_confirm_word_beats_soft_supplement(biz):
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
    dec = hitl_decisions(payload, "确认", FakeAgentLLM(), biz)
    assert dec["decisions"] == [{"type": "approve"}]


# ---------- P24-1/P24-3：主循环埋点与检索词代码闸 ----------


def test_usage_record_prints_agent_loop_telemetry(capsys):
    class _Svc:
        def record_usage(self, n):
            pass

    mw = UsageRecordMiddleware(_Svc())
    req = ModelRequest(model=None, messages=[HumanMessage(content="问"), AIMessage(content="答")])
    mw.wrap_model_call(req, lambda r: ModelResponse(result=[AIMessage(content="答")]))
    out = capsys.readouterr().out
    assert "[llm] agent主循环 ms=" in out
    assert "msgs=2 chars=" in out  # ctx_profile 复用（log-report.sh 的 chars= 正则兼容）


def _guard_req(name: str, args: dict, question: str) -> ToolCallRequest:
    return ToolCallRequest(
        tool_call=_tool_call(name, args),
        tool=None,
        state={"messages": [HumanMessage(content=question)]},
        runtime=None,
    )


def test_search_query_guard_appends_question_on_zero_overlap():
    mw = SearchQueryGuardMiddleware()
    seen = []

    def handler(r):
        seen.append(r.tool_call["args"]["query"])
        return ToolMessage(content="ok", tool_call_id="c1")

    req = _guard_req("search_knowledge", {"query": "补考安排"}, "体育挂科了怎么办")
    mw.wrap_tool_call(req, handler)
    assert seen == ["体育挂科了怎么办 补考安排"]  # 零重合：原话拼在前（增补不替换）


def test_search_query_guard_passes_when_entity_kept():
    mw = SearchQueryGuardMiddleware()
    seen = []

    def handler(r):
        seen.append(r.tool_call["args"]["query"])
        return ToolMessage(content="ok", tool_call_id="c1")

    # 线上实例：「食堂」bigram 命中 → 不干预
    req = _guard_req("search_knowledge", {"query": "食堂位置 就餐指南"}, "你知道学校食堂在哪买")
    mw.wrap_tool_call(req, handler)
    assert seen == ["食堂位置 就餐指南"]


def test_search_query_guard_ignores_deep_research():
    mw = SearchQueryGuardMiddleware()
    seen = []

    def handler(r):
        seen.append(r.tool_call["args"]["question"])
        return ToolMessage(content="ok", tool_call_id="c1")

    # deep_research 不拦：sub 是 plan 拆解产物本非原话
    mw.wrap_tool_call(
        _guard_req("deep_research", {"question": "转专业并且保研"}, "转专业和保研冲突吗"), handler
    )
    assert seen == ["转专业并且保研"]


# ---------- P26：联网检索（日限闸 / guard 扩展 / 路由分支 / 能力注入） ----------


def _budget_req() -> ToolCallRequest:
    return ToolCallRequest(
        tool_call=_tool_call("web_search", {"query": "q"}),
        tool=None,
        state={"messages": [HumanMessage(content="问题")]},
        runtime=None,
    )


def test_web_search_budget_blocks_after_limit():
    mw = WebSearchBudgetMiddleware(daily_limit=2)
    executed: list[int] = []

    def handler(r):
        executed.append(1)
        return ToolMessage(content="ok", tool_call_id="c1")

    mw.wrap_tool_call(_budget_req(), handler)
    mw.wrap_tool_call(_budget_req(), handler)
    out = mw.wrap_tool_call(_budget_req(), handler)  # 第 3 次：拦截不执行
    assert len(executed) == 2
    assert "额度已用完" in out.content
    assert out.name == "web_search"


def test_web_search_budget_ignores_other_tools():
    mw = WebSearchBudgetMiddleware(daily_limit=0)  # 0=闸全关
    executed: list[int] = []

    def handler(r):
        executed.append(1)
        return ToolMessage(content="ok", tool_call_id="c1")

    req = ToolCallRequest(
        tool_call=_tool_call("search_knowledge", {"query": "q"}),
        tool=None,
        state={"messages": []},
        runtime=None,
    )
    mw.wrap_tool_call(req, handler)
    assert executed  # 非联网工具不受日限闸影响


def test_web_search_budget_resets_on_new_day(monkeypatch):
    import gewu.agent.mw as mw_mod

    mw = WebSearchBudgetMiddleware(daily_limit=1)
    executed: list[int] = []

    def handler(r):
        executed.append(1)
        return ToolMessage(content="ok", tool_call_id="c1")

    monkeypatch.setattr(mw_mod, "today_iso", lambda: "2026-10-01")
    mw.wrap_tool_call(_budget_req(), handler)
    blocked = mw.wrap_tool_call(_budget_req(), handler)
    assert len(executed) == 1 and "额度已用完" in blocked.content
    monkeypatch.setattr(mw_mod, "today_iso", lambda: "2026-10-02")  # 跨日重置
    mw.wrap_tool_call(_budget_req(), handler)
    assert len(executed) == 2


def test_search_query_guard_covers_web_search():
    mw = SearchQueryGuardMiddleware()
    seen: list[str] = []

    def handler(r):
        seen.append(r.tool_call["args"]["query"])
        return ToolMessage(content="ok", tool_call_id="c1")

    req = _guard_req("web_search", {"query": "college entrance exam"}, "四六级报名截止了吗")
    mw.wrap_tool_call(req, handler)
    assert seen == ["四六级报名截止了吗 college entrance exam"]  # 丢原词同样拼回


def test_effective_route_web_branch():
    h = HumanMessage(content="q")
    assert effective_route(
        _msgs(h, _ai(), ToolMessage(content="x", name="web_search", tool_call_id="t1"))
    ) == ("factual", "本轮联网作答")
    # 联网+知识库混合：检索作答口径优先（检索是主证据源）
    both = _msgs(
        h,
        _ai(),
        ToolMessage(content="x", name="web_search", tool_call_id="t1"),
        ToolMessage(content="x", name="search_knowledge", tool_call_id="t2"),
    )
    assert effective_route(both) == ("factual", "本轮检索作答")


def test_agent_system_prompt_capability_injection():
    off = agent_system_prompt("")
    assert "web_search" not in off  # 能力关闭：工具名不出现在提示词
    assert "不要拒绝" in off  # 通用化准则常驻
    on = agent_system_prompt("", web_search=True)
    assert "联网检索规则" in on and "web_search" in on
    # 记忆块保持尾部注入（联网准则插在记忆块之前）
    with_mem = agent_system_prompt("绩点 3.8", web_search=True)
    assert with_mem.endswith("绩点 3.8")
    assert with_mem.index("联网检索规则") < with_mem.index("已知用户信息")


# ---------- P27：工具观测接缝与 llm span ----------


def test_tool_trace_middleware_records_args_and_output():
    from gewu.agent.mw import ToolTraceMiddleware
    from gewu.obs import Tracer, set_current_tracer

    tracer = Tracer(None, {"session_id": "s", "user": "u", "role": "r", "mode": "auto", "q": "x"})
    set_current_tracer(tracer)
    try:
        mw = ToolTraceMiddleware()
        req = _guard_req("web_search", {"query": "tyloo 比赛结果", "k": 5}, "问")
        out = mw.wrap_tool_call(
            req, lambda r: ToolMessage(content="联网检索结果…", tool_call_id="c1")
        )
        assert "联网检索结果" in out.content
        sp = tracer.spans[0]
        assert (sp.kind, sp.name, sp.status) == ("tool", "web_search", "ok")
        assert sp.input == {"query": "tyloo 比赛结果", "k": 5}  # args 原样（盲区根治）
        assert sp.output == {"content": "联网检索结果…"}
        assert sp.latency_ms >= 0
    finally:
        set_current_tracer(None)


def test_tool_trace_middleware_zero_cost_without_tracer():
    from gewu.agent.mw import ToolTraceMiddleware

    mw = ToolTraceMiddleware()
    called: list[int] = []
    out = mw.wrap_tool_call(
        _guard_req("search_knowledge", {"query": "q"}, "问"),
        lambda r: (called.append(1), ToolMessage(content="ok", tool_call_id="c1"))[1],
    )
    assert called and out.content == "ok"


def test_usage_record_middleware_writes_llm_span():
    from gewu.obs import Tracer, set_current_tracer

    class _Svc:
        def record_usage(self, n):
            pass

    tracer = Tracer(None, {"session_id": "s", "user": "u", "role": "r", "mode": "auto", "q": "x"})
    set_current_tracer(tracer)
    try:
        mw = UsageRecordMiddleware(_Svc())

        def handler(r):
            return ModelResponse(
                result=[AIMessage(content="答", usage_metadata={"input_tokens": 10, "output_tokens": 32, "total_tokens": 42})]
            )

        req = ModelRequest(model=None, messages=[HumanMessage(content="问")])
        mw.wrap_model_call(req, handler)
        sp = tracer.spans[0]
        assert (sp.kind, sp.name, sp.tokens) == ("llm", "agent", 42)  # 假模型无名→agent
        assert sp.input["msgs"] == 1
    finally:
        set_current_tracer(None)


def test_agent_system_prompt_injects_today_head():
    off = agent_system_prompt("")
    assert off.startswith("今天是 20") and "（周" in off  # P27-0 日期头
    fixed = agent_system_prompt("", today="2026-10-01（周四）")
    assert fixed.startswith("今天是 2026-10-01（周四）")
    with_mem = agent_system_prompt("绩点 3.8", web_search=True, today="2026-10-01（周四）")
    assert with_mem.endswith("绩点 3.8")  # 尾部序不变：日期头 → 主体 → 联网准则 → 记忆块
    assert (
        with_mem.index("今天是") < with_mem.index("联网检索规则") < with_mem.index("已知用户信息")
    )
