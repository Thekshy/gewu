"""第一方链路追踪（P27）：trace=一轮对话、span=轮内一步，落 PG 两表。

动机（docs/runbooks/P27-first-party-tracing.md §背景）：print 埋点散/落不了
库/正则聚合脆——观测升级为系统性资产。第一消费者是 AI 排障（第一消费面
scripts/trace-query.sh），admin 可视化与 feedback join 列 B 期。

语义对齐 OTel GenAI / OpenInference 约定（将来 OTLP exporter 的映射表）：
  kind="llm"        ↔ span.kind INTERNAL + gen_ai.*
  kind="tool"       ↔ span.kind CLIENT  + tool.name/input/output
  name              ↔ gen_ai.request.model / tool.name
  tokens            ↔ gen_ai.usage.total_tokens
  input/output      ↔ input.value / output.value（output 截断 OUTPUT_LIMIT）

写入路径：Tracer 经 contextvar 作用域（chat.py 每轮 start、流式迭代内每次
next 前 re-set——P23 教训：SSE sync 迭代跨 Context，set 一次只活第一次）；
span 内存 buffer，finish 轮末一次 batch INSERT。观测永不杀业务：store
软降级（PG 不可达→None→no-op tracer），落库异常静默打 [obs] 一行。

支撑域纪律：本模块禁依赖 agent/rag/business（lint-arch 守护）；print 层
（journalctl）全保留，本表是机读真相源，两者由 chat.py turn_log 同源产出。
"""

from __future__ import annotations

import time
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any

OUTPUT_LIMIT = 4096  # output JSONB 字符截断（input 不截，args 本就是小字段）
TEXT_SNIPPET = 512  # output.content 预览截断

# 当前轮的 tracer；None=观测关闭（chat 外/线程外/软降级），None-safe 取用。
_current_tracer: ContextVar[Tracer | None] = ContextVar("gewu_tracer", default=None)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS agent_trace (
    id BIGSERIAL PRIMARY KEY,
    session_id TEXT NOT NULL,
    "user" TEXT NOT NULL,
    role TEXT NOT NULL,
    mode TEXT NOT NULL,
    question TEXT NOT NULL,
    route TEXT NOT NULL DEFAULT '',
    route_layer TEXT NOT NULL DEFAULT '',
    reason TEXT NOT NULL DEFAULT '',
    latency_ms INTEGER NOT NULL DEFAULT 0,
    steps INTEGER NOT NULL DEFAULT 0,
    answer_head TEXT NOT NULL DEFAULT '',
    error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS agent_span (
    id BIGSERIAL PRIMARY KEY,
    trace_id BIGINT NOT NULL REFERENCES agent_trace(id) ON DELETE CASCADE,
    seq INTEGER NOT NULL,
    kind TEXT NOT NULL,
    name TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'ok',
    latency_ms INTEGER NOT NULL DEFAULT 0,
    tokens INTEGER,
    input JSONB,
    output JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_trace_session ON agent_trace(session_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_trace_error ON agent_trace(error) WHERE error IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_span_trace ON agent_span(trace_id, seq);
"""


def current_tracer() -> Tracer | None:
    """当前轮 tracer（None-safe：观测关闭时调用方零成本直通）。"""
    return _current_tracer.get()


def set_current_tracer(tracer: Tracer | None) -> None:
    _current_tracer.set(tracer)


def clip(value: Any, limit: int = TEXT_SNIPPET) -> Any:
    """递归截断字符串字段（输出摘要防体积膨胀；非字符串原样）。"""
    if isinstance(value, str):
        return value if len(value) <= limit else value[:limit] + "…"
    if isinstance(value, dict):
        return {k: clip(v, limit) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [clip(v, limit) for v in value]
    return value


@dataclass
class _SpanRec:
    """内存态 span 记录（finish 时批量落库）。"""

    seq: int
    kind: str
    name: str
    status: str = "ok"
    latency_ms: int = 0
    tokens: int | None = None
    input: dict | None = None
    output: dict | None = field(default=None)


class Tracer:
    """一轮对话的观测聚合器：span() 包裹任意代码段，finish() 落库。"""

    def __init__(self, store: TracerStore | None, meta: dict[str, str]) -> None:
        self._store = store
        self._meta = meta
        self._spans: list[_SpanRec] = []
        self._seq = 0
        self.finished = False

    @contextmanager
    def span(self, kind: str, name: str, *, input: dict | None = None):  # noqa: A002
        """计时上下文：正常退出记 ok；异常记 error 并上抛（观测不改语义）。"""
        self._seq += 1
        rec = _SpanRec(seq=self._seq, kind=kind, name=name, input=clip(input) if input else input)
        t0 = time.monotonic()
        try:
            yield rec
        except Exception as e:  # noqa: BLE001
            rec.status = "error"
            rec.output = {"error": str(e)[:TEXT_SNIPPET]}
            raise
        finally:
            rec.latency_ms = int((time.monotonic() - t0) * 1000)
            self._spans.append(rec)

    def finish(
        self,
        *,
        route: str,
        route_layer: str,
        reason: str,
        latency_ms: int,
        steps: int,
        answer_head: str,
        error: str | None = None,
    ) -> None:
        """轮末落库（trace 行 + spans 批量；store 为 None / 已 finish 时 no-op）。

        latency_ms 为整轮墙钟（调用方计时），非 span 累加——并行 span 会重复计。
        """
        self.finished = True
        if self._store is None:
            return
        try:
            trace_id = self._store.write_trace(
                self._meta,
                route=route,
                route_layer=route_layer,
                reason=reason,
                latency_ms=latency_ms,
                steps=steps,
                answer_head=answer_head,
                error=error,
            )
            self._store.write_spans(trace_id, self._spans)
        except Exception as e:  # noqa: BLE001 - 落库失败静默（观测永不杀业务）
            print(f"[obs] trace 落库失败（静默）：{e}", flush=True)

    @property
    def spans(self) -> list[_SpanRec]:
        """测试只读口。"""
        return list(self._spans)


class TracerStore:
    """trace/span 两表的 PG 写入（psycopg 短连接即可：轮末一次，量微）。"""

    def __init__(self, dsn: str) -> None:
        import psycopg  # noqa: PLC0415

        self._dsn = dsn
        with psycopg.connect(dsn, connect_timeout=5, autocommit=True) as conn:
            conn.execute(_SCHEMA)

    def write_trace(
        self,
        meta: dict[str, str],
        *,
        route: str,
        route_layer: str,
        reason: str,
        latency_ms: int,
        steps: int,
        answer_head: str,
        error: str | None,
    ) -> int:
        import psycopg  # noqa: PLC0415

        with psycopg.connect(self._dsn, autocommit=True) as conn:
            row = conn.execute(
                'INSERT INTO agent_trace (session_id, "user", role, mode, question,'
                " route, route_layer, reason, latency_ms, steps, answer_head, error)"
                " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
                (
                    meta["session_id"],
                    meta["user"],
                    meta["role"],
                    meta["mode"],
                    meta["question"],
                    route,
                    route_layer,
                    reason,
                    int(latency_ms),
                    int(steps),
                    answer_head,
                    error,
                ),
            ).fetchone()
        return int(row[0])

    def write_spans(self, trace_id: int, spans: list[_SpanRec]) -> None:
        import json  # noqa: PLC0415

        import psycopg  # noqa: PLC0415

        rows = [
            (
                trace_id,
                s.seq,
                s.kind,
                s.name,
                s.status,
                s.latency_ms,
                s.tokens,
                json.dumps(s.input, ensure_ascii=False) if s.input is not None else None,
                json.dumps(clip(s.output, OUTPUT_LIMIT), ensure_ascii=False)
                if s.output is not None
                else None,
            )
            for s in spans
        ]
        with psycopg.connect(self._dsn, autocommit=True) as conn:
            # psycopg3 的 executemany 在 cursor 不在 connection；行数个位级，
            # 逐行 execute 等价且少一层 API 面
            for row in rows:
                conn.execute(
                    "INSERT INTO agent_span (trace_id, seq, kind, name, status,"
                    " latency_ms, tokens, input, output) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    row,
                )

    def wipe(self) -> None:
        """测试专用：清两表（子表先删，CASCADE 亦然但显式更稳）。"""
        import psycopg  # noqa: PLC0415

        with psycopg.connect(self._dsn, autocommit=True) as conn:
            conn.execute("DELETE FROM agent_span")
            conn.execute("DELETE FROM agent_trace")


def make_trace_store(dsn: str) -> TracerStore | None:
    """装配工厂：可达建表返回 store，PG 不可达退 None（观测软降级）。

    与 make_usage_store 同款：观测是增强不是依赖——测试环境/PG 抖动时
    全链 no-op tracer，主链路照常。
    """
    try:
        return TracerStore(dsn)
    except Exception as e:  # noqa: BLE001 - 不可达即软降级
        print(f"[app] TracerStore 不可用（链路观测禁用）：{e}", flush=True)
        return None
