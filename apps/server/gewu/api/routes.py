"""路由实现：PARITY §2 契约（P14-1 四端点；chat/reset 随后续 ticket 接入）。

错误体统一 {"detail": "<原因>"}：绑定/类型错→「请求体不是合法 JSON」（全局
exception handler 收口），语义越界→各端点中文 detail。不用 pydantic 默认校验
错误体（PARITY §2.3 差异决定）。

P21-3 认证范围：search 需登录；business/overview 登录者本人视图（admin 可
?all=1）；business/reset 仅 admin。health 保持公开（探活）；docs 需登录
（P36 与 search 口径统一）。
"""

from __future__ import annotations

import json
import traceback
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Body, HTTPException, Request

from gewu.api.auth import require_admin, require_member, require_user

router = APIRouter()


def _internal(e: Exception) -> HTTPException:
    """500 收口（P36）：异常原文可能含 SQL/DSN 片段，仅进日志，对外统一笼统文案。"""
    print(f"[routes] 500 {type(e).__name__}: {e}", flush=True)
    traceback.print_exc()
    return HTTPException(status_code=500, detail="服务内部错误，请稍后再试")


def truncate_runes(text: str, limit: int) -> str:
    """按 Unicode 字符截断（Python str 天然按 rune 计，与 Go []rune 语义一致）。"""
    return text[:limit]


def _body_param(payload: dict, key: str, expected: type) -> object:
    """取 body 字段并做类型守卫（类型不符=绑定失败，对齐 Go ShouldBindJSON）。"""
    v = payload.get(key)
    if v is not None and (isinstance(v, bool) or not isinstance(v, expected)):
        raise HTTPException(status_code=422, detail="请求体不是合法 JSON")
    return v


def _budget_usage(settings) -> tuple[int, int]:
    """读 data/usage.json（与 Go internal/budget 同格式：{"date","tokens"}），跨天归零。"""
    path = settings.data_dir / "usage.json"
    try:
        f = json.loads(path.read_text(encoding="utf-8"))
        used = int(f["tokens"]) if f.get("date") == date.today().isoformat() else 0
    except (OSError, ValueError, KeyError, TypeError):
        used = 0
    return used, settings.daily_token_budget


@router.get("/api/health")
def health(request: Request):
    settings = request.app.state.settings
    try:
        stats = request.app.state.store.get_stats()
    except Exception as e:
        raise _internal(e) from e
    used, limit = _budget_usage(settings)
    return {
        "status": "ok",
        "version": request.app.version,
        "llm": bool(settings.llm_api_key),
        "embeddings": bool(settings.embed_api_key) and stats.embedded,
        "docs": stats.docs,
        "chunks": stats.chunks,
        "budget": {"used": used, "limit": limit},
    }


@router.get("/api/docs")
def list_docs(request: Request):
    require_member(request)  # P36 收紧：语料清单枚举与 search 同口径；P39 起非游客（控制台面）
    try:
        docs = request.app.state.store.list_docs()
    except Exception as e:
        raise _internal(e) from e
    return [
        {
            "doc_id": d.doc_id,
            "title": d.title,
            "source": d.source,
            "updated": d.updated,
            "chunks": d.chunks,
        }
        for d in docs
    ]


@router.post("/api/search")
def search(request: Request, payload: Annotated[dict, Body(...)]):
    require_member(request)  # console 检索调试：正式成员（P39 游客 403）
    query = _body_param(payload, "query", str)
    if query is None:
        query = ""
    length = len(query)  # Python str 按 rune 计
    if length < 1 or length > 200:
        raise HTTPException(status_code=422, detail="query 长度需在 1~200 字之间")
    k = _body_param(payload, "k", int)
    if k is None:
        k = 5
    if k < 1 or k > 20:
        raise HTTPException(status_code=422, detail="k 需在 1~20 之间")
    try:
        hits = request.app.state.retriever.search(query, k)
    except Exception as e:
        raise _internal(e) from e
    return [
        {
            "doc_id": h.doc_id,
            "title": h.title,
            "source": h.source,
            "seq": h.seq,
            "text": truncate_runes(h.text, 300),
        }
        for h in hits
    ]


@router.post("/api/business/reset")
def business_reset(request: Request):
    require_admin(request)
    try:
        request.app.state.business.reset()
    except Exception as e:
        raise _internal(e) from e
    return {"status": "ok"}


@router.get("/api/business/overview")
def business_overview(request: Request, all: str | None = None):
    user = require_user(request)
    admin_all = all == "1" and user.role == "admin"
    biz = request.app.state.business
    try:
        bookings = biz.all_bookings()
        tickets = biz.all_tickets()
    except Exception as e:
        raise _internal(e) from e
    if not admin_all:  # 本人视图：台账按登录 email 过滤（admin ?all=1 看全量）
        bookings = [b for b in bookings if b.user == user.email]
        tickets = [t for t in tickets if t.user == user.email]
    return {
        "scope": "all" if admin_all else "mine",
        "bookings": [
            {
                "booking_id": b.booking_id,
                "venue": b.venue,
                "date": b.date,
                "slot": b.slot,
                "user": b.user,
            }
            for b in bookings
        ],
        "tickets": [
            {
                "ticket": t.ticket,
                "user": t.user,
                "leave_type": t.leave_type,
                "start": t.start,
                "end": t.end,
                "days": t.days,
                "approver": t.approver,
                "status": t.status,
            }
            for t in tickets
        ],
    }
