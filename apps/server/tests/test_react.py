"""ReAct 子图测试：G5 截断防御 / 指纹去重 / 写操作转确认流 / 最终回答。"""

from __future__ import annotations

from langchain_core.messages import AIMessage

from gewu.agent.react import ReactContext, build_react_subgraph
from gewu.agent.tools import tools_for
from gewu.business.db import Business
from gewu.rag.store import Hit, Scored


class FakeRetriever:
    def __init__(self, hits: list[Hit] | None = None) -> None:
        self.hits = hits or []
        self.calls: list[str] = []
        self.k = 5

    def search(self, query: str, k: int) -> list[Hit]:
        self.calls.append(query)
        return list(self.hits)

    def bm25_search(self, query: str, k: int) -> list[Scored]:
        return []

    def vector_search(self, query_vec, k: int) -> list[Scored]:
        return []

    def chunk_rows(self, ids):
        return {}

    def parent_rows(self, ids):
        return {}

    def doc_meta_map(self, ids):
        return {}

    def has_embeddings(self) -> bool:
        return True


class FakeReactLLM:
    """按序吐出预置响应（dict: content/tool_calls/finish_reason）。"""

    def __init__(self, responses: list[dict]) -> None:
        self._responses = list(responses)
        self.calls = 0

    def has_key(self) -> bool:
        return True

    def chat(self, *a, **k):
        return ""

    def chat_stream(self, *a, **k):
        raise AssertionError("react 不走流式")

    def embed(self, texts):
        return [[0.0] * 2048]

    def chat_with_tools(self, msgs, tools, *, max_tokens=1200, **k):
        self.calls += 1
        r = self._responses.pop(0)
        return AIMessage(
            content=r.get("content", ""),
            tool_calls=r.get("tool_calls", []),
            response_metadata={"finish_reason": r.get("finish_reason", "stop")},
        )


def _hit() -> dict:
    return {
        "chunk_id": 1,
        "doc_id": "d1",
        "seq": 0,
        "text": "条款内容",
        "title": "转专业办法",
        "source": "教务处",
        "section_path": "",
    }


def _ctx(tmp_path, retriever: FakeRetriever) -> ReactContext:
    return ReactContext(
        retriever, Business(tmp_path / "b.db"), tools_for(), "student", "demo-student", []
    )


def _run(sub, ctx, question: str) -> dict:
    return sub.invoke(
        {
            "question": question,
            "role": "student",
            "user": "demo-student",
            "session_id": "s",
            "toolset": [],
            "mem_block": "",
            "turn": 0,
        },
        config={"configurable": {"react_ctx": ctx}},
    )


def _search_call(query: str = "转专业条件") -> dict:
    return {"tool_calls": [{"id": "c1", "name": "search_knowledge", "args": {"query": query}}]}


def test_react_final_answer_without_tools(tmp_path):
    llm = FakeReactLLM([{"content": "答案是 42"}])
    sub = build_react_subgraph(llm)
    result = _run(sub, _ctx(tmp_path, FakeRetriever()), "问题")
    assert result["final"] == "答案是 42"
    assert llm.calls == 1


def test_react_search_then_answer(tmp_path):
    retr = FakeRetriever([_hit()])
    llm = FakeReactLLM(
        [
            _search_call(),
            {"content": "依据检索结果……"},
        ]
    )
    sub = build_react_subgraph(llm)
    result = _run(sub, _ctx(tmp_path, retr), "转专业条件是什么")
    assert result["final"] == "依据检索结果……"
    assert len(retr.calls) == 1  # 检索执行了一次
    assert result["citations"] and result["citations"][0]["doc_id"] == "d1"


def test_react_g5_truncation_guard_blocks_tool_exec(tmp_path):
    """G5：finish_reason=length 且带 tool_calls → 不执行工具，回填错误 observation 重发。"""
    retr = FakeRetriever([_hit()])
    llm = FakeReactLLM(
        [
            {
                "tool_calls": [
                    {"id": "c1", "name": "search_knowledge", "args": {"query": "转专业"}}
                ],
                "finish_reason": "length",
            },
            {
                "tool_calls": [
                    {"id": "c2", "name": "search_knowledge", "args": {"query": "转专业"}}
                ]
            },  # 模型重发完整调用
            {"content": "最终答案"},
        ]
    )
    sub = build_react_subgraph(llm)
    result = _run(sub, _ctx(tmp_path, retr), "转专业条件")
    assert result["final"] == "最终答案"
    assert len(retr.calls) == 1  # 截断那次未执行，只有重发执行了 1 次


def test_react_fingerprint_rejects_third_repeat(tmp_path):
    retr = FakeRetriever([_hit()])
    llm = FakeReactLLM(
        [
            _search_call(),
            _search_call(),
            _search_call(),  # 第 3 次同指纹 → 拒绝执行
            {"content": "换思路后的答案"},
        ]
    )
    sub = build_react_subgraph(llm)
    result = _run(sub, _ctx(tmp_path, retr), "q")
    assert len(retr.calls) == 2  # 前两次执行，第三次被拒
    assert result["final"] == "换思路后的答案"


def test_react_write_tool_with_full_args_goes_confirm(tmp_path):
    b = Business(tmp_path / "b.db")
    ctx = ReactContext(FakeRetriever(), b, tools_for(), "student", "demo-student", [])
    llm = FakeReactLLM(
        [
            {
                "tool_calls": [
                    {
                        "id": "c1",
                        "name": "book_venue",
                        "args": {
                            "venue": "羽毛球馆",
                            "date": "2099-01-01",
                            "slot": "19:00-21:00",
                            "purpose": "训练",
                        },
                    }
                ]
            },
        ]
    )
    sub = build_react_subgraph(llm)
    result = _run(sub, ctx, "帮我预约羽毛球馆")
    assert result["stop"] == "confirm"
    assert result["tx_tool"] == "book_venue"
    assert result["tx_slots"]["venue"] == "venue-badminton"  # 槽位经归一（场馆名→ID）
    assert b.my_bookings("demo-student") == []  # 写操作未直接执行


def test_react_write_tool_missing_args_prompts_collection(tmp_path):
    llm = FakeReactLLM(
        [
            {"tool_calls": [{"id": "c1", "name": "book_venue", "args": {"venue": "羽毛球馆"}}]},
            {"content": "请问您想预约哪一天、哪个时段呢？"},
        ]
    )
    sub = build_react_subgraph(llm)
    result = _run(sub, _ctx(tmp_path, FakeRetriever()), "帮我预约羽毛球馆")
    assert not result.get("stop")
    assert "预约哪一天" in result["final"]  # agent 收到缺参 observation 后向用户提问


def test_react_tool_error_fills_observation(tmp_path):
    llm = FakeReactLLM(
        [
            {"tool_calls": [{"id": "c1", "name": "parse_date", "args": {"text": "礼拜八"}}]},
            {"content": "无法识别该日期"},
        ]
    )
    sub = build_react_subgraph(llm)
    result = _run(sub, _ctx(tmp_path, FakeRetriever()), "礼拜八是哪天")
    assert result["final"] == "无法识别该日期"


def test_react_permission_denied_via_tool_layer(tmp_path):
    """辅导员专属工具对学生不可用（越权在工具层被拦截，错误回填 observation）。"""
    llm = FakeReactLLM(
        [
            {
                "tool_calls": [
                    {"id": "c1", "name": "approve_leave", "args": {"ticket_id": "LV-0001"}}
                ]
            },
            {"content": "没有权限"},
        ]
    )
    sub = build_react_subgraph(llm)
    result = _run(sub, _ctx(tmp_path, FakeRetriever()), "帮我批准请假")
    assert result["final"] == "没有权限"
