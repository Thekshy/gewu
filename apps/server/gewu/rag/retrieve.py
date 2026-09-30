"""混合检索管线（移植自 Go internal/rag/retrieve.go / rewrite.go / rerank.go）。

漏斗：BM25/向量各取 poolN=20 → RRF 粗排 →（有 key 且开启 rerank）LLM 精排到
topK → 按 parent_id 回取父块去重。查询侧口语→术语改写（有 key 即启用）。
"""

from __future__ import annotations

import json
import threading
from typing import Protocol

from gewu.rag.store import ChunkRow, DocMeta, Hit, MissingVectorsError, Scored, rrf_fuse

POOL_N = 20
RRF_K = 60


class RagLLM(Protocol):
    """检索管线对 LLM 访问层的最小依赖（Go LLMer 接口同款）。"""

    def has_key(self) -> bool: ...

    def chat(
        self,
        messages: list[tuple[str, str]],
        *,
        small: bool = False,
        json_mode: bool = False,
        temperature: float = 0.0,
        max_tokens: int = 2048,
    ) -> str: ...

    def embed(self, texts: list[str]) -> list[list[float]]: ...


class RetrievalStore(Protocol):
    """检索管线对存储层的最小依赖（测试用 Fake 实现同一协议）。"""

    def bm25_search(self, query: str, k: int) -> list[Scored]: ...

    def vector_search(self, query_vec: list[float], k: int) -> list[Scored]: ...

    def chunk_rows(self, ids: list[int]) -> dict[int, ChunkRow]: ...

    def parent_rows(self, parent_ids: list[str]) -> dict[str, ChunkRow]: ...

    def doc_meta_map(self, doc_ids: list[str]) -> dict[str, DocMeta]: ...

    def has_embeddings(self) -> bool: ...


class Retriever:
    """混合检索器：BM25 + 向量 → RRF 融合 →（可选）LLM 精排 → 父子扩展。"""

    def __init__(
        self,
        store: RetrievalStore,
        k: int,
        client: RagLLM | None,
        *,
        reranker: LLMReranker | None = None,
    ) -> None:
        self.store = store
        self.k = k
        self.client = client
        self.rewriter = Rewriter(client)
        self.reranker = reranker

    def search(self, query: str, k: int = 0) -> list[Hit]:
        if k <= 0:
            k = self.k
        query = self.rewriter.expand(query)  # 口语 → 政策术语（无 key 时原样返回）

        has_emb = self.store.has_embeddings()
        if not has_emb:
            raise MissingVectorsError("向量索引缺失，请配好 EMBED_* 后用 -rebuild 重建索引")

        pool = POOL_N
        if k > pool:
            pool = k
        bm_scored = self.store.bm25_search(query, pool)
        bm_ids = [sc.id for sc in bm_scored]

        vec_ids = self._vector_candidates(query, pool)

        fused = bm_ids[:k]
        if vec_ids:
            fused = rrf_fuse([bm_ids, vec_ids], RRF_K)[:pool]
        elif len(bm_ids) > pool:
            fused = bm_ids[:pool]

        fused = self._rerank_or_keep(query, fused, k)
        return self._expand_to_parents(fused, k)

    def _vector_candidates(self, query: str, k: int) -> list[int]:
        """向量召回路：查询向量化失败（瞬时错误）时退化为纯 BM25（单次查询容错）。"""
        if self.client is None or not self.client.has_key():
            return []
        try:
            vecs = self.client.embed([query])
        except Exception as e:  # noqa: BLE001 - 容错路径：本查询退化，不阻断
            print(f"[rag] 查询向量化失败，本查询退化为纯 BM25：{e}")
            return []
        if not vecs:
            return []
        try:
            scored = self.store.vector_search(vecs[0], k)
        except Exception as e:  # noqa: BLE001
            print(f"[rag] 向量检索失败，本查询退化为纯 BM25：{e}")
            return []
        return [sc.id for sc in scored]

    def _rerank_or_keep(self, query: str, ids: list[int], k: int) -> list[int]:
        """有 key 且配置精排器时打分重排（平局保持 RRF 次序）；失败/关闭原样返回。"""
        if (
            self.reranker is None
            or self.client is None
            or not self.client.has_key()
            or len(ids) <= k
        ):
            return ids[:k]
        rows: dict[int, ChunkRow] = self.store.chunk_rows(ids)
        texts = [rows[cid].text if cid in rows else "" for cid in ids]
        try:
            scores = self.reranker.rerank(query, texts)
        except Exception as e:  # noqa: BLE001
            print(f"[rag] rerank 失败，退回 RRF 粗排顺序：{e}")
            return ids[:k]
        order = sorted(range(len(ids)), key=lambda i: (-scores[i], i))
        return [ids[i] for i in order[:k]]

    def _expand_to_parents(self, ids: list[int], k: int) -> list[Hit]:
        """父子扩展：命中子块按 parent_id 回取父块，同父去重（取最高命中的位次）。

        flat 模式（parent_id 为空）每个命中自成一块。
        """
        child_rows = self.store.chunk_rows(ids)
        slots: list[tuple[str, int]] = []  # (parent_key, child_id)
        best_rank: dict[str, int] = {}
        for rank, cid in enumerate(ids):
            row = child_rows.get(cid)
            if row is None:
                continue
            pk = row.parent_id or f"__self__:{cid}"
            if pk not in best_rank:
                best_rank[pk] = rank
                slots.append((pk, cid))
        slots = slots[:k]

        parent_ids = [pk for pk, _ in slots if not pk.startswith("__self__:")]
        doc_ids = [child_rows[cid].doc_id for _, cid in slots]
        parent_rows = self.store.parent_rows(parent_ids)
        metas = self.store.doc_meta_map(doc_ids)

        hits: list[Hit] = []
        for pk, cid in slots:
            child = child_rows[cid]
            meta = metas.get(child.doc_id)
            prow = parent_rows.get(pk)
            if prow is not None and prow.is_parent:
                pmeta = metas.get(prow.doc_id)
                hits.append(
                    Hit(
                        chunk_id=prow.id,
                        doc_id=prow.doc_id,
                        seq=prow.seq,
                        text=prow.text,
                        title=(pmeta.title if pmeta and pmeta.title else prow.doc_id),
                        source=(pmeta.source if pmeta else ""),
                        section_path=prow.section_path,
                    )
                )
                continue
            # flat 模式或父块缺失：命中子块即命中本身。
            hits.append(
                Hit(
                    chunk_id=child.id,
                    doc_id=child.doc_id,
                    seq=child.seq,
                    text=child.text,
                    title=(meta.title if meta and meta.title else child.doc_id),
                    source=(meta.source if meta else ""),
                    section_path=child.section_path,
                )
            )
        return hits


class Rewriter:
    """进程内查询改写器（口语 → 政策术语检索串），带并发安全缓存。"""

    def __init__(self, client: RagLLM | None) -> None:
        self._client = client
        self._mu = threading.Lock()
        self._cache: dict[str, str] = {}

    def expand(self, query: str) -> str:
        if self._client is None or not self._client.has_key():
            return query
        with self._mu:
            cached = self._cache.get(query)
        if cached is not None:
            return cached
        try:
            rewritten = self._client.chat(
                [
                    ("system", REWRITE_SYSTEM),
                    ("user", query),
                ],
                small=True,
                temperature=0.0,
                max_tokens=80,
            )
        except Exception as e:  # noqa: BLE001 - 改写失败用原查询
            print(f"[rag] 查询改写失败，使用原查询：{e}")
            return query
        rewritten = rewritten.strip('"“” \n\t')
        result = f"{query} {rewritten}" if rewritten else query
        with self._mu:
            self._cache[query] = result
        return result


REWRITE_SYSTEM = """你是校园政策检索的查询改写器。把用户的口语化问题改写为适合关键词检索的查询串：
1. 保留核心实体（图书馆、转专业、奖学金、体测……）与数字；
2. 把口语说法换成政策文件用语（如：最多→上限，借书→外借 借阅，钱→元，挂科→不及格，发学位证→授予学位）；
3. 输出 10~25 个字的查询词串，不解释、不使用引号。
只输出改写后的查询串本身。"""

RERANK_SYSTEM = """你是检索结果的相关性打分器。给你一个查询和若干编号候选段落，对每条候选打 0~10 的整数相关性分：
- 10：直接包含回答该查询所需的核心条款/数字/流程；
- 5：主题相关但只是背景信息；
- 0：与查询无关。
只输出 JSON：{"scores":[n1, n2, ...]}，scores 与候选编号一一对应、长度相同。"""


class LLMReranker:
    """基于 OpenAI 兼容 Chat 端点的批量 pointwise 精排器（小模型一次调用）。"""

    def __init__(self, client: RagLLM) -> None:
        self._client = client

    def rerank(self, query: str, candidates: list[str]) -> list[float]:
        if not candidates:
            return []
        parts = [f"查询：{query}", "", "候选段落："]
        for i, c in enumerate(candidates):
            parts.append(f"[{i + 1}] {c}")
            parts.append("")
        raw = self._client.chat(
            [
                ("system", RERANK_SYSTEM),
                ("user", "\n".join(parts).rstrip()),
            ],
            small=True,
            json_mode=True,
            temperature=0.0,
            max_tokens=300,
        )
        return parse_scores(raw, len(candidates))


def parse_scores(raw: str, n: int) -> list[float]:
    """解析 {"scores":[...]}，容错剥离围栏/前后缀；长度不符或含非法分数即报错。"""
    s = raw.strip()
    start = s.find("{")
    end = s.rfind("}")
    if start >= 0 and end > start:
        s = s[start : end + 1]
    obj = json.loads(s)
    scores = obj.get("scores")
    if not isinstance(scores, list) or len(scores) != n:
        raise ValueError(
            f"分数个数 {len(scores) if isinstance(scores, list) else '?'} 与候选数 {n} 不符"
        )
    out: list[float] = []
    for i, v in enumerate(scores):
        f = _to_float(v)
        if f is None or f < 0 or f > 10:
            raise ValueError(f"第 {i} 个分数非法: {v}")
        out.append(f)
    return out


def _to_float(v: object) -> float | None:
    """JSON 数值容错转换（LLM 偶尔会把分数输出成字符串）。"""
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        try:
            return float(v.strip())
        except ValueError:
            return None
    return None
