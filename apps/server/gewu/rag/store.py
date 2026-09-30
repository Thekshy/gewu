"""PG 知识库存储：FTS + pgvector halfvec HNSW（移植自 Go internal/rag/store.go）。

P12 起读写收口 PG 存储函数（rag_fts_search / rag_upsert_doc），Go 与 Python 同为
调用方。查询向量经 L2 归一化后以文本 cast `::halfvec` 传入（与 rag_upsert_doc 的
JSONB vec 契约同款，保持单一向量传递路径）。
"""

from __future__ import annotations

import json
import math
import struct
import threading
from dataclasses import dataclass
from typing import Protocol

from psycopg_pool import ConnectionPool

from gewu.rag.schema import ensure_schema

EmbedDim = 2048


class MissingVectorsError(Exception):
    """索引无向量（-no-embed 入库 / 旧索引未重建）时检索的明确失败（不静默降级）。"""


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


@dataclass(frozen=True)
class Scored:
    """带分数的 chunk id（BM25 / 向量两路召回的中间形态）。"""

    id: int
    score: float


@dataclass(frozen=True)
class ChunkRow:
    """chunk 基本行（含父子块字段；flat 模式 ParentID 为空串）。"""

    id: int
    doc_id: str
    seq: int
    text: str
    parent_id: str
    section_path: str
    is_parent: bool


@dataclass(frozen=True)
class DocMeta:
    """文档元信息。"""

    title: str
    source: str
    updated: str


@dataclass(frozen=True)
class Hit:
    """检索命中（hierarchical 模式下为父块文本）。"""

    chunk_id: int
    doc_id: str
    seq: int
    text: str
    title: str
    source: str
    section_path: str


class DocStore(Protocol):
    """api 层依赖的最小存储面（测试用 Fake 实现同一协议）。"""

    def list_docs(self) -> list[DocInfo]: ...

    def get_stats(self) -> Stats: ...


_CHUNK_COLS = "id, doc_id, seq, text, COALESCE(parent_id, ''), section_path, is_parent"


def _to_f32(x: float) -> float:
    """float32 舍入（对齐 Go toFloat32 → halfvec 前的精度路径）。"""
    return struct.unpack("<f", struct.pack("<f", x))[0]


def normalized_query_vector(vec: list[float]) -> str:
    """L2 归一化 + float32 舍入，序列化为 halfvec 文本入参。"""
    if len(vec) != EmbedDim:
        raise ValueError(f"查询向量维度 {len(vec)} 与 halfvec({EmbedDim}) 不符")
    norm = math.sqrt(sum(x * x for x in vec))
    q = [_to_f32(x) for x in vec]
    if norm > 0:
        inv = _to_f32(1.0 / norm)
        q = [_to_f32(x * inv) for x in q]
    return "[" + ",".join(repr(x) for x in q) + "]"


def rrf_fuse(rank_lists: list[list[int]], k: int = 60) -> list[int]:
    """Reciprocal Rank Fusion：多路召回的排名融合。

    平分时按首次出现顺序（对齐 Go map 插入序 + 稳定排序语义）。
    """
    scores: dict[int, float] = {}
    first_seen: dict[int, int] = {}
    order = 0
    for lst in rank_lists:
        for rank, cid in enumerate(lst):
            if cid not in scores:
                first_seen[cid] = order
                order += 1
                scores[cid] = 0.0
            scores[cid] += 1.0 / (k + rank + 1)
    return sorted(scores, key=lambda cid: (-scores[cid], first_seen[cid]))


class Store:
    """PG 连接池封装：知识库读写（检索两路 + 行取 + upsert）。"""

    def __init__(self, dsn: str, *, ensure: bool = False) -> None:
        self._dsn = dsn
        self._lock = threading.Lock()
        self._pool = ConnectionPool(dsn, min_size=1, max_size=4, open=True, name="gewu-rag")
        if ensure:
            with self._pool.connection() as conn:
                ensure_schema(conn)

    def close(self) -> None:
        self._pool.close()

    # ---------- 关键词检索（PG 原生 FTS，分词下沉存储函数） ----------

    def bm25_search(self, query: str, k: int) -> list[Scored]:
        if not query or k <= 0:
            return []
        with self._pool.connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT id, score FROM rag_fts_search(%s, %s, %s)", ("simple", query, k))
            rows = cur.fetchall()
        return [Scored(int(r[0]), float(r[1])) for r in rows]

    # ---------- 向量检索（pgvector halfvec HNSW） ----------

    def vector_search(self, query_vec: list[float], k: int) -> list[Scored]:
        if not query_vec or k <= 0:
            return []
        qtext = normalized_query_vector(query_vec)
        sql = """
            SELECT chunk_id, 1 - (embedding <=> %s::halfvec) AS score
            FROM vectors
            ORDER BY embedding <=> %s::halfvec, chunk_id ASC
            LIMIT %s
        """
        with self._pool.connection() as conn, conn.cursor() as cur:
            cur.execute(sql, (qtext, qtext, k))
            rows = cur.fetchall()
        return [Scored(int(r[0]), float(r[1])) for r in rows]

    # ---------- 读取 ----------

    def chunk_rows(self, ids: list[int]) -> dict[int, ChunkRow]:
        if not ids:
            return {}
        with self._pool.connection() as conn, conn.cursor() as cur:
            cur.execute(f"SELECT {_CHUNK_COLS} FROM chunks WHERE id = ANY(%s)", (list(ids),))
            rows = cur.fetchall()
        return {int(r[0]): _chunk_row(r) for r in rows}

    def parent_rows(self, parent_ids: list[str]) -> dict[str, ChunkRow]:
        """按 parent_id（父块 chunk id 的文本形式）批量取父块行；非法 id 跳过。"""
        ids: list[int] = []
        for pid in parent_ids:
            try:
                ids.append(int(pid))
            except ValueError:
                continue
        if not ids:
            return {}
        with self._pool.connection() as conn, conn.cursor() as cur:
            cur.execute(f"SELECT {_CHUNK_COLS} FROM chunks WHERE id = ANY(%s)", (ids,))
            rows = cur.fetchall()
        return {str(int(r[0])): _chunk_row(r) for r in rows}

    def doc_meta_map(self, doc_ids: list[str]) -> dict[str, DocMeta]:
        if not doc_ids:
            return {}
        with self._pool.connection() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT id, title, source, updated FROM docs WHERE id = ANY(%s)",
                (list(dict.fromkeys(doc_ids)),),
            )
            rows = cur.fetchall()
        return {r[0]: DocMeta(r[1], r[2], r[3]) for r in rows}

    def list_docs(self) -> list[DocInfo]:
        sql = """
            SELECT d.id, d.title, d.source, d.updated, COUNT(c.id) AS chunks
            FROM docs d LEFT JOIN chunks c ON c.doc_id = d.id
            GROUP BY d.id, d.title, d.source, d.updated
            ORDER BY d.id
        """
        with self._pool.connection() as conn, conn.cursor() as cur:
            cur.execute(sql)
            rows = cur.fetchall()
        return [DocInfo(r[0], r[1], r[2], r[3], int(r[4])) for r in rows]

    def get_stats(self) -> Stats:
        with self._pool.connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT (SELECT COUNT(*) FROM docs), (SELECT COUNT(*) FROM chunks)")
            docs, chunks = cur.fetchone()
            cur.execute("SELECT EXISTS(SELECT 1 FROM vectors)")
            embedded = bool(cur.fetchone()[0])
        return Stats(docs=int(docs), chunks=int(chunks), embedded=embedded)

    def has_embeddings(self) -> bool:
        with self._pool.connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT EXISTS(SELECT 1 FROM vectors)")
            return bool(cur.fetchone()[0])

    # ---------- 写路径（rag_upsert_doc 存储函数收口） ----------

    def upsert_doc(self, doc: dict, records: list[dict]) -> int:
        """幂等替换一个文档的全部 chunk 与向量；返回写入块数。

        doc: {id, title, source, updated}；records: [{text, section_path,
        is_parent, parent_idx, vec?}]（契约同 rag_upsert_doc JSONB 载荷）。
        """
        for rec in records:
            vec = rec.get("vec")
            if vec is not None and len(vec) != EmbedDim:
                raise ValueError(f"向量维度 {len(vec)} 与 halfvec({EmbedDim}) 不符")
        payload_doc = json.dumps(doc, ensure_ascii=False)
        payload_records = json.dumps(records, ensure_ascii=False)
        with self._pool.connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT rag_upsert_doc(%s, %s)", (payload_doc, payload_records))
            (n,) = cur.fetchone()
        return int(n)

    def wipe(self) -> None:
        """清空三表（测试用；DELETE+setval——TRUNCATE 排他锁在并行下死锁）。"""
        with self._lock:
            with self._pool.connection() as conn, conn.cursor() as cur:
                cur.execute("DELETE FROM vectors")
                cur.execute("DELETE FROM chunks")
                cur.execute("DELETE FROM docs")
                cur.execute("SELECT setval('chunks_id_seq', 1, false)")
            conn.commit()


def _chunk_row(r: tuple) -> ChunkRow:
    return ChunkRow(
        id=int(r[0]),
        doc_id=r[1],
        seq=int(r[2]),
        text=r[3],
        parent_id=r[4],
        section_path=r[5],
        is_parent=bool(r[6]),
    )
