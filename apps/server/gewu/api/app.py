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
from gewu.api import admin as admin_routes
from gewu.api import auth as auth_routes
from gewu.api import chat as chat_routes
from gewu.api import feedback as feedback_routes
from gewu.api import memory as memory_routes
from gewu.api import routes
from gewu.api import sessions as session_routes
from gewu.auth.store import AuthStore
from gewu.budget import TokenBudget
from gewu.business.db import Business
from gewu.config import VERSION, Settings
from gewu.llm.service import LLMService
from gewu.memory import MemoryStore
from gewu.middleware import RateLimitMiddleware, TraceIDMiddleware
from gewu.obs import make_trace_store
from gewu.rag.retrieve import LLMReranker, Retriever
from gewu.rag.store import DocStore, Store
from gewu.session.store import SessionStore, make_feedback_store
from gewu.usage import make_usage_store


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
    return Retriever(
        store,
        settings.retrieval_k,
        llm,
        reranker=reranker,
        pool_n=settings.retrieval_pool_n,
        rrf_k=settings.rrf_k,
        vector_weight=settings.rrf_vector_weight,
        keyword_weight=settings.rrf_keyword_weight,
        rerank_threshold=settings.rerank_threshold,
    )


# usage 参数哨兵：区分「未传」（探测式软降级建 UsageStore）与「显式 None」
# （强制禁用 per-user 记账，测试替身用）；注入真 store 则直接采用。
_UNSET = object()


def create_app(
    settings: Settings,
    store: DocStore | None = None,
    business: Business | None = None,
    llm: LLMService | None = None,
    retriever: Retriever | None = None,
    memory: MemoryStore | None = None,
    auth: AuthStore | None = None,
    sessions: SessionStore | None = None,
    feedback=None,
    usage=_UNSET,
    trace=_UNSET,
    checkpointer=None,
) -> FastAPI:
    app = FastAPI(title="gewu", version=VERSION)
    # 中间件顺序（外→内）：限流 → trace-id → CORS（对齐 Go：TraceID → 限流 → CORS）。
    # P21 起 CORS 收白名单（空=仅同源；前端经 next rewrite 同源代理）+ credentials。
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.cors_origins),
        allow_methods=["*"],
        allow_headers=["*"],
        allow_credentials=True,
    )
    app.add_middleware(TraceIDMiddleware)
    app.add_middleware(RateLimitMiddleware, per_minute=settings.rate_limit_per_minute)

    # 校验类异常统一收口为 PARITY 风格错误体（对齐 Go ShouldBindJSON 的 detail 语义）
    @app.exception_handler(RequestValidationError)
    async def _validation_error_handler(request: Request, exc: RequestValidationError):
        return JSONResponse(status_code=422, content={"detail": "请求体不是合法 JSON"})

    app.state.settings = settings
    app.state.store = store if store is not None else Store(settings.pg_dsn)
    # P21-2：business/memory 自 SQLite 迁 PG；P21-1：auth 域入库
    app.state.business = business if business is not None else Business(settings.pg_dsn)
    app.state.budget = TokenBudget(settings.data_dir / "usage.json", settings.daily_token_budget)
    # P23：per-user 用量账——缺省探测式软降级（make_usage_store：PG 不可达退
    # None 禁用，全局闸兜底）；显式传 store（admin/限额测试）或 None（强制禁用）
    app.state.usage = make_usage_store(settings.pg_dsn) if usage is _UNSET else usage
    # P27：链路观测——缺省探测式软降级（make_trace_store：PG 不可达退 None，
    # 全链 no-op tracer）；显式 None=强制禁用（测试替身用）
    app.state.trace_store = make_trace_store(settings.pg_dsn) if trace is _UNSET else trace
    app.state.llm = (
        llm
        if llm is not None
        else LLMService(settings, budget=app.state.budget, usage=app.state.usage)
    )
    if retriever is not None:
        app.state.retriever = retriever
    elif isinstance(app.state.store, Store):
        app.state.retriever = _build_retriever(settings, app.state.store, app.state.llm)
    else:
        raise TypeError("store 非 Store 实例时必须显式提供 retriever")
    app.state.memory = memory if memory is not None else MemoryStore(settings.pg_dsn)
    app.state.auth = auth if auth is not None else AuthStore(settings.pg_dsn)
    # P22：会话域（chat 归属校验/CRUD）；checkpointer 可注入（测试断言连带删除行数）
    app.state.sessions = sessions if sessions is not None else SessionStore(settings.pg_dsn)
    # P25-2：消息反馈——探测式软降级（PG 不可达 None → 端点 503，不拦主链路）
    app.state.feedback = feedback if feedback is not None else make_feedback_store(settings.pg_dsn)
    app.state.checkpointer = (
        checkpointer if checkpointer is not None else _make_checkpointer(settings)
    )
    app.state.graph = build_graph(
        settings,
        app.state.retriever,
        app.state.llm,
        business=app.state.business,
        checkpointer=app.state.checkpointer,
    )
    app.include_router(auth_routes.router)
    app.include_router(routes.router)
    app.include_router(chat_routes.router)
    app.include_router(session_routes.router)
    app.include_router(memory_routes.router)
    app.include_router(admin_routes.router)
    app.include_router(feedback_routes.router)
    return app
