"""语料入库 CLI：data/corpus/*.md → 解析 → 分块 →（可选）向量化 → SQLite。

用法：python -m app.rag.ingest [--no-embed] [--rebuild]
无 LLM_API_KEY 时自动降级为仅 BM25 索引。
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

import numpy as np

from ..config import get_settings
from ..llm import embed, has_key
from .store import Store

FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n?", re.DOTALL)

EMBED_BATCH = 32


def parse_doc(path: Path) -> tuple[dict, str]:
    """解析带 frontmatter 的 Markdown：title / source / updated + 正文。"""
    meta = {"title": path.stem, "source": "钱塘大学", "updated": ""}
    text = path.read_text("utf-8")
    m = FRONTMATTER_RE.match(text)
    if m:
        for line in m.group(1).splitlines():
            key, sep, val = line.partition(":")
            if sep:
                meta[key.strip().lower()] = val.strip()
        text = text[m.end():]
    return meta, text.strip()


def chunk_text(text: str, limit: int = 450, overlap: int = 80) -> list[str]:
    """按段落聚合切块；超长单段滑动硬切，保留 overlap 字符衔接。"""
    paras = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    chunks: list[str] = []
    buf = ""
    for para in paras:
        candidate = f"{buf}\n\n{para}".strip() if buf else para
        if len(candidate) <= limit:
            buf = candidate
            continue
        if buf:
            chunks.append(buf)
        while len(para) > limit:
            chunks.append(para[:limit])
            para = para[limit - overlap:]
        buf = para
    if buf:
        chunks.append(buf)
    return chunks


def embed_batched(chunks: list[str]) -> list[np.ndarray]:
    """分批向量化并做 L2 归一化。"""
    vecs: list[list[float]] = []
    for i in range(0, len(chunks), EMBED_BATCH):
        vecs.extend(embed(chunks[i : i + EMBED_BATCH]))
    out = []
    for v in vecs:
        arr = np.asarray(v, dtype=np.float32)
        norm = np.linalg.norm(arr)
        out.append(arr / norm if norm > 0 else arr)
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="语料入库")
    parser.add_argument("--no-embed", action="store_true", help="只建 BM25 索引")
    parser.add_argument("--rebuild", action="store_true", help="删除旧索引后重建")
    args = parser.parse_args(argv)

    s = get_settings()
    if args.rebuild and s.index_path and Path(s.index_path).exists():
        Path(s.index_path).unlink()

    store = Store(s.index_path)  # type: ignore[arg-type]
    use_embed = (not args.no_embed) and has_key()
    files = sorted(Path(s.corpus_dir).glob("*.md"))  # type: ignore[union-attr]
    if not files:
        print(f"!! 未找到语料文件：{s.corpus_dir}/*.md")
        return 1

    for f in files:
        meta, text = parse_doc(f)
        chunks = chunk_text(text)
        vectors = embed_batched(chunks) if (use_embed and chunks) else None
        store.upsert_doc(
            doc_id=f.stem,
            title=str(meta.get("title", f.stem)),
            source=str(meta.get("source", "")),
            updated=str(meta.get("updated", "")),
            chunk_texts=chunks,
            vectors=vectors,
        )
        mode = "BM25+向量" if vectors is not None else "仅BM25"
        print(f"  ✓ {f.stem}  {len(chunks)} 块  [{mode}]")

    stats = store.stats()
    print(f"\n入库完成：{stats['docs']} 篇文档 / {stats['chunks']} 个 chunk / "
          f"向量化={'是' if stats['embedded'] else '否'} → {s.index_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
