"""知识库存储：SQLite + BM25（中文字符二元语法）+ 向量余弦，供上层做 RRF 混合检索。

设计取舍：校园知识库规模在千级 chunk，暴力余弦足够快，因此不引入外部向量数据库，
换取零部署依赖；存储接口保持可替换（见 docs/architecture.md）。
所有 SQL 均为单行静态语句 + 参数绑定，不拼接任何动态片段。
"""

from __future__ import annotations

import math
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path

import numpy as np

SCHEMA = """
CREATE TABLE IF NOT EXISTS docs (
    id      TEXT PRIMARY KEY,
    title   TEXT NOT NULL,
    source  TEXT NOT NULL,
    updated TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS chunks (
    id     INTEGER PRIMARY KEY AUTOINCREMENT,
    doc_id TEXT NOT NULL,
    seq    INTEGER NOT NULL,
    text   TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS vectors (
    chunk_id INTEGER PRIMARY KEY,
    dim      INTEGER NOT NULL,
    data     BLOB NOT NULL
);
"""

_LATIN_RE = re.compile(r"[a-zA-Z0-9]+")
_HAN_RE = re.compile(r"[\u4e00-\u9fff]")


def tokenize(text: str) -> list[str]:
    """英文/数字按词、中文按字符二元语法：免去分词依赖，中文召回效果可用。"""
    tokens = [m.group(0).lower() for m in _LATIN_RE.finditer(text)]
    han = _HAN_RE.findall(text)
    tokens += [han[i] + han[i + 1] for i in range(len(han) - 1)]
    return tokens


@dataclass
class Hit:
    chunk_id: int
    doc_id: str
    seq: int
    text: str
    title: str
    source: str


def rrf_fuse(rank_lists: list[list[int]], k: int = 60) -> list[int]:
    """Reciprocal Rank Fusion：多路召回的排名融合。"""
    scores: dict[int, float] = {}
    for lst in rank_lists:
        for rank, chunk_id in enumerate(lst):
            scores[chunk_id] = scores.get(chunk_id, 0.0) + 1.0 / (k + rank + 1)
    return [cid for cid, _ in sorted(scores.items(), key=lambda kv: -kv[1])]


class Store:
    def __init__(self, path: Path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.executescript(SCHEMA)
        self._bm25_cache: dict | None = None
        self._vector_cache: dict[int, np.ndarray] | None = None

    # ---------- 写入 ----------

    def upsert_doc(
        self,
        doc_id: str,
        title: str,
        source: str,
        updated: str,
        chunk_texts: list[str],
        vectors: list[np.ndarray] | None = None,
    ) -> None:
        """幂等入库：同一 doc_id 重复导入时先清旧 chunk 与向量。"""
        with self.conn:
            old_ids = [
                r[0]
                for r in self.conn.execute("SELECT id FROM chunks WHERE doc_id = ?", (doc_id,))
            ]
            for old_id in old_ids:
                self.conn.execute("DELETE FROM vectors WHERE chunk_id = ?", (old_id,))
            self.conn.execute("DELETE FROM chunks WHERE doc_id = ?", (doc_id,))
            self.conn.execute(
                "INSERT OR REPLACE INTO docs (id, title, source, updated) VALUES (?, ?, ?, ?)",
                (doc_id, title, source, updated),
            )
            for seq, text in enumerate(chunk_texts):
                cur = self.conn.execute(
                    "INSERT INTO chunks (doc_id, seq, text) VALUES (?, ?, ?)",
                    (doc_id, seq, text),
                )
                if vectors is not None and seq < len(vectors):
                    vec = np.asarray(vectors[seq], dtype=np.float32)
                    self.conn.execute(
                        "INSERT INTO vectors (chunk_id, dim, data) VALUES (?, ?, ?)",
                        (cur.lastrowid, int(vec.shape[0]), vec.tobytes()),
                    )
        self._bm25_cache = None
        self._vector_cache = None

    # ---------- BM25 ----------

    def _ensure_bm25(self) -> None:
        if self._bm25_cache is not None:
            return
        postings: dict[str, dict[int, int]] = {}
        doc_len: dict[int, int] = {}
        for cid, text in self.conn.execute("SELECT id, text FROM chunks"):
            tokens = tokenize(text)
            doc_len[cid] = max(1, len(tokens))
            counts: dict[str, int] = {}
            for t in tokens:
                counts[t] = counts.get(t, 0) + 1
            for t, tf in counts.items():
                postings.setdefault(t, {})[cid] = tf
        n_docs = len(doc_len) or 1
        avgdl = sum(doc_len.values()) / n_docs
        idf = {
            t: math.log(1 + (n_docs - len(plist) + 0.5) / (len(plist) + 0.5))
            for t, plist in postings.items()
        }
        self._bm25_cache = {"postings": postings, "doc_len": doc_len, "idf": idf, "avgdl": avgdl}

    def bm25_search(self, query: str, k: int = 6) -> list[tuple[int, float]]:
        self._ensure_bm25()
        c = self._bm25_cache
        k1, b = 1.5, 0.75
        scores: dict[int, float] = {}
        for token in set(tokenize(query)):
            plist = c["postings"].get(token)
            if not plist:
                continue
            idf = c["idf"][token]
            for cid, tf in plist.items():
                denom = tf + k1 * (1 - b + b * c["doc_len"][cid] / c["avgdl"])
                scores[cid] = scores.get(cid, 0.0) + idf * tf * (k1 + 1) / denom
        ranked = sorted(scores.items(), key=lambda kv: -kv[1])
        return ranked[:k]

    # ---------- 向量 ----------

    def _ensure_vectors(self) -> None:
        if self._vector_cache is not None:
            return
        cache: dict[int, np.ndarray] = {}
        for cid, dim, blob in self.conn.execute("SELECT chunk_id, dim, data FROM vectors"):
            vec = np.frombuffer(blob, dtype=np.float32)
            if vec.shape[0] != dim:
                continue
            norm = np.linalg.norm(vec)
            cache[cid] = vec / norm if norm > 0 else vec
        self._vector_cache = cache

    def has_embeddings(self) -> bool:
        (n,) = self.conn.execute("SELECT COUNT(*) FROM vectors").fetchone()
        return n > 0

    def vector_search(self, query_vec: np.ndarray, k: int = 6) -> list[tuple[int, float]]:
        """暴力余弦：千级 chunk 规模下延迟毫秒级，规模上去后再换专用向量库。"""
        self._ensure_vectors()
        if not self._vector_cache:
            return []
        q = np.asarray(query_vec, dtype=np.float32)
        norm = np.linalg.norm(q)
        if norm > 0:
            q = q / norm
        ids = list(self._vector_cache.keys())
        matrix = np.stack([self._vector_cache[i] for i in ids])
        scores = matrix @ q
        order = np.argsort(-scores)[:k]
        return [(ids[i], float(scores[i])) for i in order]

    # ---------- 读取 ----------

    def chunk_rows(self, chunk_ids: list[int]) -> dict[int, tuple[str, int, str]]:
        rows: dict[int, tuple[str, int, str]] = {}
        for cid in chunk_ids:
            row = self.conn.execute(
                "SELECT id, doc_id, seq, text FROM chunks WHERE id = ?", (cid,)
            ).fetchone()
            if row:
                rows[row[0]] = (row[1], row[2], row[3])
        return rows

    def doc_meta_map(self, doc_ids) -> dict[str, dict]:
        metas: dict[str, dict] = {}
        for doc_id in doc_ids:
            row = self.conn.execute(
                "SELECT id, title, source, updated FROM docs WHERE id = ?", (doc_id,)
            ).fetchone()
            if row:
                metas[row[0]] = {"title": row[1], "source": row[2], "updated": row[3]}
        return metas

    def list_docs(self) -> list[dict]:
        docs = self.conn.execute("SELECT id, title, source, updated FROM docs ORDER BY id").fetchall()
        out: list[dict] = []
        for doc_id, title, source, updated in docs:
            (n,) = self.conn.execute(
                "SELECT COUNT(*) FROM chunks WHERE doc_id = ?", (doc_id,)
            ).fetchone()
            out.append(
                {"doc_id": doc_id, "title": title, "source": source, "updated": updated, "chunks": n}
            )
        return out

    def stats(self) -> dict:
        (docs,) = self.conn.execute("SELECT COUNT(*) FROM docs").fetchone()
        (chunks,) = self.conn.execute("SELECT COUNT(*) FROM chunks").fetchone()
        return {"docs": docs, "chunks": chunks, "embedded": self.has_embeddings()}
