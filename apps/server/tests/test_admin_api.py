"""P23 管理后台测试：八端点/403 守卫/自我保护/停用即踢/per-user 限额闸/连带删除。"""

from __future__ import annotations

from pathlib import Path

import psycopg
from fastapi.testclient import TestClient

from gewu.api.app import create_app
from gewu.auth.store import AuthStore
from gewu.business.db import Business
from gewu.config import Settings
from gewu.memory import MemoryStore
from gewu.rag.store import DocInfo, Stats
from gewu.session.store import SessionStore
from gewu.usage import UsageStore
from tests.conftest import make_logged_client
from tests.test_api import FakeRetriever, FakeStore
from tests.test_chat_api import FakeChatLLM

_CP_TABLES = ("checkpoints", "checkpoint_blobs", "checkpoint_writes")


def make_admin_client(
    tmp_path: Path,
    biz: Business,
    mem: MemoryStore,
    auth: AuthStore,
    sess: SessionStore,
    usage: UsageStore,
    cp,
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
        usage=usage,
        checkpointer=cp,
        retriever=FakeRetriever([]),
        llm=FakeChatLLM(["好"]),
    )
    return make_logged_client(app, auth, email="boss@example.com", admin=True)


# ---------- 403 守卫（student 全端点） ----------


def test_admin_endpoints_require_admin(tmp_path, biz, mem, auth, sess, usage, cp):
    c = make_admin_client(tmp_path, biz, mem, auth, sess, usage, cp)
    student = make_logged_client(c.app, auth, email="s@example.com")
    for method, path in [
        ("get", "/api/admin/stats"),
        ("get", "/api/admin/users"),
        ("patch", "/api/admin/users/x@y.com"),
        ("get", "/api/admin/invites"),
        ("post", "/api/admin/invites"),
        ("get", "/api/admin/sessions"),
        ("delete", "/api/admin/sessions/x"),
        ("get", "/api/admin/usage"),
    ]:
        if method in ("post", "patch"):
            r = getattr(student, method)(path, json={})
        else:
            r = getattr(student, method)(path)
        assert r.status_code == 403, (method, path)


# ---------- stats / users ----------


def test_admin_stats_shape(tmp_path, biz, mem, auth, sess, usage, cp):
    c = make_admin_client(tmp_path, biz, mem, auth, sess, usage, cp)
    usage.add("boss@example.com", 120)
    sess.create("boss@example.com", "chat")
    body = c.get("/api/admin/stats").json()
    assert body["users"] == 1
    assert body["invites"] >= 1  # make_logged_client 注册时发过码
    assert body["chat_sessions"] == 1
    assert body["today_tokens"] == 120
    assert body["budget"]["limit"] == 2_000_000


def test_admin_users_with_today_tokens(tmp_path, biz, mem, auth, sess, usage, cp):
    c = make_admin_client(tmp_path, biz, mem, auth, sess, usage, cp)
    make_logged_client(c.app, auth, email="u2@example.com")
    usage.add("u2@example.com", 88)
    users = {u["email"]: u for u in c.get("/api/admin/users").json()}
    assert set(users) == {"boss@example.com", "u2@example.com"}
    assert users["u2@example.com"]["today_tokens"] == 88
    assert users["u2@example.com"]["daily_token_limit"] is None
    assert users["boss@example.com"]["role"] == "admin"


# ---------- PATCH users：改写 / 校验 / 自我保护 / 停用即踢 ----------


def test_admin_patch_user_role_limit_and_clear(tmp_path, biz, mem, auth, sess, usage, cp):
    c = make_admin_client(tmp_path, biz, mem, auth, sess, usage, cp)
    make_logged_client(c.app, auth, email="u2@example.com")

    r = c.patch("/api/admin/users/u2@example.com", json={"daily_token_limit": 500})
    assert r.status_code == 200 and r.json()["daily_token_limit"] == 500

    r = c.patch("/api/admin/users/u2@example.com", json={"role": "counselor"})
    assert r.json()["role"] == "counselor"

    # clear_limit：显式传 null 恢复全局缺省
    r = c.patch("/api/admin/users/u2@example.com", json={"daily_token_limit": None})
    assert r.json()["daily_token_limit"] is None

    r = c.patch("/api/admin/users/ghost@x.com", json={"role": "admin"})
    assert r.status_code == 404
    for bad in (
        {"role": "bogus"},
        {"status": "bogus"},
        {"daily_token_limit": 0},
        {"daily_token_limit": "x"},
        {"daily_token_limit": True},
    ):
        r = c.patch("/api/admin/users/u2@example.com", json=bad)
        assert r.status_code == 422, bad


def test_admin_cannot_demote_self(tmp_path, biz, mem, auth, sess, usage, cp):
    c = make_admin_client(tmp_path, biz, mem, auth, sess, usage, cp)
    r = c.patch("/api/admin/users/boss@example.com", json={"status": "disabled"})
    assert r.status_code == 422
    assert r.json()["detail"] == "不能修改自己的角色或状态"
    r = c.patch("/api/admin/users/boss@example.com", json={"role": "student"})
    assert r.status_code == 422
    # 改自己的限额允许
    r = c.patch("/api/admin/users/boss@example.com", json={"daily_token_limit": 999})
    assert r.status_code == 200


def test_admin_disable_user_kicks_sessions(tmp_path, biz, mem, auth, sess, usage, cp):
    c = make_admin_client(tmp_path, biz, mem, auth, sess, usage, cp)
    u2 = make_logged_client(c.app, auth, email="u2@example.com")
    assert u2.get("/api/auth/me").status_code == 200
    r = c.patch("/api/admin/users/u2@example.com", json={"status": "disabled"})
    assert r.status_code == 200
    assert u2.get("/api/auth/me").status_code == 401  # cookie 立即失效（status 过滤）
    # 恢复后仍需重新登录（旧 session 已无效）
    assert c.patch("/api/admin/users/u2@example.com", json={"status": "active"}).status_code == 200
    assert u2.get("/api/auth/me").status_code == 401


# ---------- per-user 限额闸 ----------


def test_chat_per_user_budget_429(tmp_path, biz, mem, auth, sess, usage, cp, monkeypatch):
    monkeypatch.setenv("MEMORY_CONSOLIDATE", "off")
    c = make_admin_client(tmp_path, biz, mem, auth, sess, usage, cp)
    u2 = make_logged_client(c.app, auth, email="u2@example.com")
    sid = u2.post("/api/sessions", json={}).json()["session_id"]

    # 未设个性化限额：全局缺省 200_000，先抬高到门口再验 429 文案
    usage.add("u2@example.com", 200_000)
    r = u2.post(
        "/api/chat", json={"question": "图书馆几点开门", "mode": "direct", "session_id": sid}
    )
    assert r.status_code == 429
    assert r.json()["detail"] == "今日个人 token 预算已用尽（上限 200000），请明天再试"

    # 个性化限额 50：用量 60 即超
    usage.wipe()
    r = c.patch("/api/admin/users/u2@example.com", json={"daily_token_limit": 50})
    assert r.status_code == 200
    usage.add("u2@example.com", 60)
    r = u2.post(
        "/api/chat", json={"question": "图书馆几点开门", "mode": "direct", "session_id": sid}
    )
    assert r.status_code == 429
    assert r.json()["detail"] == "今日个人 token 预算已用尽（上限 50），请明天再试"

    # admin 给自己设高限额后不受全局缺省影响
    r = c.patch("/api/admin/users/boss@example.com", json={"daily_token_limit": 999_000})
    assert r.status_code == 200
    usage.add("boss@example.com", 300_000)  # 超缺省但低于自己的限额
    boss_sid = c.post("/api/sessions", json={}).json()["session_id"]
    r = c.post(
        "/api/chat", json={"question": "图书馆几点开门", "mode": "direct", "session_id": boss_sid}
    )
    assert r.status_code == 200


# ---------- 邀请码 ----------


def test_admin_invites_create_and_list(tmp_path, biz, mem, auth, sess, usage, cp):
    c = make_admin_client(tmp_path, biz, mem, auth, sess, usage, cp)
    r = c.post("/api/admin/invites", json={"uses": 2, "days": 7, "note": "内测一批"})
    assert r.status_code == 200
    code = r.json()["code"]

    invites = {i["code"]: i for i in c.get("/api/admin/invites").json()}
    assert invites[code]["max_uses"] == 2
    assert invites[code]["used_count"] == 0
    assert invites[code]["created_by"] == "boss@example.com"

    # 发的码可注册核销（复用 auth 域原子核销）
    fresh = TestClient(c.app)
    r = fresh.post(
        "/api/auth/register",
        json={"email": "n@x.com", "password": "password123", "invite_code": code},
    )
    assert r.status_code == 200
    assert {i["code"]: i for i in c.get("/api/admin/invites").json()}[code]["used_count"] == 1

    for bad in ({"uses": 0}, {"uses": "x"}, {"days": 0}, {"note": "n" * 101}):
        assert c.post("/api/admin/invites", json=bad).status_code == 422, bad


# ---------- 会话巡查 + admin 删除连带 ----------


def test_admin_sessions_inspect_and_delete_cascade(
    tmp_path, biz, mem, auth, sess, usage, cp, pg_dsn
):
    c = make_admin_client(tmp_path, biz, mem, auth, sess, usage, cp)
    u2 = make_logged_client(c.app, auth, email="u2@example.com")
    sid = u2.post("/api/sessions", json={"kind": "compare"}).json()["session_id"]

    rows = c.get("/api/admin/sessions").json()
    assert {r["session_id"] for r in rows} >= {sid}
    by_sid = {r["session_id"]: r for r in rows}
    assert by_sid[sid]["user"] == "u2@example.com"
    assert by_sid[sid]["kind"] == "compare"

    assert {r["kind"] for r in c.get("/api/admin/sessions?kind=compare").json()} == {"compare"}
    assert all("u2@" in r["user"] for r in c.get("/api/admin/sessions?q=u2@").json())
    assert c.get("/api/admin/sessions?kind=bogus").status_code == 422

    # 注入 checkpointer 行 → admin 删任意会话 → 三处归零
    with psycopg.connect(pg_dsn, autocommit=True) as conn:
        conn.execute(
            "INSERT INTO checkpoints (thread_id, checkpoint_id, checkpoint) VALUES (%s, 'x', '{}')",
            (sid,),
        )
    mem.append_episode(sid, "u2@example.com", "user", "q")
    assert c.delete(f"/api/admin/sessions/{sid}").status_code == 200
    counts: list[int] = []
    with psycopg.connect(pg_dsn, autocommit=True) as conn:
        for t in _CP_TABLES:
            n = conn.execute(f"SELECT COUNT(*) FROM {t} WHERE thread_id = %s", (sid,))
            counts.append(int(n.fetchone()[0]))
        epis = int(
            conn.execute(
                "SELECT COUNT(*) FROM memory_episodic WHERE session_id = %s", (sid,)
            ).fetchone()[0]
        )
    assert counts == [0, 0, 0] and epis == 0
    assert sess.get("u2@example.com", sid) is None


# ---------- usage 趋势 ----------


def test_admin_usage_daily_and_top(tmp_path, biz, mem, auth, sess, usage, cp):
    c = make_admin_client(tmp_path, biz, mem, auth, sess, usage, cp)
    usage.add("a@x.com", 30)
    usage.add("b@x.com", 70)
    body = c.get("/api/admin/usage").json()
    assert body["today_top"][0] == {"user": "b@x.com", "tokens": 70}
    today = body["daily"][0]
    assert today["tokens"] == 100
    assert c.get("/api/admin/usage?days=0").status_code == 422
    assert c.get("/api/admin/usage?days=91").status_code == 422
