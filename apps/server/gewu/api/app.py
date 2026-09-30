"""FastAPI 应用工厂：装配 settings/store/llm/retriever + 路由 + CORS。

独立于 main.py，供测试以自定义依赖构建应用（对齐 Go internal/api 可脱离 main 测试）。
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from langgraph.checkpoint.memory import MemorySaver

from gewu.agent.graph import build_graph
from gewu.api import chat as chat_routes
from gewu.api import routes
from gewu.budget import TokenBudget
from gewu.business.db import Business
from gewu.config import VERSION, Settings
from gewu.llm.service import LLMService
from gewu.memory import MemoryStore
from gewu.middleware import RateLimitMiddleware, TraceIDMiddleware
from gewu.rag.retrieve import LLMReranker, Retriever
from gewu.rag.store import DocStore, Store


def _make_checkpointer(settings: Settings):
    """PostgresSaver（Q4 原生机制：会话/办理流程跨重启持久化）；PG 不可用退内存版。

    官方要求 autocommit 连接（checkpointer 内部自管事务），不接受 DSN 字符串。
    """
    try:
        import psycopg
        from langgraph.checkpoint.postgres import PostgresSaver

        conn = psycopg.connect(settings.pg_dsn, autocommit=True)
        cp = PostgresSaver(conn)
        cp.setup()
        print(f"[app] checkpointer=PostgresSaver dsn={settings.pg_dsn.split('@')[-1]}", flush=True)
        return cp
    except Exception as e:  # noqa: BLE001
        import traceback

        print(f"[app] PostgresSaver 不可用（{e}），退内存版 checkpointer", flush=True)
        traceback.print_exc()
        return MemorySaver()


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
    # 中间件顺序（外→内）：限流 → trace-id → CORS（对齐 Go：TraceID → 限流 → CORS）
    app.add_middleware(
        CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"]
    )
    app.add_middleware(TraceIDMiddleware)
    app.add_middleware(RateLimitMiddleware, per_minute=settings.rate_limit_per_minute)

    # 校验类异常统一收口为 PARITY 风格错误体（对齐 Go ShouldBindJSON 的 detail 语义）
    @app.exception_handler(RequestValidationError)
    async def _validation_error_handler(request: Request, exc: RequestValidationError):
        return JSONResponse(status_code=422, content={"detail": "请求体不是合法 JSON"})

    app.state.settings = settings
    app.state.store = store if store is not None else Store(settings.pg_dsn)
    app.state.business = (
        business if business is not None else Business(settings.data_dir / "business.db")
    )
    app.state.budget = TokenBudget(settings.data_dir / "usage.json", settings.daily_token_budget)
    app.state.llm = llm if llm is not None else LLMService(settings, budget=app.state.budget)
    if retriever is not None:
        app.state.retriever = retriever
    elif isinstance(app.state.store, Store):
        app.state.retriever = _build_retriever(settings, app.state.store, app.state.llm)
    else:
        raise TypeError("store 非 Store 实例时必须显式提供 retriever")
    app.state.memory = MemoryStore(settings.data_dir / "memory.db")
    app.state.checkpointer = _make_checkpointer(settings)
    app.state.graph = build_graph(
        settings,
        app.state.retriever,
        app.state.llm,
        business=app.state.business,
        checkpointer=app.state.checkpointer,
        memory=app.state.memory,
    )
    app.include_router(routes.router)
    app.include_router(chat_routes.router)
    return app
