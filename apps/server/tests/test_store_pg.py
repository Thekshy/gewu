"""PG 集成测试：存储函数调用（FTS/向量/行取/upsert）+ 检索管线真库 E2E。

测试库 gewu_test（conftest 夹具建 schema + 清库）；向量用 2048 维单位向量
（对齐 Go UnitVec），不依赖任何 embed 服务。
"""

from __future__ import annotations

import pytest

from gewu.rag.retrieve import Retriever
from gewu.rag.store import EmbedDim, Store


def unit_vec(i: int) -> list[float]:
    v = [0.0] * EmbedDim
    v[i % EmbedDim] = 1.0
    return v


@pytest.fixture()
def seeded_store(pg_store: Store) -> Store:
    """两篇文档：doc-a 父子块（父块 0 + 子块 1/2），doc-b flat 块（向量区分）。"""
    pg_store.upsert_doc(
        {"id": "t-doc-a", "title": "测试文档A", "source": "教务处", "updated": "2026-09-01"},
        [
            {
                "text": "测试文档A 第一章 总则",
                "section_path": "第一章",
                "is_parent": True,
                "parent_idx": -1,
            },
            {
                "text": "图书馆工作日八点开门二十一点闭馆",
                "section_path": "第一章 > 开放时间",
                "is_parent": False,
                "parent_idx": 0,
                "vec": unit_vec(0),
            },
            {
                "text": "学生请假三天以内由辅导员审批",
                "section_path": "第一章 > 请假",
                "is_parent": False,
                "parent_idx": 0,
                "vec": unit_vec(1),
            },
        ],
    )
    pg_store.upsert_doc(
        {"id": "t-doc-b", "title": "测试文档B", "source": "学生处", "updated": "2026-09-02"},
        [
            {
                "text": "奖学金评定每年九月开展",
                "section_path": "",
                "is_parent": False,
                "parent_idx": -1,
                "vec": unit_vec(2),
            },
        ],
    )
    return pg_store


def test_upsert_is_idempotent_replacing_docs(pg_store: Store):
    pg_store.upsert_doc(
        {"id": "t-doc-c", "title": "幂等", "source": "", "updated": ""},
        [{"text": "第一版", "section_path": "", "is_parent": False, "parent_idx": -1}],
    )
    pg_store.upsert_doc(
        {"id": "t-doc-c", "title": "幂等", "source": "", "updated": ""},
        [
            {"text": "第二版A", "section_path": "", "is_parent": False, "parent_idx": -1},
            {"text": "第二版B", "section_path": "", "is_parent": False, "parent_idx": -1},
        ],
    )
    scored = pg_store.bm25_search("第二版", 10)
    rows = pg_store.chunk_rows([s.id for s in scored])
    texts = {r.text for r in rows.values()}
    assert {"第二版A", "第二版B"} <= texts  # 新块在
    assert "第一版" not in texts  # 旧块被整体替换，不叠加


def test_bm25_hits_children_not_parents(seeded_store: Store):
    """rag_fts_search 过滤父块：搜「图书馆 开门」只回子块。"""
    scored = seeded_store.bm25_search("图书馆开门", 5)
    assert scored, "FTS 应命中"
    rows = seeded_store.chunk_rows([s.id for s in scored])
    assert all(not r.is_parent for r in rows.values())


def test_bm25_or_semantics_recalls_any_token(seeded_store: Store):
    """OR 语义：只命中一个 token 也召回（长 query 不被 AND 过滤成空）。"""
    scored = seeded_store.bm25_search("闭馆", 5)
    assert scored


def test_vector_search_orders_by_similarity_and_validates_dim(seeded_store: Store):
    scored = seeded_store.vector_search(unit_vec(0), 3)
    assert scored
    top = seeded_store.chunk_rows([s.id for s in scored])
    assert top[scored[0].id].text == "图书馆工作日八点开门二十一点闭馆"
    with pytest.raises(ValueError, match="不符"):
        seeded_store.vector_search([0.1] * 10, 3)


def test_chunk_rows_and_parent_rows(seeded_store: Store):
    scored = seeded_store.bm25_search("图书馆开门", 1)
    child = seeded_store.chunk_rows([scored[0].id])[scored[0].id]
    assert child.parent_id != ""  # 父子块：子块带 parent_id
    parents = seeded_store.parent_rows([child.parent_id])
    assert parents[child.parent_id].is_parent is True
    assert parents[child.parent_id].text.startswith("测试文档A")


def test_doc_meta_map(seeded_store: Store):
    metas = seeded_store.doc_meta_map(["t-doc-a", "t-doc-b", "t-missing"])
    assert metas["t-doc-a"].title == "测试文档A"
    assert metas["t-doc-a"].source == "教务处"
    assert "t-missing" not in metas


def test_retrieve_pipeline_e2e_on_real_pg(seeded_store: Store):
    """真库管线 E2E：BM25 + 向量（假 embed 单位向量）→ RRF → 父子扩展出父块文本。"""

    class FakeLLM:
        def has_key(self) -> bool:
            return True

        def chat(self, messages, *, small=False, json_mode=False, temperature=0.0, max_tokens=2048):
            raise AssertionError("reranker 未启用时不应触发 chat")

        def embed(self, texts):
            return [unit_vec(0)]  # 查询向量指向「图书馆」块

    rr = Retriever(seeded_store, k=2, client=FakeLLM())
    hits = rr.search("图书馆几点开门", 2)
    assert hits, "真库检索应命中"
    assert hits[0].doc_id == "t-doc-a"
    assert hits[0].text.startswith("测试文档A 第一章")  # hierarchical：进上下文的是父块
    assert hits[0].title == "测试文档A"


def test_upsert_vec_dim_mismatch_rejected(pg_store: Store):
    with pytest.raises(ValueError, match="不符"):
        pg_store.upsert_doc(
            {"id": "t-bad-dim", "title": "x", "source": "", "updated": ""},
            [
                {
                    "text": "t",
                    "section_path": "",
                    "is_parent": False,
                    "parent_idx": -1,
                    "vec": [0.1] * 3,
                }
            ],
        )


def test_get_stats_and_has_embeddings(seeded_store: Store):
    stats = seeded_store.get_stats()
    assert stats.docs >= 2
    assert stats.chunks >= 4
    assert seeded_store.has_embeddings() is True
