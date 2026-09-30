"""FastAPI middleware：trace-id（uuid v4 响应头）+ 固定窗口限流（对齐 Go internal/middleware）。"""

from __future__ import annotations

import threading
import time
import uuid

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

RATE_LIMIT_DEFAULT = 600  # 缺省与 Go/.env 演示口径一致


def client_ip(request: Request) -> str:
    """限流键：X-Forwarded-For 首段（反代场景）或直连 IP。"""
    fwd = request.headers.get("x-forwarded-for", "")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


class TraceIDMiddleware(BaseHTTPMiddleware):
    """每请求 uuid v4（入站头有则沿用），响应头 X-Trace-Id 透出。"""

    async def dispatch(self, request: Request, call_next):
        trace = request.headers.get("x-trace-id") or uuid.uuid4().hex
        response = await call_next(request)
        response.headers["X-Trace-Id"] = trace
        return response


class RateLimiter:
    """固定窗口每分钟限流（进程内；Go 版同语义）。"""

    def __init__(self, per_minute: int = RATE_LIMIT_DEFAULT) -> None:
        self._limit = max(1, per_minute)
        self._window_start = 0.0
        self._counts: dict[str, int] = {}
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        with self._lock:
            if now - self._window_start >= 60.0:
                self._window_start = now
                self._counts = {}
            n = self._counts.get(key, 0)
            if n >= self._limit:
                return False
            self._counts[key] = n + 1
            return True


class RateLimitMiddleware(BaseHTTPMiddleware):
    """超限 429 {"detail": ...}（PARITY 错误体）。健康检查不限流（探活语义）。"""

    def __init__(self, app, per_minute: int = RATE_LIMIT_DEFAULT) -> None:
        super().__init__(app)
        self._limiter = RateLimiter(per_minute)

    async def dispatch(self, request: Request, call_next):
        if request.url.path != "/api/health" and not self._limiter.allow(client_ip(request)):
            return JSONResponse(status_code=429, content={"detail": "请求过于频繁，请稍后再试"})
        return await call_next(request)
