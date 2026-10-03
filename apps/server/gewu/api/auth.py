"""认证路由（P21）：register / login / logout / me + 登录态守卫。

契约（PARITY「认证契约」节）：
- 422 请求体格式/长度校验；400 邀请码无效；409 邮箱已注册；
  401 未登录或凭证错（login 失败统一「邮箱或密码错误」不区分原因）；
  429 登录尝试过于频繁（P36 账号/IP 双键限速）。
- cookie：gewu_session，httpOnly + SameSite=Lax，30d；Secure 由
  COOKIE_SECURE 控制（M4 https 部署后开启）。前端经 next 同源代理访问，
  无跨域 cookie 依赖。
"""

from __future__ import annotations

import re
from typing import Annotated

from fastapi import APIRouter, Body, HTTPException, Request, Response

from gewu.auth.store import (
    COOKIE_NAME,
    AuthError,
    AuthStore,
    EmailTaken,
    InvalidCredentials,
    InviteInvalid,
    User,
)
from gewu.middleware import RateLimiter, client_ip

router = APIRouter()

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_SESSION_COOKIE_MAX_AGE = 30 * 24 * 3600

# P36 登录暴力破解防线：账号键 10 次/分（单账号爆破）+ IP 键 30 次/分
# （单 IP 多账号喷洒）。进程内固定窗口与全局限流同语义（重启清零）；
# register 不设（邀请码门槛已封批量注册面）。
_LOGIN_EMAIL_LIMITER = RateLimiter(per_minute=10)
_LOGIN_IP_LIMITER = RateLimiter(per_minute=30)


def _login_throttle(request: Request, email: str) -> None:
    if not _LOGIN_IP_LIMITER.allow(f"ip:{client_ip(request)}") or not (
        _LOGIN_EMAIL_LIMITER.allow(f"email:{email.lower()}")
    ):
        raise HTTPException(status_code=429, detail="登录尝试过于频繁，请稍后再试")


def _auth(request: Request) -> AuthStore:
    return request.app.state.auth


def _set_session_cookie(response: Response, request: Request, token: str) -> None:
    response.set_cookie(
        COOKIE_NAME,
        token,
        max_age=_SESSION_COOKIE_MAX_AGE,
        httponly=True,
        samesite="lax",
        secure=request.app.state.settings.cookie_secure,
    )


def require_user(request: Request) -> User:
    """登录态守卫（dependency 语义的普通函数：各受保护端点首行调用）。"""
    token = request.cookies.get(COOKIE_NAME, "")
    user = _auth(request).user_for_token(token) if token else None
    if user is None:
        raise HTTPException(status_code=401, detail="未登录或会话已过期")
    return user


def require_admin(request: Request) -> User:
    user = require_user(request)
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="需要管理员权限")
    return user


def _user_payload(user: User) -> dict:
    return {"email": user.email, "display_name": user.display_name, "role": user.role}


def _parse_credentials(payload: dict) -> tuple[str, str]:
    email = payload.get("email")
    password = payload.get("password")
    if not isinstance(email, str) or not isinstance(password, str):
        raise HTTPException(status_code=422, detail="请求体不是合法 JSON")
    email = email.strip()
    if not _EMAIL_RE.match(email):
        raise HTTPException(status_code=422, detail="邮箱格式不正确")
    if len(password) < 8 or len(password) > 128:
        raise HTTPException(status_code=422, detail="密码长度需在 8~128 位之间")
    return email, password


@router.post("/api/auth/register")
def register(request: Request, response: Response, payload: Annotated[dict, Body(...)]):
    email, password = _parse_credentials(payload)
    invite_code = payload.get("invite_code")
    if not isinstance(invite_code, str) or not invite_code.strip():
        raise HTTPException(status_code=422, detail="邀请码不能为空")
    try:
        user, token = _auth(request).register(
            email, password, invite_code.strip(), display_name=email.split("@", 1)[0]
        )
    except InviteInvalid as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except EmailTaken as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    _set_session_cookie(response, request, token)
    return _user_payload(user)


@router.post("/api/auth/login")
def login(request: Request, response: Response, payload: Annotated[dict, Body(...)]):
    email, password = _parse_credentials(payload)
    _login_throttle(request, email)
    try:
        user, token = _auth(request).login(email, password)
    except InvalidCredentials as e:
        raise HTTPException(status_code=401, detail=str(e)) from e
    except AuthError as e:  # pragma: no cover - 其余 auth 语义错误按 400
        raise HTTPException(status_code=400, detail=str(e)) from e
    _set_session_cookie(response, request, token)
    return _user_payload(user)


@router.post("/api/auth/logout")
def logout(request: Request, response: Response):
    token = request.cookies.get(COOKIE_NAME, "")
    _auth(request).delete_session(token)
    response.delete_cookie(COOKIE_NAME, samesite="lax")
    return {"status": "ok"}


@router.get("/api/auth/me")
def me(request: Request):
    return _user_payload(require_user(request))
