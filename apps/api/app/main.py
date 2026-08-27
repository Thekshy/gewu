"""格物 API 入口：SSE 问答 / 调试检索 / 文档列表 / 健康检查。"""

from __future__ import annotations

import json

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from . import __version__, llm
from .agent.pipeline import get_retriever, run_chat
from .agent.tools import business
from .budget import BudgetExceeded, budget
from .config import get_settings
from .rate_limit import RateLimitMiddleware
from .schemas import ChatRequest, SearchRequest

settings = get_settings()

app = FastAPI(
    title="格物 Gewu API",
    version=__version__,
    description="高校场景 Deep Research 智能问答系统（演示数据为虚构的「钱塘大学」）",
)
app.add_middleware(RateLimitMiddleware, limit_per_minute=settings.rate_limit_per_minute)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
def health():
    store = get_retriever().store
    stats = store.stats()
    return {
        "status": "ok",
        "version": __version__,
        "llm": llm.has_key(),
        "embeddings": llm.has_key() and stats["embedded"],
        "docs": stats["docs"],
        "chunks": stats["chunks"],
        "budget": {"used": budget.used(), "limit": budget.daily_limit},
    }


@app.get("/api/docs")
def list_docs():
    return get_retriever().store.list_docs()


@app.post("/api/search")
def search(req: SearchRequest):
    """调试用：直接看混合检索命中了什么。"""
    hits = get_retriever().search(req.query, k=req.k)
    return [
        {
            "doc_id": h.doc_id,
            "title": h.title,
            "source": h.source,
            "seq": h.seq,
            "text": h.text[:300],
        }
        for h in hits
    ]


@app.post("/api/chat")
def chat(req: ChatRequest):
    if len(req.question) > settings.max_question_chars:
        raise HTTPException(422, "问题过长")
    try:
        budget.ensure()
    except BudgetExceeded as exc:
        raise HTTPException(429, str(exc)) from exc

    def gen():
        for event in run_chat(req.question, req.mode, req.session_id, req.role):
            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/api/business/reset")
def business_reset():
    """清空 mock 业务数据（演示/评测用）。"""
    business.reset()
    return {"status": "ok"}


@app.get("/api/business/overview")
def business_overview():
    """调试：查看当前预约与请假单。"""
    return {"bookings": business.all_bookings(), "tickets": business.all_tickets()}
