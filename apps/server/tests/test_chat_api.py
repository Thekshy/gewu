"""POST /api/chat 契约测试：校验序列、SSE 分帧、事件序、done.reason 单点语义。

P21 起：需登录（cookie）；role 服务端权威（请求体 role 废弃忽略）。
P22 起：session_id 必填且须为已登记属本人的会话（make_client 预建固定 id）。
"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from gewu.agent.state import new_state
from gewu.api.app import create_app
from gewu.auth.store import AuthStore
from gewu.business.db import Business
from gewu.config import Settings
from gewu.memory import MemoryStore
from gewu.rag.store import DocInfo, Stats
from gewu.session.store import SessionStore
from tests.conftest import make_logged_client
from tests.test_api import FakeRetriever, FakeStore


class FakeChatStream:
    def __init__(self, deltas: list[str], finish_reason: str = "stop") -> None:
        self._deltas = deltas
        self.finish_reason = finish_reason

    def __iter__(self):
        return iter(self._deltas)


class FakeChatLLM:
    """编排域 LLM 替身：直答流式返回预置增量。"""

    def __init__(self, deltas: list[str], finish_reason: str = "stop") -> None:
        self._deltas = deltas
        self._finish = finish_reason

    def has_key(self) -> bool:
        return True

    def chat(self, messages, *, small=False, json_mode=False, temperature=0.0, max_tokens=2048):
        return ""

    def chat_stream(self, messages, *, small=False, temperature=0.0, max_tokens=2048):
        return FakeChatStream(self._deltas, self._finish)

    def embed(self, texts):
        return [[0.0] * 2048]

    def agent_model(self, *, small: bool = False, max_tokens: int = 1200):
        from tests.agent_fakes import FakeToolChatModel  # noqa: PLC0415

        return FakeToolChatModel(responses=[])

    def record_usage(self, total_tokens: int) -> None:
        pass


def _hit() -> dict:
    return {
        "chunk_id": 1,
        "doc_id": "doc-x",
        "seq": 0,
        "text": "图书馆周一至周五 7:30—22:30 开放。",
        "title": "图书馆管理办法",
        "source": "图书馆",
        "section_path": "开放时间",
    }


def make_client(
    tmp_path: Path,
    biz: Business,
    mem: MemoryStore,
    auth: AuthStore,
    sess: SessionStore,
    cp=None,
    llm: FakeChatLLM | None = None,
    graph=None,
    logged: bool = True,
) -> TestClient:
    (tmp_path / "usage.json").write_text("{}", encoding="utf-8")
    settings = Settings(llm_api_key="lk", embed_api_key="ek", data_dir=tmp_path)
    kwargs = {} if cp is None else {"checkpointer": cp}
    app = create_app(
        settings,
        store=FakeStore(Stats(1, 1, False), [DocInfo("d1", "t", "s", "u", 1)]),
        business=biz,
        memory=mem,
        auth=auth,
        sessions=sess,
        retriever=FakeRetriever([_hit()]),
        llm=llm or FakeChatLLM(["开放时间", "是 7:30。"]),
        **kwargs,
    )
    if graph is not None:
        app.state.graph = graph
    if not logged:
        return TestClient(app)
    client = make_logged_client(app, auth)
    # 预建固定 id 会话（用例直传；属主=登录用户 u1@example.com）
    for sid in ("s1", "t", "u", "e"):
        sess.create("u1@example.com", "chat", session_id=sid)
    return client


def _parse_sse(text: str) -> list[dict]:
    out = []
    for block in text.split("\n\n"):
        block = block.strip()
        if block.startswith("data: "):
            out.append(json.loads(block[len("data: ") :]))
    return out


def test_chat_requires_login(tmp_path: Path, biz, mem, auth, sess):
    c = make_client(tmp_path, biz, mem, auth, sess, logged=False)
    r = c.post("/api/chat", json={"question": "图书馆几点开门", "mode": "direct"})
    assert r.status_code == 401
    assert r.json()["detail"] == "未登录或会话已过期"


def test_chat_validation(tmp_path: Path, biz, mem, auth, sess):
    c = make_client(tmp_path, biz, mem, auth, sess)
    cases = [
        ({"question": ""}, "问题不能为空"),
        ({"question": "字" * 501}, "问题过长"),
        ({"question": "q", "mode": "bogus"}, "mode 必须为 auto/direct/research/react/classic"),
        ({"question": "q", "session_id": "s" * 65}, "session_id 过长（上限 64 字符）"),
        ({"question": 123}, "请求体不是合法 JSON"),
    ]
    for body, detail in cases:
        r = c.post("/api/chat", json=body)
        assert r.status_code == 422, body
        assert r.json()["detail"] == detail, body


def test_chat_session_id_required_and_registered(tmp_path: Path, biz, mem, auth, sess):
    """P22：default 缺省废弃（未传 422 给指引）；未登记/他人会话统一 404。"""
    c = make_client(tmp_path, biz, mem, auth, sess)
    r = c.post("/api/chat", json={"question": "图书馆几点开门", "mode": "direct"})
    assert r.status_code == 422
    assert r.json()["detail"] == "session_id 不能为空（请先 POST /api/sessions 创建会话）"

    r = c.post(
        "/api/chat",
        json={"question": "q", "mode": "direct", "session_id": "never-registered"},
    )
    assert r.status_code == 404
    assert r.json()["detail"] == "会话不存在"

    # 他人会话同样 404（不泄露存在性）：u2 建会话，u1 引用
    other = make_logged_client(c.app, auth, email="u2@example.com")
    sid = other.post("/api/sessions", json={}).json()["session_id"]
    r = c.post("/api/chat", json={"question": "q", "mode": "direct", "session_id": sid})
    assert r.status_code == 404


def test_chat_role_param_ignored_server_side_authority(tmp_path: Path, biz, mem, auth, sess):
    """role 废弃：自称 counselor 不再改变身份（服务端 users.role 权威）。"""
    c = make_client(tmp_path, biz, mem, auth, sess)  # 注册用户 role=student
    r = c.post(
        "/api/chat",
        json={
            "question": "图书馆几点开门",
            "mode": "direct",
            "session_id": "s1",
            "role": "counselor",
        },
    )
    assert r.status_code == 200  # 字段被忽略，不 422 不越权


def test_chat_sse_headers_and_frame(tmp_path: Path, biz, mem, auth, sess):
    c = make_client(tmp_path, biz, mem, auth, sess)
    r = c.post(
        "/api/chat", json={"question": "图书馆几点开门", "mode": "direct", "session_id": "s1"}
    )
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/event-stream")
    assert r.headers["cache-control"] == "no-cache"
    assert r.headers["x-accel-buffering"] == "no"
    # 分帧格式：每个事件 data: {json}\n\n
    assert "\n\ndata: " in r.text
    events = _parse_sse(r.text)
    types = [e["type"] for e in events]
    # 直答链路事件序：route → answer_delta* → citations → done（恰一个 done 收尾）
    assert types[0] == "route"
    assert "answer_delta" in types
    assert types[-1] == "done"
    assert types.count("done") == 1
    assert types.index("citations") == len(types) - 2


def test_chat_done_reason_completed_and_citations(tmp_path: Path, biz, mem, auth, sess):
    llm = FakeChatLLM(["开放时间", "是 7:30。"])
    c = make_client(tmp_path, biz, mem, auth, sess, llm=llm)
    r = c.post(
        "/api/chat",
        json={"question": "图书馆几点开门", "mode": "direct", "session_id": "t"},
    )
    events = _parse_sse(r.text)
    done = events[-1]
    assert done["type"] == "done"
    assert done["reason"] == "completed"
    assert isinstance(done["latency_ms"], int)
    answer = "".join(e["text"] for e in events if e["type"] == "answer_delta")
    assert answer == "开放时间是 7:30。"
    cites = [e for e in events if e["type"] == "citations"][0]
    assert cites["items"][0]["doc_id"] == "doc-x"
    assert cites["items"][0]["n"] == 1


def test_chat_truncated_marks_max_tokens(tmp_path: Path, biz, mem, auth, sess):
    llm = FakeChatLLM(["很长".replace("长", "文") * 50], finish_reason="length")
    c = make_client(tmp_path, biz, mem, auth, sess, llm=llm)
    r = c.post("/api/chat", json={"question": "讲讲校历", "mode": "direct", "session_id": "t"})
    events = _parse_sse(r.text)
    assert events[-1]["reason"] == "max_tokens"
    assert any(e["type"] == "status" and "长度上限" in e["text"] for e in events)


def test_chat_utf8_raw_not_escaped(tmp_path: Path, biz, mem, auth, sess):
    c = make_client(tmp_path, biz, mem, auth, sess)
    r = c.post(
        "/api/chat", json={"question": "图书馆几点开门", "mode": "direct", "session_id": "u"}
    )
    assert "图书馆" in r.text  # UTF-8 原文（不转义为 \uXXXX）


def test_chat_error_path_emits_error_and_done(tmp_path: Path, biz, mem, auth, sess):
    class BoomGraph:
        def stream(self, *_a, **_k):
            raise RuntimeError("链路炸了")
            yield  # pragma: no cover

    c = make_client(tmp_path, biz, mem, auth, sess, graph=BoomGraph())
    r = c.post("/api/chat", json={"question": "q", "mode": "direct", "session_id": "e"})
    events = _parse_sse(r.text)
    assert events[-2]["type"] == "error"
    assert events[-1]["type"] == "done"
    assert events[-1]["reason"] == "error"


def test_new_state_defaults():
    s = new_state("q", "auto", "sid", "student", "u1@example.com")
    assert s["resolved"] == "q"
    assert s["truncated"] is False
    assert s["answer"] == ""
