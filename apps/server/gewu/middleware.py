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
    """限流键：X-Forwarded-For 末段（反代场景）或直连 IP。

    nginx $proxy_add_x_forwarded_for 是「请求带入值 + $remote_addr」追加式，
    末段=连上 nginx 的真实来源；首段是浏览器可携带的伪造值（P36 安全审计：
    取首段时每请求换一个伪造 XFF 即可绕过限流）。拓扑前提=单层可信反代，
    前面再加 CDN/二层代理时须回头调整。
    """
    fwd = request.headers.get("x-forwarded-for", "")
    if fwd:
        return fwd.split(",")[-1].strip()
    return request.client.host if request.client else "unknown"


class TraceIDMiddleware(BaseHTTPMiddleware):
    """每请求 uuid v4（入站头有则沿用），响应头 X-Trace-Id 透出。"""

    async def dispatch(self, request: Request, call_next):
        trace = request.headers.get("x-trace-id") or uuid.uuid4().hex
        response = await call_next(request)
        response.headers["X-Trace-Id"] = trace
        return response


class RateLimiter:
    """固定窗口限流（进程内；Go 版同语义）。缺省每分钟；window_sec 可调（如 P39 游客签发日闸）。"""

    def __init__(self, per_minute: int = RATE_LIMIT_DEFAULT, window_sec: float = 60.0) -> None:
        self._limit = max(1, per_minute)
        self._window = float(window_sec)
        self._window_start = 0.0
        self._counts: dict[str, int] = {}
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        with self._lock:
            if now - self._window_start >= self._window:
                self._window_start = now
                self._counts = {}
            n = self._counts.get(key, 0)
            if n >= self._limit:
                return False
            self._counts[key] = n + 1
            return True

    def reset(self) -> None:
        """清空计数（测试隔离：模块级 login 限速器跨用例复位）。"""
        with self._lock:
            self._counts = {}
            self._window_start = 0.0


class RateLimitMiddleware(BaseHTTPMiddleware):
    """超限 429 {"detail": ...}（PARITY 错误体）。健康检查不限流（探活语义）。"""

    def __init__(self, app, per_minute: int = RATE_LIMIT_DEFAULT) -> None:
        super().__init__(app)
        self._limiter = RateLimiter(per_minute)

    async def dispatch(self, request: Request, call_next):
        if request.url.path != "/api/health" and not self._limiter.allow(client_ip(request)):
            return JSONResponse(status_code=429, content={"detail": "请求过于频繁，请稍后再试"})
        return await call_next(request)
