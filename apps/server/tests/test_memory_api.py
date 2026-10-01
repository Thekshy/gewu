"""P22 记忆端点测试：fact 列表/upsert/删除 + 未登录 401 + 他人不可见。"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from gewu.api.app import create_app
from gewu.auth.store import AuthStore
from gewu.business.db import Business
from gewu.config import Settings
from gewu.memory import MemoryStore
from gewu.session.store import SessionStore
from tests.conftest import make_logged_client
from tests.test_api import FakeRetriever, FakeStore
from tests.test_chat_api import FakeChatLLM


def make_client(
    tmp_path: Path,
    biz: Business,
    mem: MemoryStore,
    auth: AuthStore,
    sess: SessionStore,
    logged: bool = True,
) -> TestClient:
    (tmp_path / "usage.json").write_text("{}", encoding="utf-8")
    settings = Settings(llm_api_key="lk", embed_api_key="ek", data_dir=tmp_path)
    from gewu.rag.store import DocInfo, Stats

    app = create_app(
        settings,
        store=FakeStore(Stats(1, 1, False), [DocInfo("d1", "t", "s", "u", 1)]),
        business=biz,
        memory=mem,
        auth=auth,
        sessions=sess,
        retriever=FakeRetriever([]),
        llm=FakeChatLLM(["ok"]),
    )
    if not logged:
        return TestClient(app)
    return make_logged_client(app, auth)


def test_memory_facts_require_login(tmp_path, biz, mem, auth, sess):
    c = make_client(tmp_path, biz, mem, auth, sess, logged=False)
    assert c.get("/api/memory/facts").status_code == 401
    r = c.post("/api/memory/facts", json={"kind": "profile", "key": "k", "value": "v"})
    assert r.status_code == 401
    assert c.delete("/api/memory/facts?kind=profile&key=k").status_code == 401


def test_fact_crud_roundtrip(tmp_path, biz, mem, auth, sess):
    c = make_client(tmp_path, biz, mem, auth, sess)
    assert c.get("/api/memory/facts").json() == []

    # 新增（upsert 语义：同 kind/key 覆盖）
    for body in [
        {"kind": "profile", "key": "major", "value": "计算机科学"},
        {"kind": "preference", "key": "sport", "value": "羽毛球"},
        {"kind": "constraint", "key": "budget", "value": "免费场馆优先"},
    ]:
        assert c.post("/api/memory/facts", json=body).status_code == 200
    assert (
        c.post(
            "/api/memory/facts", json={"kind": "profile", "key": "major", "value": "软件工程"}
        ).status_code
        == 200
    )

    facts = c.get("/api/memory/facts").json()
    assert [(f["kind"], f["key"], f["value"]) for f in facts] == [
        ("constraint", "budget", "免费场馆优先"),
        ("preference", "sport", "羽毛球"),
        ("profile", "major", "软件工程"),  # 覆盖后只留最新
    ]

    assert c.delete("/api/memory/facts?kind=profile&key=major").status_code == 200
    assert c.delete("/api/memory/facts?kind=profile&key=major").status_code == 404
    assert c.delete("/api/memory/facts?kind=bogus&key=x").status_code == 422
    assert {f["key"] for f in c.get("/api/memory/facts").json()} == {"sport", "budget"}


def test_fact_validation(tmp_path, biz, mem, auth, sess):
    c = make_client(tmp_path, biz, mem, auth, sess)
    cases = [
        {"kind": "bogus", "key": "k", "value": "v"},
        {"kind": "profile", "key": "", "value": "v"},
        {"kind": "profile", "key": "k" * 61, "value": "v"},
        {"kind": "profile", "key": "k", "value": ""},
        {"kind": "profile", "key": "k", "value": "v" * 501},
        {"kind": "profile", "key": 1, "value": "v"},
    ]
    for body in cases:
        assert c.post("/api/memory/facts", json=body).status_code == 422, body


def test_fact_isolated_between_users(tmp_path, biz, mem, auth, sess):
    c1 = make_client(tmp_path, biz, mem, auth, sess)
    assert (
        c1.post(
            "/api/memory/facts", json={"kind": "profile", "key": "major", "value": "物理"}
        ).status_code
        == 200
    )

    c2 = make_logged_client(c1.app, auth, email="u2@example.com")
    assert c2.get("/api/memory/facts").json() == [], "他人 fact 不可见"
    # 复合主键含 user_id，越权删不中
    assert c2.delete("/api/memory/facts?kind=profile&key=major").status_code == 404
    assert c1.get("/api/memory/facts").json()[0]["value"] == "物理"
