"""POST /api/chat：SSE 流式问答（PARITY §2.4/§3 契约，移植 Go internal/api/chat.go）。

校验序列逐条对照；SSE 分帧 `data: {json}\n\n`（UTF-8 原文不转义）；done 事件
在流末单点发射（reason: completed/max_tokens/error/aborted——RunChat 单点语义）。
interrupt/resume 桥：thread 停在确认门（tx_gate interrupt）时，用户本轮消息作为
resume 值续跑——前端照常发 /api/chat，零改动。流结束后异步固化长期记忆。
"""

from __future__ import annotations

import json
import os
import threading
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


def _consolidate_async(request: Request, req: dict, question: str, answer: str) -> None:
    """会话结束后的记忆固化（Go consolidateAsync 等价）：线程内跑，不阻塞响应。"""
    memory = getattr(request.app.state, "memory", None)
    if memory is None:
        return

    def work():
        from gewu.memory import consolidate

        try:
            consolidate(
                memory,
                request.app.state.llm,
                f"demo-{req['role']}",
                req["session_id"],
                question,
                answer,
            )
        except Exception as e:  # noqa: BLE001 - 固化失败不影响主链路
            print(f"[agent] 记忆固化失败（不影响主链路）：{e}")

    threading.Thread(target=work, daemon=True).start()


@router.post("/api/chat")
def chat(request: Request, payload: Annotated[dict, Body(...)]):
    req = _parse_chat_body(payload)
    try:
        request.app.state.budget.ensure()
    except Exception as e:  # noqa: BLE001 - 预算耗尽 → 429（PARITY §2.4）
        from fastapi import HTTPException

        raise HTTPException(status_code=429, detail=str(e)) from e
    graph = request.app.state.graph
    config = {"configurable": {"thread_id": req["session_id"]}}
    state_input = new_state(
        req["question"], req["mode"], req["session_id"], req["role"], f"demo-{req['role']}"
    )

    # interrupt/resume 桥（Q4 原生机制）：thread 停在确认门时以用户消息 resume。
    from langgraph.types import Command

    run_input = state_input
    try:
        snap = graph.get_state(config)
        if snap.next:  # 停在 tx_gate interrupt（确认门）
            run_input = Command(resume=req["question"])
    except Exception:  # noqa: BLE001 - 状态读取失败按新会话处理
        pass

    def generate():
        t0 = time.monotonic()
        truncated = False
        answer = ""
        try:
            for stream_mode, chunk in graph.stream(
                run_input, config, stream_mode=["custom", "values"]
            ):
                if stream_mode == "custom":
                    yield _sse(chunk)
                else:
                    truncated = bool(chunk.get("truncated"))
                    answer = chunk.get("answer") or answer
        except GeneratorExit:
            raise  # 客户端断开：done 已无法送达（语义上记 aborted）
        except Exception as e:  # noqa: BLE001 - 链路错误 → error 事件 + done(error)
            yield _sse(ev.error_evt(str(e)))
            yield _sse(ev.done_evt(int((time.monotonic() - t0) * 1000), "error"))
            return
        reason = "max_tokens" if truncated else "completed"
        yield _sse(ev.done_evt(int((time.monotonic() - t0) * 1000), reason))
        if os.environ.get("MEMORY_CONSOLIDATE", "on") == "on":
            _consolidate_async(request, req, req["question"], answer)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
