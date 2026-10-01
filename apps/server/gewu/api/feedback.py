"""POST /api/feedback（P25-2）：消息级 👍/👎 落库（america.gov Good/Bad response 同款）。

登录 + 会话归属校验（他人/不存在统一 404，与 chat/sessions 同口径防枚举）；
upsert 覆盖语义——同一轮改主意 good↔bad 不产生双行；成功 204。无 GET，
admin 侧聚合分析留给 P23 台账后续观察票。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Body, HTTPException, Request

from gewu.api.auth import require_user

router = APIRouter()

_QUESTION_MAX = 500  # 与 chat 校验同限


@router.post("/api/feedback", status_code=204)
def submit_feedback(request: Request, payload: Annotated[dict, Body(...)]):
    user = require_user(request)
    session_id = payload.get("session_id")
    question = payload.get("question")
    rating = payload.get("rating")
    if not isinstance(session_id, str) or not session_id:
        raise HTTPException(status_code=422, detail="session_id 不能为空")
    if not isinstance(question, str) or not question.strip():
        raise HTTPException(status_code=422, detail="question 不能为空")
    if len(question) > _QUESTION_MAX:
        raise HTTPException(status_code=422, detail=f"question 过长（上限 {_QUESTION_MAX} 字）")
    if rating not in ("good", "bad"):
        raise HTTPException(status_code=422, detail="rating 必须为 good/bad")
    if request.app.state.sessions.get(user.email, session_id) is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    store = getattr(request.app.state, "feedback", None)
    if store is None:
        raise HTTPException(status_code=503, detail="反馈存储暂不可用")
    store.upsert(user.email, session_id, question.strip(), rating)
