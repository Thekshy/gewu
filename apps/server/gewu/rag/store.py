"""PG 知识库存储（P14-0 最小接线，只读）。

P12 已把检索读写收口到 PG 侧（docs/chunks/vectors 表 + rag_* 存储函数，DDL 见
Go internal/rag/schema.go），Go 与 Python 同为调用方；建库建表由 `make ingest`
完成，这里不执行 DDL。P14-1 再移植全量检索管线（FTS+向量+RRF）与测试库基建。
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Protocol

import psycopg


@dataclass(frozen=True)
class DocInfo:
    """/api/docs 列表项（字段名经 routes 层映射为 PARITY 的 snake_case）。"""

    doc_id: str
    title: str
    source: str
    updated: str
    chunks: int


@dataclass(frozen=True)
class Stats:
    """索引规模统计（/api/health）。"""

    docs: int
    chunks: int
    embedded: bool


class DocStore(Protocol):
    """api 层依赖的最小存储面（测试用 Fake 实现同一协议）。"""

    def list_docs(self) -> list[DocInfo]: ...

    def get_stats(self) -> Stats: ...


class Store:
    """PG 连接：惰性建立、单连接 + 锁（查询全程持锁，P14-1 换连接池）。"""

    def __init__(self, dsn: str) -> None:
        self._dsn = dsn
        self._conn: psycopg.Connection | None = None
        self._lock = threading.Lock()

    def _open(self) -> psycopg.Connection:
        """取连接（须持锁调用）。"""
        if self._conn is None or self._conn.closed:
            self._conn = psycopg.connect(self._dsn)
            self._conn.autocommit = True
        return self._conn

    def close(self) -> None:
        with self._lock:
            if self._conn is not None and not self._conn.closed:
                self._conn.close()
            self._conn = None

    def list_docs(self) -> list[DocInfo]:
        sql = """
            SELECT d.id, d.title, d.source, d.updated, COUNT(c.id) AS chunks
            FROM docs d LEFT JOIN chunks c ON c.doc_id = d.id
            GROUP BY d.id, d.title, d.source, d.updated
            ORDER BY d.id
        """
        with self._lock:
            with self._open().cursor() as cur:
                cur.execute(sql)
                rows = cur.fetchall()
        return [DocInfo(r[0], r[1], r[2], r[3], r[4]) for r in rows]

    def get_stats(self) -> Stats:
        with self._lock:
            with self._open().cursor() as cur:
                cur.execute("SELECT (SELECT COUNT(*) FROM docs), (SELECT COUNT(*) FROM chunks)")
                docs, chunks = cur.fetchone()
                cur.execute("SELECT EXISTS(SELECT 1 FROM vectors)")
                embedded = bool(cur.fetchone()[0])
        return Stats(docs=docs, chunks=chunks, embedded=embedded)
