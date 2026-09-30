"""FastAPI 应用工厂：装配 settings/store/business + 路由 + CORS。

独立于 main.py，供测试以自定义依赖构建应用（对齐 Go internal/api 可脱离 main 测试）。
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from gewu.api import routes
from gewu.business.db import Business
from gewu.config import VERSION, Settings
from gewu.rag.store import DocStore, Store


def create_app(
    settings: Settings,
    store: DocStore | None = None,
    business: Business | None = None,
) -> FastAPI:
    app = FastAPI(title="gewu", version=VERSION)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],  # 公开 demo，对齐 Go 版 cors()
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.state.settings = settings
    app.state.store = store if store is not None else Store(settings.pg_dsn)
    app.state.business = (
        business if business is not None else Business(settings.data_dir / "business.db")
    )
    app.include_router(routes.router)
    return app
