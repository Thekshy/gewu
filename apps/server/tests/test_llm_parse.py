"""llm 层解析测试：finish_reason / usage 三元组（P10 契约 Python 侧），无网络。"""

from __future__ import annotations

from langchain_core.messages import AIMessage

from gewu.llm.chat import parse_finish_reason, parse_usage


def test_parse_finish_reason_present():
    msg = AIMessage(content="ok", response_metadata={"finish_reason": "stop"})
    assert parse_finish_reason(msg) == "stop"


def test_parse_finish_reason_length_marks_truncation():
    msg = AIMessage(content="半截", response_metadata={"finish_reason": "length"})
    assert parse_finish_reason(msg) == "length"


def test_parse_finish_reason_missing_returns_empty():
    assert parse_finish_reason(AIMessage(content="x")) == ""


def test_parse_usage_triplet():
    msg = AIMessage(
        content="x",
        usage_metadata={"input_tokens": 12, "output_tokens": 34, "total_tokens": 46},
    )
    u = parse_usage(msg)
    assert (u.prompt, u.completion, u.total) == (12, 34, 46)


def test_parse_usage_missing_all_zero():
    u = parse_usage(AIMessage(content="x"))
    assert (u.prompt, u.completion, u.total) == (0, 0, 0)
