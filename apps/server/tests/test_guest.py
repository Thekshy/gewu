"""游客开放通道测试（P39）：影子用户签发 / 权限边界 / 限流 / 清理级联 / 开放注册。

口径（runbook P39）：
- GUEST_MODE 缺省关 → /api/auth/guest 404（与未开通道不可区分）；
- 开 → 免登签发 guest 影子用户（学生同集工具面 + 低配额 + 短 TTL 不续期）；
- 记忆/控制台面 require_member：游客 403；会话/台账/反馈照常（email 锚点贯通）；
- OPEN_REGISTRATION 缺省关=邀请码内测制（P21 语义不动）；开=纯邮箱+密码注册。
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import gewu.api.auth as auth_api
from gewu.api.app import create_app
from gewu.auth.store import GUEST_EMAIL_DOMAIN, InvalidCredentials
from gewu.config import Settings
from gewu.maintenance import prune_guests
from gewu.middleware import RateLimiter
from gewu.rag.store import Stats
from tests.conftest import make_logged_client
from tests.test_api import FakeRetriever, FakeStore, make_client

PWD = "password123"


@pytest.fixture(autouse=True)
def _reset_guest_limiters():
    """模块级限流器跨用例复位（与 login 限速测试同隔离模式）。"""
    auth_api._GUEST_MINUTE_LIMITER.reset()
    auth_api._GUEST_DAY_LIMITER.reset()
    auth_api._REGISTER_IP_LIMITER.reset()
    yield


def make_guest_client(
    tmp_path: Path,
    biz,
    mem,
    auth,
    sess,
    *,
    guest_mode: bool = True,
    ttl_days: int = 7,
    limit: int = 50_000,
    open_reg: bool = False,
) -> TestClient:
    settings = Settings(
        llm_api_key="lk",
        embed_api_key="ek",
        data_dir=tmp_path,
        guest_mode=guest_mode,
        guest_session_ttl_days=ttl_days,
        guest_daily_token_limit=limit,
        open_registration=open_reg,
    )
    app = create_app(
        settings,
        store=FakeStore(stats=Stats(docs=1, chunks=1, embedded=True), docs=[]),
        business=biz,
        memory=mem,
        auth=auth,
        sessions=sess,
        retriever=FakeRetriever(),
    )
    return TestClient(app)


# ---------- store 层 ----------


def test_create_guest_shape(auth):
    user, token = auth.create_guest(ttl=timedelta(days=7), daily_token_limit=123)
    assert user.role == "guest"
    assert user.email.startswith("guest-") and user.email.endswith(f"@{GUEST_EMAIL_DOMAIN}")
    assert user.display_name == "游客"
    assert auth.user_for_token(token).email == user.email
    assert auth.daily_limit(user.email) == 123
    with pytest.raises(InvalidCredentials):  # 密码为随机不可用串
        auth.login(user.email, "whatever-123")


def test_guest_session_no_sliding_refresh(auth):
    """游客 expires_at 命中续期窗口（<15d）也不滑动——短 TTL 硬过期。"""
    user, token = auth.create_guest(ttl=timedelta(days=8), daily_token_limit=1)

    def expires_at() -> object:
        with auth._pool.connection() as conn:  # noqa: SLF001 - 测试直达
            return conn.execute(
                "SELECT s.expires_at FROM auth_sessions s JOIN users u ON u.id = s.user_id"
                " WHERE u.email = %s",
                (user.email,),
            ).fetchone()[0]

    before = expires_at()
    assert auth.user_for_token(token) is not None
    assert expires_at() == before


def test_admin_cannot_assign_guest_role(auth):
    """VALID_ROLES 不含 guest：admin 改角色面不可把账号降成游客。"""
    auth.register("m@qtu.edu.cn", PWD, auth.create_invite())
    with pytest.raises(ValueError):
        auth.update_user("m@qtu.edu.cn", role="guest")


# ---------- RateLimiter window 扩展 ----------


def test_rate_limiter_custom_window():
    rl = RateLimiter(per_minute=2, window_sec=3600)
    assert rl.allow("k") and rl.allow("k")
    assert not rl.allow("k")  # 长窗内计数不随 60s 归零
    assert rl.allow("other")  # 键间独立


# ---------- API 层：签发与守卫 ----------


def test_guest_endpoint_disabled_by_default(tmp_path, biz, mem, auth, sess):
    c = make_client(tmp_path, biz, mem, auth, sess)  # 缺省 Settings：guest_mode=False
    assert c.post("/api/auth/guest").status_code == 404


def test_guest_sign_in_and_me(tmp_path, biz, mem, auth, sess):
    c = make_guest_client(tmp_path, biz, mem, auth, sess)
    r = c.post("/api/auth/guest")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["role"] == "guest"
    assert body["email"].endswith(f"@{GUEST_EMAIL_DOMAIN}")
    assert "gewu_session" in {k for k, _ in c.cookies.items()} or r.headers.get("set-cookie")
    me = c.get("/api/auth/me")
    assert me.status_code == 200 and me.json()["role"] == "guest"


def test_guest_minute_throttle(tmp_path, biz, mem, auth, sess):
    c = make_guest_client(tmp_path, biz, mem, auth, sess)
    for _ in range(5):
        assert c.post("/api/auth/guest").status_code == 200
    assert c.post("/api/auth/guest").status_code == 429


def test_guest_logout(tmp_path, biz, mem, auth, sess):
    c = make_guest_client(tmp_path, biz, mem, auth, sess)
    assert c.post("/api/auth/guest").status_code == 200
    assert c.post("/api/auth/logout").status_code == 200
    assert c.get("/api/auth/me").status_code == 401


def test_guest_sessions_flow(tmp_path, biz, mem, auth, sess):
    c = make_guest_client(tmp_path, biz, mem, auth, sess)
    assert c.post("/api/auth/guest").status_code == 200
    r = c.post("/api/sessions", json={"kind": "chat"})
    assert r.status_code == 200
    sid = r.json()["session_id"]
    assert [s["session_id"] for s in c.get("/api/sessions").json()] == [sid]
    # 归属保护：他人（含其他游客）不可见
    c2 = make_guest_client(tmp_path, biz, mem, auth, sess)
    assert c2.post("/api/auth/guest").status_code == 200
    assert c2.get(f"/api/sessions/{sid}/messages").status_code == 404


def test_guest_blocked_on_member_apis(tmp_path, biz, mem, auth, sess):
    c = make_guest_client(tmp_path, biz, mem, auth, sess)
    assert c.post("/api/auth/guest").status_code == 200
    assert c.get("/api/docs").status_code == 403
    assert c.post("/api/search", json={"query": "x"}).status_code == 403
    assert c.get("/api/memory/facts").status_code == 403
    r = c.post("/api/memory/facts", json={"kind": "profile", "key": "k", "value": "v"})
    assert r.status_code == 403
    assert c.delete("/api/memory/facts?kind=profile&key=k").status_code == 403
    # 正式成员同端点放行（对照组）
    member = make_logged_client(c.app, auth)
    assert member.get("/api/docs").status_code == 200
    assert member.get("/api/memory/facts").status_code == 200


def test_guest_business_overview_ok(tmp_path, biz, mem, auth, sess):
    """台账/反馈属「能做的都开」面：游客按自身 email 隔离可见。"""
    c = make_guest_client(tmp_path, biz, mem, auth, sess)
    assert c.post("/api/auth/guest").status_code == 200
    assert c.get("/api/business/overview").status_code == 200


def test_guest_admin_apis_forbidden(tmp_path, biz, mem, auth, sess):
    c = make_guest_client(tmp_path, biz, mem, auth, sess)
    assert c.post("/api/auth/guest").status_code == 200
    assert c.get("/api/admin/stats").status_code == 403


# ---------- 工具权限矩阵 ----------


def test_guest_tool_matrix():
    from gewu.agent.tools import has_role, role_label, tools_for

    tools = tools_for()
    for name in (
        "query_venues",
        "my_bookings",
        "leave_status",
        "book_venue",
        "cancel_booking",
        "submit_leave",
    ):
        assert has_role(tools[name].roles, "guest"), name
    for name in ("pending_leaves", "approve_leave"):
        assert not has_role(tools[name].roles, "guest"), name
    assert role_label("guest") == "游客"


# ---------- 清理级联 ----------


def test_prune_guests_cascade(pg_dsn, pg_lock, auth, sess, mem, usage, fb, biz):
    from datetime import date

    from gewu.memory import Fact

    guest, _token = auth.create_guest(ttl=timedelta(days=7), daily_token_limit=1)
    s = sess.create(guest.email, "chat")
    mem.append_episode(s.session_id, guest.email, "user", "问了一句话")
    mem.upsert_facts(guest.email, [Fact(kind="profile", key="年级", value="大三")])
    usage.add(guest.email, 42)
    fb.upsert(guest.email, s.session_id, "问题", "good")
    start = (date.today() + timedelta(days=1)).isoformat()
    end = (date.today() + timedelta(days=2)).isoformat()
    assert biz.submit_leave(guest.email, "事假", start, end, "测试").ok

    # 对照组：正式用户数据不动
    member, _ = auth.register("m@qtu.edu.cn", PWD, auth.create_invite())
    ms = sess.create(member.email, "chat")
    mem.upsert_facts(member.email, [Fact(kind="profile", key="k", value="v")])

    counts = prune_guests(pg_dsn, days=0)  # cutoff=now：清全部已创建游客
    assert counts["users"] >= 1
    assert auth.user_for_token(_token) is None  # users 行已删（session 级联）
    assert sess.get(guest.email, s.session_id) is None
    assert mem.recent_episodes(s.session_id, 4) == []
    assert mem.all_facts(guest.email) == []
    assert usage.today(guest.email) == 0
    # 对照组完好
    assert sess.get(member.email, ms.session_id) is not None
    assert len(mem.all_facts(member.email)) == 1


# ---------- 开放注册（P39 二段：OPEN_REGISTRATION） ----------


def test_open_registration_email_password_only(tmp_path, biz, mem, auth, sess):
    """开放态：免邀请码注册成功即登录；health 暴露 open_registration=true。"""
    c = make_guest_client(tmp_path, biz, mem, auth, sess, open_reg=True)
    assert c.get("/api/health").json()["open_registration"] is True
    r = c.post("/api/auth/register", json={"email": "new@qtu.edu.cn", "password": PWD})
    assert r.status_code == 200, r.text
    assert r.json()["role"] == "student"
    assert c.get("/api/auth/me").json()["email"] == "new@qtu.edu.cn"


def test_registration_invite_mode_default(tmp_path, biz, mem, auth, sess):
    """缺省关=邀请码内测制：无邀请码 422；health 报 false（P21 语义不动）。"""
    c = make_client(tmp_path, biz, mem, auth, sess)
    assert c.get("/api/health").json()["open_registration"] is False
    r = c.post("/api/auth/register", json={"email": "x@qtu.edu.cn", "password": PWD})
    assert r.status_code == 422
    assert r.json()["detail"] == "邀请码不能为空"


def test_register_ip_throttle(tmp_path, biz, mem, auth, sess):
    """开放态防滥用：单 IP 5 次/分钟，第 6 次 429。"""
    c = make_guest_client(tmp_path, biz, mem, auth, sess, open_reg=True)
    for i in range(5):
        r = c.post("/api/auth/register", json={"email": f"u{i}@qtu.edu.cn", "password": PWD})
        assert r.status_code == 200, r.text
    r = c.post("/api/auth/register", json={"email": "u9@qtu.edu.cn", "password": PWD})
    assert r.status_code == 429
