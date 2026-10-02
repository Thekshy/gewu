"""P30 agent 主循环答案流式：中间轮撤回 / 最终轮多帧直出 / 防重 / 开关回退。

口径：answer_reset 后拼接 = 前端最终显示文本（reset 清零语义）；全文帧
（text == 最终答案）不得出现——agent_done 防重不得全文重发。
"""

from __future__ import annotations

import json
from datetime import date, timedelta

from langchain_core.messages import AIMessageChunk
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from gewu.agent.agent import build_agent
from gewu.agent.graph import build_graph
from gewu.agent.resume import find_hitl_payload, hitl_decisions
from gewu.agent.state import new_state
from gewu.agent.tools import tools_for
from gewu.config import Settings
from tests.agent_fakes import FakeRetriever, FakeStreamAgentLLM, make_hit

FINAL = "根据[1]，转专业需要在校期间无未通过课程，且绩点排名前30%。"


def _text_round(text: str, n: int = 3, *, usage: int = 0) -> list[AIMessageChunk]:
    """文本轮切成 n 个 chunk（模拟 token 流）；usage>0 时末 chunk 带用量。"""
    step = max(1, len(text) // n)
    parts = [text[i : i + step] for i in range(0, len(text), step)]
    chunks = [AIMessageChunk(content=p) for p in parts]
    if usage:
        chunks[-1] = AIMessageChunk(
            content=chunks[-1].content,
            usage_metadata={"input_tokens": usage, "output_tokens": 1, "total_tokens": usage + 1},
        )
    return chunks


def _call_round(text: str, name: str, args: dict, cid: str = "call_1") -> list[AIMessageChunk]:
    """中间轮：过渡文本 chunk + tool_call chunk（聚合出 tool_calls 触发撤回）。"""
    chunks = [AIMessageChunk(content=text)]
    chunks.append(
        AIMessageChunk(
            content="",
            tool_call_chunks=[
                {
                    "name": name,
                    "args": json.dumps(args, ensure_ascii=False),
                    "id": cid,
                    "type": "tool_call_chunk",
                }
            ],
        )
    )
    return chunks


def make_stream_flow(tmp_path, biz, rounds, *, stream_answer: bool = True):
    settings = Settings(
        llm_api_key="k", embed_api_key="e", data_dir=tmp_path, stream_answer=stream_answer
    )
    retriever = FakeRetriever([make_hit()])
    llm = FakeStreamAgentLLM(rounds=rounds)
    agent = build_agent(settings, llm, retriever, biz, tools_for())
    graph = build_graph(
        settings, retriever, llm, business=biz, checkpointer=MemorySaver(), agent=agent
    )
    return graph, llm


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


def _frames(events: list[dict]) -> list[str]:
    return [e["text"] for e in events if e["type"] == "answer_delta"]


def _display(events: list[dict]) -> str:
    """前端语义：answer_delta 追加、answer_reset 清零后的最终显示文本。"""
    out = ""
    for e in events:
        if e["type"] == "answer_delta":
            out += e["text"]
        elif e["type"] == "answer_reset":
            out = ""
    return out


def test_stream_answer_multi_delta_reset_and_no_resend(tmp_path, biz):
    """中间轮过渡文本撤回 + 最终轮逐 chunk 直出 + agent_done 不全文重发。"""
    rounds = [
        _call_round("我去查一下转专业的条件。", "search_knowledge", {"query": "转专业条件"}),
        _text_round(FINAL, usage=20),
    ]
    graph, llm = make_stream_flow(tmp_path, biz, rounds)
    events = run_turn(graph, "s1", question="转专业要什么条件")

    frames = _frames(events)
    assert len(frames) >= 4, "最终轮应逐 chunk 多帧"
    assert any(e["type"] == "answer_reset" for e in events), "中间轮必须撤回"
    assert _display(events) == FINAL, "撤回后拼接 = 最终答案"
    assert FINAL not in frames, "不得出现全文帧（agent_done 防重）"
    assert any(e["type"] == "status" and "理解" in e.get("text", "") for e in events)  # P30 加餐
    assert any(e["type"] == "citations" and e["items"] for e in events)
    assert 21 in llm.recorded, "聚合消息的 usage_metadata 必须存活（记账口径不变）"


def test_stream_answer_off_single_frame_fulltext(tmp_path, biz):
    """STREAM_ANSWER=0 回退：单帧全文（改前行为），无 reset。"""
    rounds = [
        _call_round("我去查一下。", "search_knowledge", {"query": "转专业条件"}),
        _text_round(FINAL),
    ]
    graph, _ = make_stream_flow(tmp_path, biz, rounds, stream_answer=False)
    events = run_turn(graph, "s2", question="转专业要什么条件")

    frames = _frames(events)
    assert frames == [FINAL], "关闭流式 = 单帧全文"
    assert not any(e["type"] == "answer_reset" for e in events)
    assert _display(events) == FINAL


def test_write_tool_round_reset_then_confirm_summary(tmp_path, biz):
    """写工具轮：过渡文本撤回后，PendingAction 确认摘要正常显示。"""
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    rounds = [
        _call_round(
            "好的，我来帮你预约。",
            "book_venue",
            {"venue": "羽毛球馆", "date": tomorrow, "slot": "19:00-21:00"},
        ),
        _text_round("已为你预约成功，记得准时到哦。"),  # resume 续轮（脚本预置，跨轮线性消费）
    ]
    graph, llm = make_stream_flow(tmp_path, biz, rounds)
    cfg = {"configurable": {"thread_id": "s3"}}
    events = run_turn(graph, "s3", question="帮我预约明天晚上的羽毛球馆")

    assert any(e["type"] == "answer_reset" for e in events), "写工具轮过渡文本必须撤回"
    assert any(e["type"] == "pending_action" and e["tool"] == "book_venue" for e in events)
    assert "请确认" in _display(events), "撤回后确认摘要成为显示文本"
    snap = graph.get_state(cfg)
    assert snap.next, "写操作必须停在 HITL 确认门"

    payload = find_hitl_payload(snap)
    resume_events = run_turn(graph, "s3", resume=hitl_decisions(payload, "确认", llm, biz))
    results = [e for e in resume_events if e["type"] == "action_result"]
    assert results and results[0]["success"] and results[0]["tool"] == "book_venue"
    assert _display(resume_events) == "已为你预约成功，记得准时到哦。"


def test_multiturn_no_stale_streamed_flag(tmp_path, biz):
    """跨轮残留防御：上轮流式标志不得影响下轮（agent_in 清零）。"""
    rounds = [_text_round("第一轮答完了。"), _text_round("第二轮也答完了。")]
    graph, _ = make_stream_flow(tmp_path, biz, rounds)
    run_turn(graph, "s4", question="第一问")
    events2 = run_turn(graph, "s4", question="第二问")
    assert _display(events2) == "第二轮也答完了。"
    assert "第二轮也答完了。" not in _frames(events2)  # 无全文重发帧
