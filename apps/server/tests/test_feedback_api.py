"""POST /api/feedback 契约测试（P25-2）：校验序列、归属 404、upsert 覆盖语义。"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from gewu.api.app import create_app
from gewu.auth.store import AuthStore
from gewu.business.db import Business
from gewu.config import Settings
from gewu.memory import MemoryStore
from gewu.rag.store import DocInfo, Stats
from gewu.session.store import FeedbackStore, SessionStore
from tests.conftest import make_logged_client
from tests.test_api import FakeRetriever, FakeStore
from tests.test_chat_api import FakeChatLLM


def make_client(
    tmp_path: Path,
    biz: Business,
    mem: MemoryStore,
    auth: AuthStore,
    sess: SessionStore,
    fb: FeedbackStore,
    logged: bool = True,
) -> TestClient:
    (tmp_path / "usage.json").write_text("{}", encoding="utf-8")
    settings = Settings(llm_api_key="lk", embed_api_key="ek", data_dir=tmp_path)
    app = create_app(
        settings,
        store=FakeStore(Stats(1, 1, False), [DocInfo("d1", "t", "s", "u", 1)]),
        business=biz,
        memory=mem,
        auth=auth,
        sessions=sess,
        feedback=fb,
        retriever=FakeRetriever([]),
        llm=FakeChatLLM(["ok"]),
    )
    if not logged:
        return TestClient(app)
    client = make_logged_client(app, auth)
    sess.create("u1@example.com", "chat", session_id="s1")
    return client


def test_feedback_requires_login(tmp_path: Path, biz, mem, auth, sess, fb):
    c = make_client(tmp_path, biz, mem, auth, sess, fb, logged=False)
    r = c.post(
        "/api/feedback",
        json={"session_id": "s1", "question": "q", "rating": "good"},
    )
    assert r.status_code == 401


def test_feedback_validation(tmp_path: Path, biz, mem, auth, sess, fb):
    c = make_client(tmp_path, biz, mem, auth, sess, fb)
    cases = [
        ({"question": "q", "rating": "good"}, "session_id 不能为空"),
        ({"session_id": "s1", "question": "", "rating": "good"}, "question 不能为空"),
        (
            {"session_id": "s1", "question": "字" * 501, "rating": "good"},
            "question 过长（上限 500 字）",
        ),
        ({"session_id": "s1", "question": "q", "rating": "mid"}, "rating 必须为 good/bad"),
    ]
    for body, detail in cases:
        r = c.post("/api/feedback", json=body)
        assert r.status_code == 422, body
        assert r.json()["detail"] == detail, body


def test_feedback_unknown_or_foreign_session_404(tmp_path: Path, biz, mem, auth, sess, fb):
    c = make_client(tmp_path, biz, mem, auth, sess, fb)
    r = c.post(
        "/api/feedback",
        json={"session_id": "never", "question": "q", "rating": "good"},
    )
    assert r.status_code == 404
    assert r.json()["detail"] == "会话不存在"

    # 他人会话同样 404（不泄露存在性）
    other = make_logged_client(c.app, auth, email="u2@example.com")
    sid = other.post("/api/sessions", json={}).json()["session_id"]
    r = c.post("/api/feedback", json={"session_id": sid, "question": "q", "rating": "good"})
    assert r.status_code == 404


def test_feedback_upsert_overwrites_rating(tmp_path: Path, biz, mem, auth, sess, fb):
    """good→bad 覆盖（唯一键 "user"+session+question），不产生双行。"""
    c = make_client(tmp_path, biz, mem, auth, sess, fb)
    body = {"session_id": "s1", "question": "图书馆几点开门", "rating": "good"}
    assert c.post("/api/feedback", json=body).status_code == 204
    assert fb.get("u1@example.com", "s1", "图书馆几点开门") == "good"
    assert fb.count() == 1

    body["rating"] = "bad"
    assert c.post("/api/feedback", json=body).status_code == 204
    assert fb.get("u1@example.com", "s1", "图书馆几点开门") == "bad"
    assert fb.count() == 1  # 覆盖而非新增

    # 不同问题 = 不同轮 = 独立行
    assert (
        c.post(
            "/api/feedback",
            json={"session_id": "s1", "question": "体育馆怎么预约", "rating": "good"},
        ).status_code
        == 204
    )
    assert fb.count() == 2
