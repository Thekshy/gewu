from pathlib import Path

from app.rag.ingest import chunk_text, parse_doc


def test_chunk_text_splits_long_paragraph():
    text = "长句。" * 300  # 900 字
    chunks = chunk_text(text, limit=450, overlap=80)
    assert len(chunks) > 1
    assert all(len(c) <= 450 for c in chunks)


def test_chunk_text_keeps_short_text_whole():
    assert chunk_text("只有一段短文本。") == ["只有一段短文本。"]


def test_parse_doc_frontmatter(tmp_path: Path):
    f = tmp_path / "0001-demo.md"
    f.write_text(
        "---\ntitle: 演示文档\nsource: 教务处\nupdated: 2026-01-01\n---\n\n正文第一段。\n\n正文第二段。",
        encoding="utf-8",
    )
    meta, text = parse_doc(f)
    assert meta["title"] == "演示文档"
    assert meta["source"] == "教务处"
    assert text.startswith("正文第一段")
