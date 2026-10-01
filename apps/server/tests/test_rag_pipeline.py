"""检索管线纯逻辑测试：RRF 融合、精排分数解析、Retriever 漏斗与父子扩展（全 Fake）。"""

from __future__ import annotations

import pytest

from gewu.rag.retrieve import LLMReranker, Retriever, parse_scores
from gewu.rag.store import ChunkRow, DocMeta, MissingVectorsError, Scored, rrf_fuse


class FakeStore:
    """RetrievalStore 协议替身：可编程双路召回，无 PG。"""

    def __init__(
        self,
        bm25: list[Scored] | None = None,
        vec: list[Scored] | None = None,
        chunks: dict[int, ChunkRow] | None = None,
        embedded: bool = True,
        fail_vector: bool = False,
    ) -> None:
        self.bm25 = bm25 or []
        self.vec = vec or []
        self.chunks = chunks or {}
        self.embedded = embedded
        self.fail_vector = fail_vector

    def bm25_search(self, query: str, k: int) -> list[Scored]:
        return self.bm25[:k]

    def vector_search(self, query_vec: list[float], k: int) -> list[Scored]:
        if self.fail_vector:
            raise RuntimeError("向量检索失败")
        return self.vec[:k]

    def chunk_rows(self, ids: list[int]) -> dict[int, ChunkRow]:
        return {i: self.chunks[i] for i in ids if i in self.chunks}

    def parent_rows(self, parent_ids: list[str]) -> dict[str, ChunkRow]:
        return {p: self.chunks[int(p)] for p in parent_ids if int(p) in self.chunks}

    def doc_meta_map(self, doc_ids: list[str]) -> dict[str, DocMeta]:
        return {d: DocMeta(f"标题{d}", "教务处", "2026-09-01") for d in doc_ids}

    def has_embeddings(self) -> bool:
        return self.embedded


class FakeLLM:
    """RagLLM 协议替身：可编程改写输出与精排分数。"""

    def __init__(
        self,
        has_key: bool = True,
        rewrite: str = "改写词",
        rerank_scores: list[float] | None = None,
        rerank_fail: bool = False,
    ) -> None:
        self._has_key = has_key
        self.rewrite = rewrite
        self.rerank_scores = rerank_scores
        self.rerank_fail = rerank_fail
        self.chat_calls: list[tuple[str, dict]] = []

    def has_key(self) -> bool:
        return self._has_key

    def chat(self, messages, *, small=False, json_mode=False, temperature=0.0, max_tokens=2048):
        self.chat_calls.append(
            (messages[0][1], {"small": small, "json": json_mode, "max_tokens": max_tokens})
        )
        if messages[0][1].startswith("你是检索结果的相关性打分器"):
            if self.rerank_fail:
                raise RuntimeError("rerank 失败")
            return '{"scores": [' + ", ".join(str(s) for s in (self.rerank_scores or [])) + "]}"
        return self.rewrite

    def embed(self, texts):
        return [[1.0] * 2048]


def _chunk(cid: int, doc: str = "d1", parent: str = "", text: str = "") -> ChunkRow:
    return ChunkRow(
        id=cid,
        doc_id=doc,
        seq=cid,
        text=text or f"文本{cid}",
        parent_id=parent,
        section_path="P",
        is_parent=False,
    )


def _parent(cid: int, text: str) -> ChunkRow:
    return ChunkRow(
        id=cid, doc_id="d1", seq=cid, text=text, parent_id="", section_path="P", is_parent=True
    )


def _store_two_ways() -> FakeStore:
    """BM25=[1,2,3] 向量=[2,4]；1/2 的父块是 9，4 的父块是 8，3 是 flat 命中。"""
    chunks = {
        1: _chunk(1, parent="9"),
        2: _chunk(2, parent="9"),
        3: _chunk(3),
        4: _chunk(4, parent="8"),
        9: _parent(9, "父块9"),
        8: _parent(8, "父块8"),
    }
    return FakeStore(
        bm25=[Scored(1, 3.0), Scored(2, 2.0), Scored(3, 1.0)],
        vec=[Scored(2, 0.9), Scored(4, 0.8)],
        chunks=chunks,
    )


# ---------- RRF 融合（P15 加权版：返回带归一分） ----------


def test_rrf_fuse_interleaves_two_lists():
    fused = rrf_fuse([[1, 2, 3], [2, 4, 5]], 60)
    # 2 两路都出现得分最高；其余按 1/(60+rank+1) 与首现序排
    assert [s.id for s in fused] == [2, 1, 4, 3, 5]
    # 双路出现者拿最高归一分（两路均首位时恰为 1.0，此处 bm 路次位 → 0.99+）
    assert fused[0].score > 0.98


def test_rrf_fuse_tie_keeps_first_seen_order():
    assert [s.id for s in rrf_fuse([[1], [2]], 60)] == [1, 2]


def test_rrf_fuse_weighted_ranks():
    # 向量权重 0.7：向量路 rank=2（4）压过关键词路 rank=2（3）
    fused = rrf_fuse([[1, 2, 3], [2, 4]], 60, weights=[0.3, 0.7])
    assert [s.id for s in fused] == [2, 4, 1, 3]
    # 归一分 ∈ (0,1]：双路冠军逼近 1，纯向量亚军 ≈ 0.7，关键词季军 ≈ 0.3
    assert fused[0].score > 0.98
    assert 0.6 < fused[1].score < 0.8
    assert fused[3].score < fused[2].score < 0.35


def test_rrf_fuse_equal_weights_match_legacy_order():
    # 等权退化：与旧版序一致（加权是旧版的单调变换）
    fused = rrf_fuse([[1, 2, 3], [2, 4]])
    assert [s.id for s in fused] == [2, 1, 4, 3]


# ---------- 精排分数解析 ----------


def test_parse_scores_plain():
    assert parse_scores('{"scores":[10, 5, 0]}', 3) == [10.0, 5.0, 0.0]


def test_parse_scores_strips_fences_and_prose():
    raw = '```json\n前置噪声 {"scores":[3, "7"]} 后置\n```'
    assert parse_scores(raw, 2) == [3.0, 7.0]  # 字符串分数容错


def test_parse_scores_rejects_length_mismatch_and_out_of_range():
    with pytest.raises(ValueError):
        parse_scores('{"scores":[1, 2]}', 3)
    with pytest.raises(ValueError):
        parse_scores('{"scores":[11]}', 1)
    with pytest.raises(ValueError):
        parse_scores('{"scores":["abc"]}', 1)


# ---------- Retriever 漏斗 ----------


def test_search_fuses_ranks_and_expands_parents():
    rr = Retriever(_store_two_ways(), k=3, client=FakeLLM())  # reranker 缺省关
    hits = rr.search("查询", 3)
    # RRF 融合=[2,1,4,3] → 截 k=3 [2,1,4] → 1/2 同父 9 去重、4 归父块 8
    assert [h.chunk_id for h in hits] == [9, 8]
    assert hits[0].text == "父块9"
    assert hits[0].title == "标题d1"


def test_search_without_llm_key_skips_rewrite_and_vector():
    llm = FakeLLM(has_key=False)
    rr = Retriever(_store_two_ways(), k=3, client=llm)
    hits = rr.search("原始问题", 3)
    assert llm.chat_calls == []  # 无 key：不改写不精排
    # 无向量召回 → 纯 BM25 顺序 1,2,3 → 1/2 同父 9、3 自身
    assert [h.chunk_id for h in hits] == [9, 3]
    assert hits[1].text == "文本3"


def test_search_rewrites_query_when_key_present():
    llm = FakeLLM(rewrite="图书馆 开放时间")
    rr = Retriever(_store_two_ways(), k=1, client=llm)
    rr.search("几点开门", 1)
    assert llm.chat_calls[0][1] == {"small": True, "json": False, "max_tokens": 80}
    assert llm.chat_calls[0][0].startswith("你是校园政策检索的查询改写器")


def test_search_vector_failure_degrades_to_bm25():
    store = _store_two_ways()
    store.fail_vector = True
    rr = Retriever(store, k=3, client=FakeLLM())
    assert [h.chunk_id for h in rr.search("q", 3)] == [9, 3]  # 纯 BM25 兜底


def test_search_no_vectors_in_index_raises():
    rr = Retriever(FakeStore(embedded=False), k=3, client=FakeLLM())
    with pytest.raises(MissingVectorsError):
        rr.search("q", 3)


def test_reranker_reorders_and_falls_back_on_error():
    store = _store_two_ways()
    # 加权融合候选 [2,4,1,3] 打分 [0,1,10,5]：复合分序 [1,3,2,4]，阈值 2.0
    # 滤掉 0/1 分 → 保留 [1,3] → 1 归父块 9、3 flat → [9,3]
    rr = Retriever(
        store, k=3, client=FakeLLM(), reranker=LLMReranker(FakeLLM(rerank_scores=[0, 1, 10, 5]))
    )
    assert [h.chunk_id for h in rr.search("q", 3)] == [9, 3]

    # 失败退回 RRF 粗排：[2,4,1] → 1/2 同父去重 → [9,8]
    rr2 = Retriever(store, k=3, client=FakeLLM(), reranker=LLMReranker(FakeLLM(rerank_fail=True)))
    assert [h.chunk_id for h in rr2.search("q", 3)] == [9, 8]


def test_reranker_skipped_when_candidates_fit_k():
    llm = FakeLLM()
    rr = Retriever(_store_two_ways(), k=5, client=llm, reranker=LLMReranker(llm))
    rr.search("q", 5)  # 候选 4 ≤ k=5 → 不精排（Go rerankOrKeep 语义）
    assert llm.chat_calls[-1][0].startswith("你是校园政策检索的查询改写器")


# ---------- 精排阈值容错（P15：WeKnora 退化语义） ----------


def test_rerank_threshold_filters_low_scores():
    # 候选 [2,4,1,3] 打分 [8, 1, 7, 0]：阈值 2.0 滤掉 1/0 分 → 保留 [2,1]
    # （复合分序 [2,1,4,3]）→ 同父块 9 去重 → [9]
    rr = Retriever(
        _store_two_ways(),
        k=3,
        client=FakeLLM(),
        reranker=LLMReranker(FakeLLM(rerank_scores=[8, 1, 7, 0])),
    )
    assert [h.chunk_id for h in rr.search("q", 3)] == [9]


def test_rerank_threshold_relaxes_when_all_filtered():
    # 全部低于阈值 2.0：退化阈值 max(2×0.7, 1.5)=1.5 重试 → 1.8/1.7 都保留
    rr = Retriever(
        _store_two_ways(),
        k=3,
        client=FakeLLM(),
        reranker=LLMReranker(FakeLLM(rerank_scores=[1.8, 1.7, 1.0, 0.5])),
    )
    hits = rr.search("q", 3)
    assert [h.chunk_id for h in hits] == [9, 8]  # 候选 2/4（1.8/1.7）保留 → 父块 9/8


def test_rerank_returns_empty_when_nothing_reaches_floor():
    # 全 0 分：阈值与保底（≥1.5）都不过 → 空命中（上游走拒答路径）
    rr = Retriever(
        _store_two_ways(),
        k=3,
        client=FakeLLM(),
        reranker=LLMReranker(FakeLLM(rerank_scores=[0, 0, 0, 0])),
    )
    assert rr.search("q", 3) == []


def test_rerank_keeps_top1_when_barely_passes_floor():
    # 阈值全滤、退化后仅一个 1.5 分候选：保底保留 top1
    rr = Retriever(
        _store_two_ways(),
        k=3,
        client=FakeLLM(),
        reranker=LLMReranker(FakeLLM(rerank_scores=[1.5, 0.2, 0.1, 0.0])),
    )
    hits = rr.search("q", 3)
    assert [h.chunk_id for h in hits] == [9]  # 候选 2 → 父块 9


def test_single_way_fallback_normalizes_scores():
    # 无 key（向量路关闭）：纯 FTS 分按列表 max 归一（WeKnora keyword-only 语义）
    rr = Retriever(_store_two_ways(), k=3, client=FakeLLM(has_key=False))
    hits = rr.search("q", 3)
    assert [h.chunk_id for h in hits] == [9, 3]
