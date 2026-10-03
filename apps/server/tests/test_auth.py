"""auth 域测试（P21-1）：邀请码原子核销 / 注册登录登出 / 会话过期 / 密码哈希。

store 层直测 + API 层契约（422/400/409/401 映射、cookie 会话）。
"""

from __future__ import annotations

import hashlib
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from gewu.auth.store import (
    EmailTaken,
    InvalidCredentials,
    InviteInvalid,
    hash_password,
    verify_password,
)
from tests.conftest import make_logged_client
from tests.test_api import make_client

PWD = "password123"


# ---------- store 层 ----------


def test_register_and_token_roundtrip(auth):
    code = auth.create_invite(uses=1)
    user, token = auth.register("a@qtu.edu.cn", PWD, code)
    assert user.email == "a@qtu.edu.cn"
    assert user.role == "student" and user.status == "active"
    assert auth.user_for_token(token).email == "a@qtu.edu.cn"
    auth.delete_session(token)
    assert auth.user_for_token(token) is None


def test_register_rejects_bad_invite(auth):
    with pytest.raises(InviteInvalid):
        auth.register("a@qtu.edu.cn", PWD, "no-such-code")


def test_invite_oversubscribe_and_expiry(auth):
    code = auth.create_invite(uses=1)
    auth.register("a@qtu.edu.cn", PWD, code)
    with pytest.raises(InviteInvalid):  # 用尽
        auth.register("b@qtu.edu.cn", PWD, code)
    # 过期码：直接 SQL 造（days=0 边界受时钟偏差影响，不靠它）
    expired = auth.create_invite(uses=1)
    with auth._pool.connection() as conn:  # noqa: SLF001 - 测试直达：人造过期
        conn.execute(
            "UPDATE invite_codes SET expires_at = now() - interval '1 day' WHERE code = %s",
            (expired,),
        )
    with pytest.raises(InviteInvalid):
        auth.register("c@qtu.edu.cn", PWD, expired)


def test_invite_concurrent_single_winner(auth):
    """并发核销 uses=1 的码：恰好一个成功（UPDATE ... RETURNING 行锁原子性）。"""
    code = auth.create_invite(uses=1)
    emails = [f"u{i}@qtu.edu.cn" for i in range(4)]
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda e: _try_register(auth, e, code), emails))
    assert sum(1 for r in results if r) == 1
    assert sum(1 for r in results if r is False) == 3


def _try_register(auth, email: str, code: str) -> bool:
    try:
        auth.register(email, PWD, code)
        return True
    except InviteInvalid:
        return False


def test_email_taken_rolls_back_invite(auth):
    code = auth.create_invite(uses=2)
    auth.register("dup@qtu.edu.cn", PWD, code)
    with pytest.raises(EmailTaken):
        auth.register("dup@qtu.edu.cn", "another-pass-9", code)
    # 核销随事务回滚：同码还能注册一个新邮箱
    user, _ = auth.register("fresh@qtu.edu.cn", PWD, code)
    assert user.email == "fresh@qtu.edu.cn"


def test_login_paths(auth):
    _, token = auth.register("a@qtu.edu.cn", PWD, auth.create_invite())
    with pytest.raises(InvalidCredentials):
        auth.login("a@qtu.edu.cn", "wrong-pass-1")
    with pytest.raises(InvalidCredentials):
        auth.login("ghost@qtu.edu.cn", PWD)
    user, token2 = auth.login("a@qtu.edu.cn", PWD)
    assert user.email == "a@qtu.edu.cn"
    assert auth.user_for_token(token) and auth.user_for_token(token2)  # 双会话并存


def test_expired_session_rejected(auth):
    _, token = auth.register("a@qtu.edu.cn", PWD, auth.create_invite())
    with auth._pool.connection() as conn:  # noqa: SLF001 - 测试直达：人造过期
        conn.execute(
            "UPDATE auth_sessions SET expires_at = now() - interval '1 day' WHERE token_hash = %s",
            (hashlib.sha256(token.encode()).hexdigest(),),
        )
    assert auth.user_for_token(token) is None


def test_password_hashing():
    h = hash_password(PWD)
    assert h.startswith("$argon2id$")
    assert verify_password(PWD, h)
    assert not verify_password("wrong-pass-1", h)
    assert not verify_password(PWD, "not-a-hash")


def test_promote_admin(auth):
    auth.register("boss@qtu.edu.cn", PWD, auth.create_invite())
    assert auth.promote_admin("BOSS@qtu.edu.cn")  # 大小写归一
    _, token = auth.login("boss@qtu.edu.cn", PWD)
    assert auth.user_for_token(token).role == "admin"
    assert not auth.promote_admin("ghost@qtu.edu.cn")


# ---------- API 层 ----------


def test_auth_api_flow(tmp_path, biz, mem, auth, sess):
    c = make_client(tmp_path, biz, mem, auth, sess)
    assert c.get("/api/auth/me").status_code == 401

    code = auth.create_invite(uses=1)
    # 校验序列：邮箱格式 / 密码长度 / 邀请码必填
    assert (
        c.post(
            "/api/auth/register",
            json={"email": "bad", "password": PWD, "invite_code": code},
        ).status_code
        == 422
    )
    assert (
        c.post(
            "/api/auth/register",
            json={"email": "a@qtu.edu.cn", "password": "short", "invite_code": code},
        ).status_code
        == 422
    )
    assert (
        c.post("/api/auth/register", json={"email": "a@qtu.edu.cn", "password": PWD}).status_code
        == 422
    )

    r = c.post(
        "/api/auth/register",
        json={"email": "a@qtu.edu.cn", "password": PWD, "invite_code": code},
    )
    assert r.status_code == 200
    assert r.json() == {"email": "a@qtu.edu.cn", "display_name": "a", "role": "student"}
    assert c.cookies.get("gewu_session")  # 注册即登录
    me = c.get("/api/auth/me").json()
    assert me["email"] == "a@qtu.edu.cn"

    # 登出即失效（服务端 session 删除 + cookie 清除）
    assert c.post("/api/auth/logout").status_code == 200
    assert c.get("/api/auth/me").status_code == 401

    # 登录：错密码 401 统一话术；成功后 me 恢复
    bad = c.post("/api/auth/login", json={"email": "a@qtu.edu.cn", "password": "x" * 12})
    assert bad.json()["detail"] == "邮箱或密码错误"
    ok = c.post("/api/auth/login", json={"email": "a@qtu.edu.cn", "password": PWD})
    assert ok.status_code == 200
    assert c.get("/api/auth/me").status_code == 200


def test_login_throttle_429_per_account(tmp_path, biz, mem, auth, sess):
    """P36：login 账号键限速——同账号第 11 次尝试 429（连正确密码也拦），register 不计次。"""
    from gewu.api.auth import _LOGIN_EMAIL_LIMITER, _LOGIN_IP_LIMITER

    _LOGIN_EMAIL_LIMITER.reset()
    _LOGIN_IP_LIMITER.reset()
    try:
        app = make_client(tmp_path, biz, mem, auth, sess).app
        make_logged_client(app, auth, email="victim@qtu.edu.cn")
        c = TestClient(app)
        for _ in range(10):
            r = c.post("/api/auth/login", json={"email": "victim@qtu.edu.cn", "password": "x" * 12})
            assert r.status_code == 401
        # 第 11 次：密码正确也 429（固定窗口内账号键耗尽）
        r = c.post("/api/auth/login", json={"email": "victim@qtu.edu.cn", "password": PWD})
        assert r.status_code == 429
        assert r.json()["detail"] == "登录尝试过于频繁，请稍后再试"
        # 别的账号不受牵连（register + 登录正常走通）
        c2 = TestClient(app)
        code = auth.create_invite()
        assert (
            c2.post(
                "/api/auth/register",
                json={"email": "bystander@qtu.edu.cn", "password": PWD, "invite_code": code},
            ).status_code
            == 200
        )
    finally:
        _LOGIN_EMAIL_LIMITER.reset()
        _LOGIN_IP_LIMITER.reset()


def test_register_duplicate_email_409(tmp_path, biz, mem, auth, sess):
    app = make_client(tmp_path, biz, mem, auth, sess).app
    make_logged_client(app, auth, email="dup@qtu.edu.cn")
    c2 = TestClient(app)
    code = auth.create_invite()
    r = c2.post(
        "/api/auth/register",
        json={"email": "dup@qtu.edu.cn", "password": PWD, "invite_code": code},
    )
    assert r.status_code == 409
    # 回滚连带：该码未消耗，换邮箱仍可用
    r = c2.post(
        "/api/auth/register",
        json={"email": "ok@qtu.edu.cn", "password": PWD, "invite_code": code},
    )
    assert r.status_code == 200


def test_register_used_up_invite_400(tmp_path, biz, mem, auth, sess):
    c = make_client(tmp_path, biz, mem, auth, sess)
    code = auth.create_invite(uses=1)
    body = {"email": "a@qtu.edu.cn", "password": PWD, "invite_code": code}
    assert c.post("/api/auth/register", json=body).status_code == 200
    body["email"] = "b@qtu.edu.cn"
    r = c.post("/api/auth/register", json=body)
    assert r.status_code == 400
