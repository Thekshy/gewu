"""认证路由（P21）：register / login / logout / me + 登录态守卫。

契约（PARITY「认证契约」节）：
- 422 请求体格式/长度校验；400 邀请码无效；409 邮箱已注册；
  401 未登录或凭证错（login 失败统一「邮箱或密码错误」不区分原因）；
  429 登录尝试过于频繁（P36 账号/IP 双键限速）。
- cookie：gewu_session，httpOnly + SameSite=Lax，30d；Secure 由
  COOKIE_SECURE 控制（M4 https 部署后开启）。前端经 next 同源代理访问，
  无跨域 cookie 依赖。
- P39 游客开放通道：POST /api/auth/guest 免登签发受限影子用户
  （GUEST_MODE 缺省关=现状；开时 IP 双闸 5/min + 20/天防刷行）。
"""

from __future__ import annotations

import re
from datetime import timedelta
from typing import Annotated

from fastapi import APIRouter, Body, HTTPException, Request, Response

from gewu.auth.store import (
    COOKIE_NAME,
    GUEST_ROLE,
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

# P39 游客签发双闸：分钟闸防瞬时刷行，日闸（window_sec 扩展）封顶单 IP 每日
# 产生的影子用户行数；游客行由 make guest-prune 定期回收。
_GUEST_MINUTE_LIMITER = RateLimiter(per_minute=5)
_GUEST_DAY_LIMITER = RateLimiter(per_minute=20, window_sec=86400)

# 开放注册态的防滥用闸（邀请码制下注册受邀请码门槛保护，限速恒加无害）
_REGISTER_IP_LIMITER = RateLimiter(per_minute=5)


def _register_throttle(request: Request) -> None:
    if not _REGISTER_IP_LIMITER.allow(f"ip:{client_ip(request)}"):
        raise HTTPException(status_code=429, detail="注册过于频繁，请稍后再试")


def _guest_throttle(request: Request) -> None:
    key = f"ip:{client_ip(request)}"
    if not _GUEST_MINUTE_LIMITER.allow(key) or not _GUEST_DAY_LIMITER.allow(key):
        raise HTTPException(status_code=429, detail="游客身份领取过于频繁，请稍后再试")


def _login_throttle(request: Request, email: str) -> None:
    if not _LOGIN_IP_LIMITER.allow(f"ip:{client_ip(request)}") or not (
        _LOGIN_EMAIL_LIMITER.allow(f"email:{email.lower()}")
    ):
        raise HTTPException(status_code=429, detail="登录尝试过于频繁，请稍后再试")


def _auth(request: Request) -> AuthStore:
    return request.app.state.auth


def _set_session_cookie(
    response: Response, request: Request, token: str, max_age: int = _SESSION_COOKIE_MAX_AGE
) -> None:
    response.set_cookie(
        COOKIE_NAME,
        token,
        max_age=max_age,
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


def require_member(request: Request) -> User:
    """正式成员守卫（P39）：游客（role=guest）403——记忆/控制台等登录后解锁的面。"""
    user = require_user(request)
    if user.role == GUEST_ROLE:
        raise HTTPException(status_code=403, detail="该功能需登录后使用")
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
    _register_throttle(request)
    # P39 二段：OPEN_REGISTRATION=1 免邀请码（纯邮箱+密码）；缺省关=邀请码内测制
    if request.app.state.settings.open_registration:
        invite_code = None
    else:
        invite_code = payload.get("invite_code")
        if not isinstance(invite_code, str) or not invite_code.strip():
            raise HTTPException(status_code=422, detail="邀请码不能为空")
        invite_code = invite_code.strip()
    try:
        user, token = _auth(request).register(
            email, password, invite_code, display_name=email.split("@", 1)[0]
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


@router.post("/api/auth/guest")
def guest_sign_in(request: Request, response: Response):
    """P39 游客签发：免登领取受限影子用户（能力面=学生同集，见 PARITY §10）。

    GUEST_MODE 缺省关——关闭时 404 与未开通道不可区分（不暴露开关存在）。
    游客会话短 TTL 硬过期（不滑动续期），cookie max_age 对齐；响应体同 me。
    """
    settings = request.app.state.settings
    if not settings.guest_mode:
        raise HTTPException(status_code=404, detail="Not Found")
    _guest_throttle(request)
    ttl = timedelta(days=settings.guest_session_ttl_days)
    user, token = _auth(request).create_guest(
        ttl=ttl, daily_token_limit=settings.guest_daily_token_limit
    )
    _set_session_cookie(response, request, token, max_age=int(ttl.total_seconds()))
    return _user_payload(user)


@router.get("/api/auth/me")
def me(request: Request):
    return _user_payload(require_user(request))
