"""FastAPI 应用工厂：装配 settings/store/llm/retriever + 路由 + CORS。

独立于 main.py，供测试以自定义依赖构建应用（对齐 Go internal/api 可脱离 main 测试）。
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from gewu.agent.graph import build_graph
from gewu.api import chat as chat_routes
from gewu.api import routes
from gewu.business.db import Business
from gewu.config import VERSION, Settings
from gewu.llm.service import LLMService
from gewu.rag.retrieve import LLMReranker, Retriever
from gewu.rag.store import DocStore, Store


def _build_retriever(settings: Settings, store: Store, llm: LLMService) -> Retriever:
    reranker = LLMReranker(llm) if settings.rerank_mode != "off" else None
    return Retriever(store, settings.retrieval_k, llm, reranker=reranker)


def create_app(
    settings: Settings,
    store: DocStore | None = None,
    business: Business | None = None,
    llm: LLMService | None = None,
    retriever: Retriever | None = None,
) -> FastAPI:
    app = FastAPI(title="gewu", version=VERSION)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],  # 公开 demo，对齐 Go 版 cors()
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # 校验类异常统一收口为 PARITY 风格错误体（对齐 Go ShouldBindJSON 的 detail 语义）
    @app.exception_handler(RequestValidationError)
    async def _validation_error_handler(request: Request, exc: RequestValidationError):
        return JSONResponse(status_code=422, content={"detail": "请求体不是合法 JSON"})

    app.state.settings = settings
    app.state.store = store if store is not None else Store(settings.pg_dsn)
    app.state.business = (
        business if business is not None else Business(settings.data_dir / "business.db")
    )
    app.state.llm = llm if llm is not None else LLMService(settings)
    if retriever is not None:
        app.state.retriever = retriever
    elif isinstance(app.state.store, Store):
        app.state.retriever = _build_retriever(settings, app.state.store, app.state.llm)
    else:
        raise TypeError("store 非 Store 实例时必须显式提供 retriever")
    app.state.graph = build_graph(settings, app.state.retriever, app.state.llm)
    app.include_router(routes.router)
    app.include_router(chat_routes.router)
    return app
