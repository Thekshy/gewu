"""管理后台路由（P23）：用户/邀请码/会话巡查/用量统计（全部 require_admin）。

403 语义沿用 P21（require_admin）；PATCH users 带自我保护（不能改自己的
role/status，防唯一 admin 锁死；限额可改自己）。会话巡查是列表级（Q3 拍板：
不开放他人会话内容 UI）；删除复用 P22 三处连带顺序，不做属主校验。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Body, HTTPException, Request

from gewu.api.auth import require_admin
from gewu.auth.store import VALID_ROLES
from gewu.session.store import VALID_KINDS

router = APIRouter()

_LIMIT_MAX = 10_000_000


def _session_row(s) -> dict:
    return {
        "session_id": s.session_id,
        "user": s.user,
        "title": s.title,
        "kind": s.kind,
        "updated_at": s.updated_at,
    }


@router.get("/api/admin/stats")
def admin_stats(request: Request):
    require_admin(request)
    auth_stats = request.app.state.auth.stats()
    budget = request.app.state.budget
    usage = getattr(request.app.state, "usage", None)
    today_total = sum(n for _, n in usage.today_all()) if usage is not None else 0
    return {
        **auth_stats,
        "chat_sessions": request.app.state.sessions.count(),
        "today_tokens": today_total,
        "budget": {"used": budget.used(), "limit": budget.limit()},
    }


@router.get("/api/admin/users")
def admin_users(request: Request):
    require_admin(request)
    usage = getattr(request.app.state, "usage", None)
    today = dict(usage.today_all()) if usage is not None else {}
    auth = request.app.state.auth
    out = []
    for u in auth.list_users():
        out.append(
            {
                "email": u.email,
                "display_name": u.display_name,
                "role": u.role,
                "status": u.status,
                "daily_token_limit": auth.daily_limit(u.email),
                "today_tokens": today.get(u.email, 0),
            }
        )
    return out


@router.patch("/api/admin/users/{email}")
def admin_update_user(email: str, request: Request, payload: Annotated[dict, Body(...)]):
    admin = require_admin(request)
    role = payload.get("role")
    status = payload.get("status")
    limit = payload.get("daily_token_limit")
    if role is not None and role not in VALID_ROLES:
        raise HTTPException(status_code=422, detail=f"role 必须为 {'/'.join(VALID_ROLES)}")
    if status is not None and status not in ("active", "disabled"):
        raise HTTPException(status_code=422, detail="status 必须为 active/disabled")
    clear_limit = limit is None and "daily_token_limit" in payload
    if not clear_limit and limit is not None:
        if isinstance(limit, bool) or not isinstance(limit, int):
            raise HTTPException(status_code=422, detail="daily_token_limit 需为整数")
        if not 1 <= limit <= _LIMIT_MAX:
            raise HTTPException(
                status_code=422, detail=f"daily_token_limit 需在 1~{_LIMIT_MAX} 之间"
            )
    # 自我保护（Q4）：不能改自己的角色/状态（改自己限额允许）
    target = email.strip().lower()
    if target == admin.email and (role is not None or status is not None):
        raise HTTPException(status_code=422, detail="不能修改自己的角色或状态")
    try:
        user = request.app.state.auth.update_user(
            target,
            role=role,
            status=status,
            daily_token_limit=limit,
            clear_limit=clear_limit,
        )
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    if user is None:
        raise HTTPException(status_code=404, detail="用户不存在")
    return {
        "email": user.email,
        "display_name": user.display_name,
        "role": user.role,
        "status": user.status,
        "daily_token_limit": request.app.state.auth.daily_limit(user.email),
    }


@router.get("/api/admin/invites")
def admin_invites(request: Request):
    require_admin(request)
    return request.app.state.auth.list_invites()


@router.post("/api/admin/invites")
def admin_create_invite(request: Request, payload: Annotated[dict, Body(...)]):
    admin = require_admin(request)
    uses = payload.get("uses", 1)
    days = payload.get("days")
    note = payload.get("note", "")
    if isinstance(uses, bool) or not isinstance(uses, int) or not 1 <= uses <= 999:
        raise HTTPException(status_code=422, detail="uses 需在 1~999 之间")
    if days is not None and (isinstance(days, bool) or not isinstance(days, int) or days < 1):
        raise HTTPException(status_code=422, detail="days 需为正整数")
    if not isinstance(note, str) or len(note) > 100:
        raise HTTPException(status_code=422, detail="note 需为不超过 100 字的文本")
    code = request.app.state.auth.create_invite(
        uses=uses, days=days if days else None, note=note, created_by=admin.email
    )
    return {"code": code}


@router.get("/api/admin/sessions")
def admin_sessions(request: Request, kind: str | None = None, q: str | None = None):
    require_admin(request)
    if kind is not None and kind not in VALID_KINDS:
        raise HTTPException(status_code=422, detail=f"kind 必须为 {'/'.join(VALID_KINDS)}")
    if q is not None and len(q) > 64:
        raise HTTPException(status_code=422, detail="q 过长（上限 64 字符）")
    return [_session_row(s) for s in request.app.state.sessions.list_all(kind, q)]


@router.delete("/api/admin/sessions/{session_id}")
def admin_delete_session(session_id: str, request: Request):
    require_admin(request)
    if request.app.state.sessions.delete_any(session_id):
        # 连带顺序同 P22（Q4）：先 checkpointer 后 episodic（delete_any 已删业务
        # 行；cp 失败仅留悬空检查点，无入口可达，无害）
        try:
            request.app.state.checkpointer.delete_thread(session_id)
        except Exception as e:  # noqa: BLE001 - 悬空检查点无害，继续
            print(f"[admin] checkpointer 删除失败（悬空无害，继续）：{e}", flush=True)
        request.app.state.memory.delete_episodes(session_id)
    return {"status": "ok"}


@router.get("/api/admin/usage")
def admin_usage(request: Request, days: int = 7):
    require_admin(request)
    if not 1 <= days <= 90:
        raise HTTPException(status_code=422, detail="days 需在 1~90 之间")
    usage = getattr(request.app.state, "usage", None)
    if usage is None:
        return {"daily": [], "today_top": []}
    return {
        "daily": [{"day": d, "tokens": n} for d, n in usage.daily_totals(days)],
        "today_top": [{"user": u, "tokens": n} for u, n in usage.today_all()[:10]],
    }
