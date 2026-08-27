"""混合检索：BM25 + 向量（可用时）→ RRF 融合 → 命中附文档元信息。"""

from __future__ import annotations

import logging

import numpy as np

from ..llm import embed, has_key
from .store import Hit, Store, rrf_fuse

logger = logging.getLogger(__name__)


class Retriever:
    def __init__(self, store: Store, k: int = 6):
        self.store = store
        self.k = k

    def search(self, query: str, k: int | None = None) -> list[Hit]:
        k = k or self.k
        bm_ids = [cid for cid, _ in self.store.bm25_search(query, k=k * 2)]

        vec_ids: list[int] = []
        if self.store.has_embeddings() and has_key():
            try:
                qv = np.asarray(embed([query])[0], dtype=np.float32)
                vec_ids = [cid for cid, _ in self.store.vector_search(qv, k=k * 2)]
            except Exception as exc:  # noqa: BLE001
                logger.warning("向量检索失败，本查询降级为纯 BM25：%s", exc)

        fused = rrf_fuse([bm_ids, vec_ids])[:k] if vec_ids else bm_ids[:k]

        rows = self.store.chunk_rows(fused)
        metas = self.store.doc_meta_map({r[0] for r in rows.values()})
        hits: list[Hit] = []
        for cid in fused:
            if cid not in rows:
                continue
            doc_id, seq, text = rows[cid]
            meta = metas.get(doc_id, {})
            hits.append(
                Hit(
                    chunk_id=cid,
                    doc_id=doc_id,
                    seq=seq,
                    text=text,
                    title=meta.get("title", doc_id),
                    source=meta.get("source", ""),
                )
            )
        return hits
