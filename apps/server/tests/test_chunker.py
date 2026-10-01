"""chunker 单测（P15）：画像选型、heading 父子结构、recursive 兜底、校验降级。"""

from __future__ import annotations

from pathlib import Path

import pytest

from gewu.rag.chunker import (
    Chunk,
    ChunkConfig,
    chunk_document,
    chunk_text,
    parse_markdown_tree,
    profile,
    validate_chunks,
)

_REPO_ROOT = Path(__file__).resolve().parents[3]

RULED_MD = """# 转专业管理办法

## 申请条件

一年级末学分绩点排名前 10%，无不及格课程。

## 办理流程

学期末提交申请，学院审核公示后报教务处备案。
"""

FLAT_MD = ("这是一段没有任何标题的长文。" * 40).strip()


# ---------- 画像与选型 ----------


class TestProfile:
    def test_ruled_md_selects_heading(self):
        result = chunk_document(RULED_MD, ChunkConfig())
        assert result.tier == "heading"
        assert result.fallbacks == []

    def test_no_heading_falls_to_recursive(self):
        result = chunk_document(FLAT_MD, ChunkConfig())
        assert result.tier == "recursive"

    def test_few_headings_falls_to_recursive(self):
        # 标题数 < MIN_HEADINGS：画像不达标 → recursive
        text = "# 唯一标题\n\n" + "正文内容。" * 100
        assert profile(text).headings < 3
        result = chunk_document(text, ChunkConfig())
        assert result.tier == "recursive"

    def test_explicit_recursive_strategy(self):
        result = chunk_document(RULED_MD, ChunkConfig(strategy="recursive"))
        assert result.tier == "recursive"

    def test_profile_stats(self):
        prof = profile(RULED_MD)
        assert prof.headings == 3
        assert prof.main_level == 1
        assert prof.density > 0


# ---------- heading 档父子结构 ----------


class TestHeadingChunks:
    def test_parent_child_order_and_refs(self):
        result = chunk_document(RULED_MD, ChunkConfig())
        parents = [i for i, c in enumerate(result.chunks) if c.is_parent]
        assert parents, "应产出父块"
        for c in result.chunks:
            if c.is_parent:
                assert c.parent_idx == -1
            else:
                assert c.parent_idx in parents
        # 父块先于其子块出现（rag_upsert_doc 契约：父块须先写入）
        for i, c in enumerate(result.chunks):
            if not c.is_parent:
                assert c.parent_idx < i

    def test_parent_text_has_breadcrumb_prefix(self):
        result = chunk_document(RULED_MD, ChunkConfig())
        parent = next(c for c in result.chunks if c.is_parent)
        assert parent.text.startswith("【转专业管理办法】")
        assert parent.section_path == "转专业管理办法"

    def test_child_text_is_plain_body(self):
        result = chunk_document(RULED_MD, ChunkConfig())
        children = [c for c in result.chunks if not c.is_parent]
        assert children
        for c in children:
            assert not c.text.startswith("【")
            assert c.section_path == "转专业管理办法"

    def test_h2_without_h1_uses_h2_as_parent_level(self):
        text = "## 小节一\n\n正文甲。\n\n## 小节二\n\n正文乙。\n\n## 小节三\n\n正文丙。\n"
        result = chunk_document(text, ChunkConfig())
        assert result.tier == "heading"
        paths = {c.section_path for c in result.chunks}
        assert "小节一" in paths

    def test_breadcrumb_nesting(self):
        nodes = parse_markdown_tree(RULED_MD)
        by_title = {n.title: n for n in nodes}
        assert by_title["转专业管理办法"].path == "转专业管理办法"
        assert by_title["申请条件"].path == "转专业管理办法 > 申请条件"

    def test_no_heading_doc_single_node(self):
        nodes = parse_markdown_tree("纯正文，没有标题。")
        assert len(nodes) == 1
        assert nodes[0].level == 0
        assert nodes[0].body == "纯正文，没有标题。"


# ---------- recursive 档与 chunk_text ----------


class TestRecursiveChunks:
    def test_flat_no_parents(self):
        result = chunk_document(FLAT_MD, ChunkConfig())
        assert result.tier == "recursive"
        assert all(c.parent_idx == -1 for c in result.chunks)
        assert all(not c.is_parent for c in result.chunks)

    def test_no_chunk_exceeds_child_limit(self):
        result = chunk_document(FLAT_MD, ChunkConfig())
        assert all(len(c.text) <= 200 for c in result.chunks)


class TestChunkText:
    def test_paragraph_aggregation(self):
        paras = ["甲" * 50, "乙" * 50]
        chunks = chunk_text("\n\n".join(paras), 200, 40)
        assert chunks == ["\n\n".join(paras)]

    def test_sliding_window_overlap(self):
        chunks = chunk_text("字" * 500, 200, 40)
        assert all(len(c) == 200 for c in chunks[:-1])
        assert len(chunks) >= 3

    def test_exact_limit_no_split(self):
        assert chunk_text("字" * 200, 200, 40) == ["字" * 200]


# ---------- 校验与降级 ----------


class TestValidateAndFallback:
    def test_reject_empty(self):
        assert validate_chunks([], ChunkConfig()) != ""

    def test_reject_blank_chunk(self):
        chunks = [Chunk("  ", "", False, -1)]
        assert "空白块" in validate_chunks(chunks, ChunkConfig())

    def test_reject_oversize_child(self):
        chunks = [Chunk("字" * 201, "", False, -1)]
        assert "child_limit" in validate_chunks(chunks, ChunkConfig())

    def test_reject_bad_parent_ref(self):
        chunks = [Chunk("父", "", True, -1), Chunk("子", "", False, 9)]
        assert "无效" in validate_chunks(chunks, ChunkConfig())

    def test_heading_degrades_to_recursive(self):
        # 只有连续标题行、正文全空：heading 档产出为空 → 校验拒绝 → 降级 recursive
        text = "# 标题一\n\n# 标题二\n\n# 标题三\n"
        result = chunk_document(text, ChunkConfig())
        assert result.tier == "recursive"
        assert any("heading" in fb for fb in result.fallbacks)
        assert result.chunks  # recursive 档把标题行文本按段落切出


# ---------- 配置校验 ----------


class TestChunkConfig:
    def test_invalid_strategy(self):
        with pytest.raises(ValueError, match="切片策略"):
            ChunkConfig(strategy="bogus")

    def test_child_exceeds_parent(self):
        with pytest.raises(ValueError):
            ChunkConfig(child_limit=900)

    def test_overlap_ge_child(self):
        with pytest.raises(ValueError):
            ChunkConfig(overlap=200)


# ---------- 真实语料冒烟 ----------


class TestCorpusSmoke:
    def test_library_doc_chunks(self):
        path = _REPO_ROOT / "data" / "corpus" / "0007-library.md"
        if not path.is_file():
            pytest.skip("语料文件不存在")
        result = chunk_document(path.read_text(encoding="utf-8"), ChunkConfig())
        assert result.tier == "heading"
        children = [c for c in result.chunks if not c.is_parent]
        assert len(children) >= 3
        assert all(len(c.text) <= 200 for c in children)
        # 与存量索引同源：父块数 = H1 数（整篇聚合，parent_limit=800 内不滑切）
        assert sum(1 for c in result.chunks if c.is_parent) == 1
