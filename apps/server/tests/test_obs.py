"""obs 支撑域单测（P27）：Tracer 缓冲/异常/截断/软降级 + TracerStore 落库往返。

PG 用例走 conftest 的 pg_dsn（gewu_test 库）；不可达时 Skip（门禁有 pg-up 前置）。
"""

from __future__ import annotations

import json

import pytest

from gewu.obs import Tracer, TracerStore, clip, current_tracer, make_trace_store, set_current_tracer


def _meta() -> dict:
    return {
        "session_id": "s1",
        "user": "u@t.dev",
        "role": "student",
        "mode": "auto",
        "question": "测试问题",
    }


def test_tracer_noop_when_store_none():
    t = Tracer(None, _meta())
    with t.span("tool", "web_search", input={"query": "q"}) as sp:
        sp.output = {"content": "ok"}
    t.finish(
        route="factual",
        route_layer="effective",
        reason="completed",
        latency_ms=100,
        steps=1,
        answer_head="答",
        error=None,
    )
    assert t.finished and len(t.spans) == 1  # 缓冲仍可用（测试只读口），只是不落库


def test_tracer_span_records_latency_and_error():
    t = Tracer(None, _meta())
    with t.span("llm", "glm-5.3", input={"msgs": 2}):
        pass
    with pytest.raises(ValueError, match="炸了"):
        with t.span("tool", "book_venue", input={"venue": "羽毛球馆"}):
            raise ValueError("炸了")
    a, b = t.spans
    assert (a.kind, a.name, a.status, a.seq) == ("llm", "glm-5.3", "ok", 1)
    assert (b.status, b.seq) == ("error", 2)
    assert b.output == {"error": "炸了"}
    assert b.latency_ms >= 0 and a.latency_ms >= 0


def test_clip_truncates_nested_strings():
    v = {"content": "x" * 1000, "args": ["y" * 1000]}
    out = clip(v, limit=10)
    assert all(len(s) == 11 for s in [out["content"], out["args"][0]])  # 10 + 省略号
    assert clip(5) == 5 and clip(None) is None


def test_contextvar_roundtrip():
    t = Tracer(None, _meta())
    assert current_tracer() is None
    set_current_tracer(t)
    try:
        assert current_tracer() is t
    finally:
        set_current_tracer(None)
    assert current_tracer() is None


def test_tracer_store_roundtrip(pg_dsn):
    store = TracerStore(pg_dsn)
    store.wipe()
    t = Tracer(store, _meta())
    long_content = "[1] 结果…" * 1000  # 8000 字符，超 OUTPUT_LIMIT 触发落库截断
    with t.span("tool", "web_search", input={"query": "昨天 tyloo 比赛结果", "k": 5}) as sp:
        sp.output = {"content": long_content}
    with t.span("llm", "glm-5.3-flash", input={"msgs": 3, "small": True}) as sp2:
        sp2.tokens = 1234
        sp2.output = {"chars": 88}
    t.finish(
        route="factual",
        route_layer="effective",
        reason="completed",
        latency_ms=10457,
        steps=1,
        answer_head="答头",
        error=None,
    )

    import psycopg

    with psycopg.connect(pg_dsn, autocommit=True) as conn:
        tr = conn.execute(
            "SELECT session_id, route, reason, latency_ms, error FROM agent_trace"
            " WHERE session_id = %s",
            ("s1",),
        ).fetchone()
        spans = conn.execute(
            "SELECT seq, kind, name, status, tokens, input, output FROM agent_span"
            " WHERE trace_id = (SELECT id FROM agent_trace WHERE session_id = 's1')"
            " ORDER BY seq"
        ).fetchall()
    assert tr == ("s1", "factual", "completed", 10457, None)
    assert len(spans) == 2
    s1, s2 = spans
    assert (s1[1], s1[2], s1[3]) == ("tool", "web_search", "ok")
    assert s1[5] == {"query": "昨天 tyloo 比赛结果", "k": 5}  # psycopg 自动解 JSONB；input 原样（盲区根治点）
    assert len(s1[6]["content"]) <= 4096 + 1  # output 落库截断生效
    assert (s2[1], s2[4]) == ("llm", 1234)
    store.wipe()


def test_make_trace_store_soft_degrades_on_bad_dsn(capsys):
    assert make_trace_store("postgres://nope@127.0.0.1:1/none") is None
    assert "TracerStore 不可用" in capsys.readouterr().out
