"""记忆路由（P22）：memory_fact 用户可见可管（本人视角三端点）。

长期记忆从「黑盒增强」变「透明资产」：查看 / 新增(覆盖) / 删除。
防越权由 user_id=登录 email 保证（store 复合主键含 user_id）。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Body, HTTPException, Request

from gewu.api.auth import require_user
from gewu.memory import Fact

router = APIRouter()

VALID_FACT_KINDS = ("profile", "preference", "constraint")


def _fact_payload(f) -> dict:
    return {"kind": f.kind, "key": f.key, "value": f.value}


@router.get("/api/memory/facts")
def list_facts(request: Request):
    user = require_user(request)
    return [_fact_payload(f) for f in request.app.state.memory.all_facts(user.email)]


@router.post("/api/memory/facts")
def upsert_fact(request: Request, payload: Annotated[dict, Body(...)]):
    user = require_user(request)
    fact = _parse_fact(payload)
    request.app.state.memory.upsert_facts(user.email, [fact])
    return {"status": "ok"}


@router.delete("/api/memory/facts")
def delete_fact(request: Request, kind: str, key: str):
    user = require_user(request)
    if not _valid_kind(kind):
        raise HTTPException(status_code=422, detail=f"kind 必须为 {'/'.join(VALID_FACT_KINDS)}")
    if not request.app.state.memory.delete_fact(user.email, kind, key):
        raise HTTPException(status_code=404, detail="事实不存在")
    return {"status": "ok"}


def _valid_kind(kind: str) -> bool:
    return kind in VALID_FACT_KINDS


def _parse_fact(payload: dict) -> Fact:
    kind = payload.get("kind")
    key = payload.get("key")
    value = payload.get("value")
    if not isinstance(kind, str) or not isinstance(key, str) or not isinstance(value, str):
        raise HTTPException(status_code=422, detail="请求体不是合法 JSON")
    key, value = key.strip(), value.strip()
    if not _valid_kind(kind):
        raise HTTPException(status_code=422, detail=f"kind 必须为 {'/'.join(VALID_FACT_KINDS)}")
    if not 1 <= len(key) <= 60:
        raise HTTPException(status_code=422, detail="key 长度需在 1~60 字之间")
    if not 1 <= len(value) <= 500:
        raise HTTPException(status_code=422, detail="value 长度需在 1~500 字之间")
    return Fact(kind, key, value)
