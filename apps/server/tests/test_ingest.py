"""ingest 单测（P15）：frontmatter 解析、嵌入内容拼装、批量向量化与 upsert 载荷契约。"""

from __future__ import annotations

from pathlib import Path

import pytest

from gewu.rag.chunker import ChunkConfig
from gewu.rag.ingest import ParsedDoc, embed_batched, embed_content, parse_doc, run_ingest

RULED_MD = """---
title: 钱塘大学图书馆读者服务管理办法
source: 图书馆
updated: 2026-05-20
---

# 图书馆读者服务管理办法

## 开放时间

主馆开放时间：周一至周五 7:30—22:30。

## 借阅规则

本科生外借上限为 10 册，借期 30 天。
"""


class FakeEmbedder:
    """记录调用并返回固定向量（run_ingest 只依赖 embed 接口）。"""

    def __init__(self, fail_first: int = 0) -> None:
        self.calls: list[list[str]] = []
        self._fails_left = fail_first

    def embed(self, texts: list[str]) -> list[list[float]]:
        if self._fails_left > 0:
            self._fails_left -= 1
            raise RuntimeError("瞬时错误")
        self.calls.append(list(texts))
        return [[0.3, 0.4] for _ in texts]


class FakeStore:
    def __init__(self) -> None:
        self.wiped = 0
        self.docs: list[tuple[dict, list[dict]]] = []

    def wipe(self) -> None:
        self.wiped += 1

    def upsert_doc(self, doc: dict, records: list[dict]) -> int:
        self.docs.append((doc, records))
        return len(records)


def _write_corpus(tmp_path: Path) -> Path:
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "0001-library.md").write_text(RULED_MD, encoding="utf-8")
    return corpus


# ---------- parse_doc ----------


class TestParseDoc:
    def test_frontmatter(self, tmp_path: Path):
        f = tmp_path / "doc.md"
        f.write_text(RULED_MD, encoding="utf-8")
        doc = parse_doc(f)
        assert doc.meta["title"] == "钱塘大学图书馆读者服务管理办法"
        assert doc.meta["source"] == "图书馆"
        assert doc.meta["updated"] == "2026-05-20"
        assert doc.text.startswith("# 图书馆读者服务管理办法")
        assert "---" not in doc.text.splitlines()[0]

    def test_defaults_without_frontmatter(self, tmp_path: Path):
        f = tmp_path / "plain.md"
        f.write_text("# 正文\n\n内容。", encoding="utf-8")
        doc = parse_doc(f)
        assert doc.meta["title"] == "plain"
        assert doc.meta["source"] == "钱塘大学"
        assert doc.meta["updated"] == ""
        assert doc.text.startswith("# 正文")


# ---------- embed_content / embed_batched ----------


class TestEmbedContent:
    def test_full_concat(self):
        s = embed_content("图书馆办法", "图书馆办法 > 借阅规则", "本科生外借上限 10 册")
        assert s == "图书馆办法\n图书馆办法 > 借阅规则\n本科生外借上限 10 册"

    def test_empty_path_skipped(self):
        assert embed_content("标题", "", "正文") == "标题\n正文"


class TestEmbedBatched:
    def test_l2_normalized(self):
        vecs = embed_batched(FakeEmbedder(), ["a", "b"])
        assert all(abs(sum(x * x for x in v) - 1.0) < 1e-6 for v in vecs)

    def test_retry_on_transient_failure(self):
        emb = FakeEmbedder(fail_first=1)
        vecs = embed_batched(emb, ["a"])
        assert len(vecs) == 1
        assert len(emb.calls) == 1  # 首次失败未记录，重试成功记一次

    def test_gives_up_after_retries(self):
        class AlwaysFail:
            def embed(self, texts):
                raise RuntimeError("持续失败")

        with pytest.raises(RuntimeError, match="失败"):
            embed_batched(AlwaysFail(), ["a"])


# ---------- run_ingest ----------


class TestRunIngest:
    def _ingest(self, tmp_path, **kwargs):
        store = FakeStore()
        embedder = kwargs.pop("embedder", FakeEmbedder())
        summary = run_ingest(store, embedder, _write_corpus(tmp_path), ChunkConfig(), **kwargs)
        return store, embedder, summary

    def test_doc_and_records_shape(self, tmp_path):
        store, _, summary = self._ingest(tmp_path)
        assert summary.docs == 1
        assert summary.tiers == {"0001-library": "heading"}
        (doc, records) = store.docs[0]
        assert doc == {
            "id": "0001-library",
            "title": "钱塘大学图书馆读者服务管理办法",
            "source": "图书馆",
            "updated": "2026-05-20",
        }
        parents = [r for r in records if r["is_parent"]]
        children = [r for r in records if not r["is_parent"]]
        assert len(parents) >= 1 and len(children) >= 1
        parent_ids = {i for i, r in enumerate(records) if r["is_parent"]}
        for r in children:
            assert r["parent_idx"] in parent_ids

    def test_children_have_vecs_parents_do_not(self, tmp_path):
        store, _, _ = self._ingest(tmp_path)
        _, records = store.docs[0]
        assert all("vec" in r for r in records if not r["is_parent"])
        assert all("vec" not in r for r in records if r["is_parent"])

    def test_embedder_receives_title_and_breadcrumb(self, tmp_path):
        _, embedder, _ = self._ingest(tmp_path)
        sent = [t for batch in embedder.calls for t in batch]
        assert sent, "应向量化子块"
        for s in sent:
            assert s.startswith("钱塘大学图书馆读者服务管理办法\n")
            assert "【" not in s  # 嵌入内容用裸面包屑，不含父块【】前缀

    def test_rebuild_wipes_first(self, tmp_path):
        store, _, _ = self._ingest(tmp_path, rebuild=True)
        assert store.wiped == 1
        assert len(store.docs) == 1

    def test_no_rebuild_no_wipe(self, tmp_path):
        store, _, _ = self._ingest(tmp_path)
        assert store.wiped == 0

    def test_no_embed_skips_vectorization(self, tmp_path):
        store, embedder, summary = self._ingest(tmp_path, no_embed=True, embedder=None)
        assert summary.embedded is False
        assert embedder is None  # no_embed 允许无 embedder
        _, records = store.docs[0]
        assert all("vec" not in r for r in records)

    def test_missing_embedder_rejected(self, tmp_path):
        with pytest.raises(ValueError, match="no_embed"):
            run_ingest(FakeStore(), None, _write_corpus(tmp_path), ChunkConfig())

    def test_empty_corpus_rejected(self, tmp_path):
        empty = tmp_path / "corpus"
        empty.mkdir()
        with pytest.raises(FileNotFoundError):
            run_ingest(FakeStore(), FakeEmbedder(), empty, ChunkConfig())


class TestParsedDoc:
    def test_frozen(self):
        with pytest.raises(AttributeError):
            ParsedDoc(meta={}, text="x").text = "y"
