"""路由实现：PARITY §2 契约（P14-0 三只读端点；chat/search/reset 随后续 ticket 接入）。

错误体统一 {"detail": "<原因>"}（PARITY §2.3 差异决定：不用 pydantic 默认校验错误体，
校验类 422 的中文 detail 随对应端点接入时实现）。
"""

from __future__ import annotations

import json
from datetime import date

from fastapi import APIRouter, HTTPException, Request

router = APIRouter()


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
        raise HTTPException(status_code=500, detail=str(e)) from e
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
    try:
        docs = request.app.state.store.list_docs()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e
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


@router.get("/api/business/overview")
def business_overview(request: Request):
    biz = request.app.state.business
    try:
        bookings = biz.all_bookings()
        tickets = biz.all_tickets()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e
    return {
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
