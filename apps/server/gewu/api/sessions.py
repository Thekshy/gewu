"""会话路由（P22）：CRUD 五端点 + 历史恢复（均需登录，本人视角）。

- 创建/列表/改名/删除：chat_sessions 表的直接 CRUD 面（store 见 gewu/session）。
- DELETE 三处连带（任务书 Q4）：先删 checkpointer thread（跨连接非事务，悬空
  检查点无害——无入口可达）、再删 memory_episodic、最后删业务行；顺序不反。
- GET messages（任务书 Q2/硬点一）：从 checkpointer state 提取对话文本序列
  （对话级视图：工具调用轮与 ToolMessage 跳过）；classic 链路不写 messages，
  提取为空时退 memory_episodic（每轮 user/assistant 双条，链路无关）。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Body, HTTPException, Request
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage

from gewu.api.auth import require_user
from gewu.session.store import VALID_KINDS

router = APIRouter()

_TITLE_MAX = 60


def _session_payload(s) -> dict:
    return {
        "session_id": s.session_id,
        "title": s.title,
        "kind": s.kind,
        "created_at": s.created_at,
        "updated_at": s.updated_at,
    }


def _message_text(m: BaseMessage) -> str:
    """消息 content → 纯文本（兼容 str 与多模态 list[{text}] 两种形态）。"""
    c = m.content
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        return "".join(part.get("text", "") for part in c if isinstance(part, dict))
    return ""


def extract_dialog_messages(values: dict) -> list[dict]:
    """checkpointer state.values["messages"] → 对话级序列 [{role, text}]。

    过滤规则（任务书硬点一）：只取 HumanMessage 文本与 AIMessage 非空 text；
    工具调用轮（AI 无 text 只有 tool_calls）与其 ToolMessage 跳过；system 跳过。
    """
    out: list[dict] = []
    for m in values.get("messages") or []:
        if isinstance(m, HumanMessage):
            text = _message_text(m).strip()
            if text:
                out.append({"role": "user", "text": text})
        elif isinstance(m, AIMessage):
            text = _message_text(m).strip()
            if text:
                out.append({"role": "assistant", "text": text})
    return out


def _owned(request: Request, user_email: str, session_id: str):
    """归属校验（他人/不存在统一 404，防枚举）。"""
    s = request.app.state.sessions.get(user_email, session_id)
    if s is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    return s


@router.post("/api/sessions")
def create_session(request: Request, payload: Annotated[dict, Body(...)]):
    user = require_user(request)
    kind = payload.get("kind") or "chat"
    if kind not in VALID_KINDS:
        raise HTTPException(status_code=422, detail=f"kind 必须为 {'/'.join(VALID_KINDS)}")
    s = request.app.state.sessions.create(user.email, kind)
    return _session_payload(s)


@router.get("/api/sessions")
def list_sessions(request: Request, kind: str | None = None):
    user = require_user(request)
    if kind is not None and kind not in VALID_KINDS:
        raise HTTPException(status_code=422, detail=f"kind 必须为 {'/'.join(VALID_KINDS)}")
    return [_session_payload(s) for s in request.app.state.sessions.list_sessions(user.email, kind)]


@router.patch("/api/sessions/{session_id}")
def rename_session(session_id: str, request: Request, payload: Annotated[dict, Body(...)]):
    user = require_user(request)
    title = payload.get("title")
    if not isinstance(title, str):
        raise HTTPException(status_code=422, detail="请求体不是合法 JSON")
    title = title.strip()
    if not 1 <= len(title) <= _TITLE_MAX:
        raise HTTPException(status_code=422, detail=f"标题长度需在 1~{_TITLE_MAX} 字之间")
    _owned(request, user.email, session_id)
    s = request.app.state.sessions.rename(user.email, session_id, title)
    return _session_payload(s)


@router.delete("/api/sessions/{session_id}")
def delete_session(session_id: str, request: Request):
    user = require_user(request)
    _owned(request, user.email, session_id)
    # 连带顺序（任务书 Q4/§3）：先 checkpointer 后业务行——业务行删了 cp 删失败
    # = 悬空检查点（无害，无入口可达）；反过来会留「看似可用实则无历史」的会话。
    try:
        request.app.state.checkpointer.delete_thread(session_id)
    except Exception as e:  # noqa: BLE001 - 悬空检查点无害，继续删
        print(f"[sessions] checkpointer 删除失败（悬空无害，继续）：{e}", flush=True)
    request.app.state.memory.delete_episodes(session_id)
    request.app.state.sessions.delete(user.email, session_id)
    return {"status": "ok"}


@router.get("/api/sessions/{session_id}/messages")
def session_messages(session_id: str, request: Request):
    user = require_user(request)
    s = _owned(request, user.email, session_id)
    messages: list[dict] = []
    try:
        values = (
            request.app.state.graph.get_state({"configurable": {"thread_id": session_id}}).values
            or {}
        )
        messages = extract_dialog_messages(values)
    except Exception as e:  # noqa: BLE001 - 无 state 按空处理（新会话/未对话）
        print(f"[sessions] state 提取失败（按空处理）：{e}", flush=True)
    if not messages:
        # classic 链路（mode=classic/direct/research）不写 messages——退 episodic
        messages = request.app.state.memory.episodes_for_session(session_id)
    return {"session_id": s.session_id, "kind": s.kind, "messages": messages}
