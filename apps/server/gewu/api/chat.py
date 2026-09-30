"""POST /api/chat：SSE 流式问答（PARITY §2.4/§3 契约，移植 Go internal/api/chat.go）。

校验序列逐条对照；SSE 分帧 `data: {json}\n\n`（UTF-8 原文不转义）；done 事件
在流末单点发射（reason: completed/max_tokens/error/aborted——RunChat 单点语义）。
"""

from __future__ import annotations

import json
import time
from typing import Annotated

from fastapi import APIRouter, Body, HTTPException, Request
from fastapi.responses import StreamingResponse

from gewu.agent import events as ev
from gewu.agent.state import new_state

router = APIRouter()


def _parse_chat_body(payload: dict) -> dict:
    """请求校验（顺序对照 Go chat.go）。"""
    question = payload.get("question")
    if question is None:
        question = ""
    if not isinstance(question, str):
        raise HTTPException(status_code=422, detail="请求体不是合法 JSON")
    if question == "":
        raise HTTPException(status_code=422, detail="问题不能为空")
    if len(question) > 500:
        raise HTTPException(status_code=422, detail="问题过长")
    mode = payload.get("mode") or "auto"
    if mode not in ("auto", "direct", "research", "react"):
        raise HTTPException(status_code=422, detail="mode 必须为 auto/direct/research/react")
    role = payload.get("role") or "student"
    if role not in ("student", "counselor"):
        raise HTTPException(status_code=422, detail="role 必须为 student/counselor")
    session_id = payload.get("session_id") or "default"
    if not isinstance(session_id, str):
        raise HTTPException(status_code=422, detail="请求体不是合法 JSON")
    if len(session_id) > 64:
        raise HTTPException(status_code=422, detail="session_id 过长（上限 64 字符）")
    return {"question": question, "mode": mode, "role": role, "session_id": session_id}


def _sse(ev_: dict) -> str:
    return f"data: {json.dumps(ev_, ensure_ascii=False, separators=(',', ':'))}\n\n"


@router.post("/api/chat")
def chat(request: Request, payload: Annotated[dict, Body(...)]):
    req = _parse_chat_body(payload)
    graph = request.app.state.graph
    config = {"configurable": {"thread_id": req["session_id"]}}
    state_input = new_state(
        req["question"], req["mode"], req["session_id"], req["role"], f"demo-{req['role']}"
    )

    def generate():
        t0 = time.monotonic()
        truncated = False
        try:
            for stream_mode, chunk in graph.stream(
                state_input, config, stream_mode=["custom", "values"]
            ):
                if stream_mode == "custom":
                    yield _sse(chunk)
                else:
                    truncated = bool(chunk.get("truncated"))
        except GeneratorExit:
            raise  # 客户端断开：done 已无法送达（语义上记 aborted）
        except Exception as e:  # noqa: BLE001 - 链路错误 → error 事件 + done(error)
            yield _sse(ev.error_evt(str(e)))
            yield _sse(ev.done_evt(int((time.monotonic() - t0) * 1000), "error"))
            return
        reason = "max_tokens" if truncated else "completed"
        yield _sse(ev.done_evt(int((time.monotonic() - t0) * 1000), reason))

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
