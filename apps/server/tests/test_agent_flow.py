"""agent-first 全链流测试（P17-2/3）：外壳图 + create_agent 子图真跑。

覆盖：寒暄直答 / 检索引用（Command 状态更新）/ 写操作 HITL 中断与 resume
（approve）/ 缺参槽位门 / 越权回执 / classic 冒烟。事件经 custom 流收集。
"""

from __future__ import annotations

from datetime import date, timedelta

from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from gewu.agent.agent import build_agent
from gewu.agent.graph import build_graph
from gewu.agent.resume import find_hitl_payload, hitl_decisions
from gewu.agent.state import new_state
from gewu.agent.tools import tools_for
from gewu.business.db import Business
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


def make_flow(tmp_path, biz, script, *, business: Business | None = None):
    settings = Settings(llm_api_key="k", embed_api_key="e", data_dir=tmp_path)
    retriever = FakeRetriever([make_hit()])
    business = business or biz
    llm = FakeAgentLLM(script=script)
    agent = build_agent(settings, llm, retriever, business, tools_for())
    graph = build_graph(
        settings, retriever, llm, business=business, checkpointer=MemorySaver(), agent=agent
    )
    return graph, llm, business


def run_turn(graph, sid: str, question: str | None = None, resume=None) -> list[dict]:
    cfg = {"configurable": {"thread_id": sid}}
    if resume is not None:
        inp: object = Command(resume=resume)
    else:
        inp = new_state(question or "", "auto", sid, "student", "demo-student")
    events: list[dict] = []
    for chunk in graph.stream(inp, cfg, stream_mode="custom", subgraphs=True):
        events.append(chunk[-1] if isinstance(chunk, tuple) else chunk)
    return events


def _answer(events: list[dict]) -> str:
    return "".join(e["text"] for e in events if e["type"] == "answer_delta")


def _routes(events: list[dict]) -> set:
    return {e["route"] for e in events if e["type"] == "route"}


def test_chitchat_zero_tool_direct_answer(tmp_path, biz):
    graph, _, _ = make_flow(
        tmp_path, biz, [_ai_text("你好！我是格物，可以帮你查政策、约场馆、办请假。")]
    )
    events = run_turn(graph, "c1", question="你好")
    assert "你好" in _answer(events)
    assert "chitchat" in _routes(events)
    assert not any(e["type"] == "citations" and e["items"] for e in events)
    assert not any(e["type"] == "error" for e in events)


def test_search_emits_citations_and_factual_route(tmp_path, biz):
    script = [
        _ai_call("search_knowledge", {"query": "转专业条件"}),
        _ai_text("根据[1]，转专业需要在校期间无未通过课程。"),
    ]
    graph, _, _ = make_flow(tmp_path, biz, script)
    events = run_turn(graph, "c2", question="转专业要什么条件")
    assert "factual" in _routes(events)
    cites = [e for e in events if e["type"] == "citations" and e["items"]]
    assert cites, "检索后必须带引用"
    assert cites[-1]["items"][0]["doc_id"] == "0001-transfer"
    assert "无未通过课程" in _answer(events)
    assert not any(e["type"] == "error" for e in events)


def test_write_full_args_interrupts_then_approve_executes(tmp_path, biz):
    script = [
        _ai_call("book_venue", {"venue": "羽毛球馆", "date": _tomorrow(), "slot": "19:00-21:00"}),
        _ai_text("已为你预约成功，记得准时到哦。"),
    ]
    graph, llm, business = make_flow(tmp_path, biz, script)
    cfg = {"configurable": {"thread_id": "c3"}}

    events = run_turn(graph, "c3", question="帮我预约明天晚上的羽毛球馆")
    # 确认摘要先于中断：status + pending_action + answer
    assert any(e["type"] == "pending_action" and e["tool"] == "book_venue" for e in events)
    assert "请确认" in _answer(events)

    snap = graph.get_state(cfg)
    assert snap.next, "写操作必须停在 HITL 确认门"
    payload = find_hitl_payload(snap)
    assert payload is not None and payload["action_requests"][0]["name"] == "book_venue"

    resume_events = run_turn(graph, "c3", resume=hitl_decisions(payload, "确认", llm, business))
    results = [e for e in resume_events if e["type"] == "action_result"]
    assert results and results[0]["success"] and results[0]["tool"] == "book_venue"
    assert "预约成功" in _answer(resume_events)
    assert "transaction" in _routes(resume_events)
    bookings = business.my_bookings("demo-student")
    assert any(b["venue"] == "羽毛球馆" and b["slot"] == "19:00-21:00" for b in bookings)


def test_write_missing_args_slot_gate_collects(tmp_path, biz):
    script = [
        _ai_call("book_venue", {"date": _tomorrow(), "slot": "19:00-21:00"}),  # 缺 venue
        _ai_text("想预约哪个场馆？可选：羽毛球馆、篮球场、研讨间301。"),
    ]
    graph, _, business = make_flow(tmp_path, biz, script)
    events = run_turn(graph, "c4", question="帮我约个明晚七点的场地")
    slots = [e for e in events if e["type"] == "slot_question"]
    assert slots and slots[0]["slot"] == "venue"
    assert "场馆" in _answer(events)
    assert not any(e["type"] == "pending_action" for e in events)  # 参数不齐不进确认
    assert business.my_bookings("demo-student") == []  # 未落库
    snap = graph.get_state({"configurable": {"thread_id": "c4"}})
    assert not snap.next  # 未中断


def test_permission_denied_returns_receipt(tmp_path, biz):
    script = [
        _ai_call("pending_leaves", {}),
        _ai_text("抱歉，学生身份暂时无权查看待审批列表。"),
    ]
    graph, _, _ = make_flow(tmp_path, biz, script)
    events = run_turn(graph, "c5", question="帮我看看有哪些待审批的请假")
    results = [e for e in events if e["type"] == "action_result"]
    assert results and not results[0]["success"] and "无权" in results[0]["message"]
    assert "无权" in _answer(events)


def test_classic_mode_still_routes_via_cascade(tmp_path, biz):
    settings = Settings(llm_api_key="k", embed_api_key="e", data_dir=tmp_path)
    retriever = FakeRetriever([make_hit()])
    business = biz
    llm = FakeAgentLLM()  # 无 key：cascade 退化启发式
    graph = build_graph(settings, retriever, llm, business=business, checkpointer=MemorySaver())
    cfg = {"configurable": {"thread_id": "c6"}}
    inp = new_state("图书馆几点开门", "classic", "c6", "student", "demo-student")
    events = []
    for chunk in graph.stream(inp, cfg, stream_mode="custom", subgraphs=True):
        events.append(chunk[-1] if isinstance(chunk, tuple) else chunk)
    routes = _routes(events)
    assert any(r in ("factual",) for r in routes)
    assert "答" in _answer(events)
    assert not any(e["type"] == "error" for e in events)


def test_guard_block_short_circuits_in_graph(tmp_path, biz):
    """guard 在图内生效：block 时模型零调用，直接吐 GUARD_BLOCK_ANSWER（P28 新话术）。"""
    from gewu.agent.prompts import GUARD_BLOCK_ANSWER

    script = []  # 模型不应被调用（脚本为空，一旦调用会返回空 content 导致断言失败）
    settings_llm = FakeAgentLLM(
        chat_replies=['{"decision":"block","intent":"refusal","reply":""}'], has_key=True
    )
    settings = Settings(llm_api_key="k", embed_api_key="e", data_dir=tmp_path)
    retriever = FakeRetriever([make_hit()])
    business = biz
    settings_llm._script = script
    agent = build_agent(settings, settings_llm, retriever, business, tools_for())
    graph = build_graph(
        settings,
        retriever,
        settings_llm,
        business=business,
        checkpointer=MemorySaver(),
        agent=agent,
    )
    events = run_turn(graph, "g1", question="教我怎么在考试里作弊不被发现")
    assert GUARD_BLOCK_ANSWER in _answer(events)
    assert "refusal" in _routes(events)


def test_guard_meta_answers_directly_in_graph(tmp_path, biz):
    llm = FakeAgentLLM(
        chat_replies=[
            '{"decision":"meta","intent":"chitchat","reply":"嗨！我可以帮你查政策、约场馆。"}'
        ],
        has_key=True,
    )
    settings = Settings(llm_api_key="k", embed_api_key="e", data_dir=tmp_path)
    retriever = FakeRetriever([make_hit()])
    business = biz
    agent = build_agent(settings, llm, retriever, business, tools_for())
    graph = build_graph(
        settings, retriever, llm, business=business, checkpointer=MemorySaver(), agent=agent
    )
    events = run_turn(graph, "g2", question="早安呀同学")
    assert "约场馆" in _answer(events)
    assert "chitchat" in _routes(events)


def test_multiturn_slot_collection_not_eaten_by_guard(tmp_path, biz):
    """回归（agent-first 轨 tx-002 失败根因）：短回复轮不能被 guard 当寒暄吃掉。"""
    script = [
        _ai_call("book_venue", {"date": _tomorrow(), "slot": "14:00-16:00"}),  # 缺 venue
        _ai_text("想预约哪个场馆？"),
        _ai_call("book_venue", {"venue": "研讨间301", "date": _tomorrow(), "slot": "14:00-16:00"}),
        _ai_text("预约成功，明天见。"),
    ]
    graph, llm, business = make_flow(tmp_path, biz, script)
    cfg = {"configurable": {"thread_id": "m1"}}
    run_turn(graph, "m1", question="帮我约明天下午两点的研讨间")
    run_turn(graph, "m1", question="研讨间301")  # 短回复：guard 必须放行
    snap = graph.get_state(cfg)
    assert snap.next, "槽位补齐后必须停在确认门"
    payload = find_hitl_payload(snap)
    resume_events = run_turn(graph, "m1", resume=hitl_decisions(payload, "确认", llm, business))
    results = [e for e in resume_events if e["type"] == "action_result"]
    assert results and results[0]["success"]
    assert any(b["venue"] == "研讨间301" for b in business.my_bookings("demo-student"))


# ---------- P26：联网检索（条件注册 + 全链事件） ----------


def test_web_search_flow_emits_citations_and_factual_route(tmp_path, biz):
    settings = Settings(llm_api_key="k", embed_api_key="e", data_dir=tmp_path, iqs_api_key="iqs")
    retriever = FakeRetriever([make_hit()])
    llm = FakeAgentLLM(
        script=[
            _ai_call("web_search", {"query": "2026下半年四六级报名时间"}),
            _ai_text("根据 [1]，2026 下半年四六级笔试报名时间为 9 月 18 日至 28 日。"),
        ]
    )
    web_hits = [
        {
            "title": "四六级报名通知",
            "url": "https://example.com/cet",
            "snippet": "报名时间 9 月 18 日—28 日",
            "site": "示例教务处",
            "date": "2026-09-11",
        }
    ]
    agent = build_agent(
        settings,
        llm,
        retriever,
        biz,
        tools_for(),
        web=lambda q, k=5, freshness="": (web_hits, "ok"),
    )
    graph = build_graph(
        settings, retriever, llm, business=biz, checkpointer=MemorySaver(), agent=agent
    )
    events = run_turn(graph, "w1", question="四六级报名什么时候截止")
    assert any(
        e["type"] == "status" and "联网检索" in e.get("text", "") for e in events
    )  # 工具内 status 事件冒泡（subgraphs=True）
    cites = [e for e in events if e["type"] == "citations" and e["items"]]
    assert cites and cites[-1]["items"][0]["doc_id"] == "https://example.com/cet"
    assert cites[-1]["items"][0]["source"] == "联网检索"  # 来源 Dialog 单组收拢
    assert "factual" in _routes(events)
    assert not any(e["type"] == "error" for e in events)


def test_web_search_tool_conditional_registration(biz):
    from gewu.agent.agenttools import build_agent_tools

    off = build_agent_tools(FakeAgentLLM(), biz, tools_for(), FakeRetriever())
    assert all(t.name != "web_search" for t in off)  # key 空=不注册（能力注入）
    on = build_agent_tools(
        FakeAgentLLM(),
        biz,
        tools_for(),
        FakeRetriever(),
        web=lambda q, k=5, freshness="": ([], "empty"),
    )
    assert any(t.name == "web_search" for t in on)


def test_offcampus_question_answers_directly_without_refusal(tmp_path, biz):
    """P26 通用化：校外通用问题零工具直答（旧第 9 条「引导回校园」废止）。"""
    graph, _, _ = make_flow(
        tmp_path,
        biz,
        [
            _ai_text(
                "勾股定理：直角三角形两直角边平方和等于斜边平方（a²+b²=c²）。这是通用数学常识，非校园官方口径。"
            )
        ],
    )
    events = run_turn(graph, "g1", question="勾股定理是什么")
    assert "勾股定理" in _answer(events)
    assert not any(e["type"] == "error" for e in events)


def test_citations_scoped_per_turn_no_cross_pollution(tmp_path, biz):
    """P26：citations 按轮清零——上轮联网来源不得漏进本轮事件（真跑发现的跨轮污染）。"""
    settings = Settings(llm_api_key="k", embed_api_key="e", data_dir=tmp_path, iqs_api_key="iqs")
    retriever = FakeRetriever([make_hit()])
    llm = FakeAgentLLM(
        script=[
            _ai_call("web_search", {"query": "四六级报名时间"}),
            _ai_text("联网答 [1]。"),
            _ai_call("search_knowledge", {"query": "转专业条件"}),
            _ai_text("知识库答 [1]。"),
        ]
    )
    web_hits = [{"title": "t", "url": "https://e.com/a", "snippet": "s", "site": "站", "date": ""}]
    agent = build_agent(
        settings,
        llm,
        retriever,
        biz,
        tools_for(),
        web=lambda q, k=5, freshness="": (web_hits, "ok"),
    )
    graph = build_graph(
        settings, retriever, llm, business=biz, checkpointer=MemorySaver(), agent=agent
    )
    run_turn(graph, "x1", question="四六级报名时间")
    events2 = run_turn(graph, "x1", question="转专业条件")  # 同 thread 第二轮
    cites2 = [e for e in events2 if e["type"] == "citations" and e["items"]]
    assert cites2, "第二轮应有引用"
    assert all(c["source"] != "联网检索" for c in cites2[-1]["items"])  # 只剩本轮 KB 来源
