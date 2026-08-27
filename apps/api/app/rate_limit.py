"""按 IP 的令牌桶限流中间件（公开 demo 的第一道防线）。"""

import time

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse


class RateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, limit_per_minute: int = 20):
        super().__init__(app)
        self.rate = float(max(1, limit_per_minute))
        # ip -> (剩余令牌, 上次刷新时间)
        self.buckets: dict[str, tuple[float, float]] = {}

    def _client_ip(self, request) -> str:
        fwd = request.headers.get("x-forwarded-for", "")
        if fwd:
            return fwd.split(",")[0].strip()
        return request.client.host if request.client else "unknown"

    async def dispatch(self, request, call_next):
        ip = self._client_ip(request)
        now = time.monotonic()
        tokens, last = self.buckets.get(ip, (self.rate, now))
        tokens = min(self.rate, tokens + (now - last) * self.rate / 60.0)
        if tokens < 1.0:
            retry = int((1.0 - tokens) * 60.0 / self.rate) + 1
            return JSONResponse(
                {"detail": f"请求过于频繁，请 {retry} 秒后重试"},
                status_code=429,
                headers={"Retry-After": str(retry)},
            )
        self.buckets[ip] = (tokens - 1.0, now)
        response = await call_next(request)
        response.headers.setdefault("X-RateLimit-Limit-Minute", str(int(self.rate)))
        return response
