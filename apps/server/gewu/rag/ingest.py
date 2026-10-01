"""语料入库（P15：Go ingest.go 的 Python 移植 + WeKnora 嵌入内容拼装）。

corpus/*.md → frontmatter 解析 → 策略链切片 → 子块嵌入内容拼装（文档标题 +
面包屑 + 子块正文；text 列仍存纯正文，FTS 行为不变）→ 批量向量化（L2 归一 +
退避重试）→ rag_upsert_doc 幂等写入。依赖经 RagLLM/Store 协议注入，
不横向 import llm（lint-arch 分层）。
"""

from __future__ import annotations

import math
import re
import time
from dataclasses import dataclass
from pathlib import Path

from gewu.rag.chunker import ChunkConfig, ChunkResult, chunk_document

# frontmatter：文件头部的 YAML 围栏（Go frontmatterRe 同款）。
_FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n?", re.DOTALL)

EMBED_BATCH = 32  # 批量向量化分批大小（Go embedBatch 同值）
_EMBED_RETRIES = 3  # 批次失败退避重试（WeKnora BatchEmbedWithPool 语义的精简版）


@dataclass(frozen=True)
class ParsedDoc:
    """解析后的语料文档（meta 键小写：title/source/updated）。"""

    meta: dict[str, str]
    text: str


def parse_doc(path: Path) -> ParsedDoc:
    """解析带 frontmatter 的 Markdown：title / source / updated + 正文。

    缺省 title=文件名去扩展、source=钱塘大学、updated=""（Go ParseDoc 同款）。
    """
    raw = path.read_text(encoding="utf-8")
    meta = {
        "title": path.stem,
        "source": "钱塘大学",
        "updated": "",
    }
    text = raw
    if m := _FRONTMATTER_RE.match(text):
        for line in m.group(1).split("\n"):
            key, sep, val = line.partition(":")
            if sep:
                meta[key.strip().lower()] = val.strip()
        text = text[m.end() :]
    return ParsedDoc(meta=meta, text=text.strip())


def embed_content(title: str, section_path: str, body: str) -> str:
    """子块嵌入内容拼装（WeKnora knowledge_index_content 同语义）：
    文档标题 + 标题面包屑 + 正文——向量吃到标题上下文，检索匹配单元不变。
    """
    lines = [title]
    if section_path:
        lines.append(section_path)
    lines.append(body)
    return "\n".join(lines)


def _l2_normalize(vecs: list[list[float]]) -> list[list[float]]:
    """L2 归一（Go EmbedBatched 同款；已归一向量幂等无害）。"""
    out: list[list[float]] = []
    for v in vecs:
        norm = math.sqrt(sum(x * x for x in v))
        out.append([x / norm for x in v] if norm > 0 else v)
    return out


def embed_batched(embedder, texts: list[str]) -> list[list[float]]:
    """分批向量化 + 批次级退避重试；任一批最终失败即抛错（入库不静默降级）。"""
    all_vecs: list[list[float]] = []
    for i in range(0, len(texts), EMBED_BATCH):
        batch = texts[i : i + EMBED_BATCH]
        last_err: Exception | None = None
        for attempt in range(_EMBED_RETRIES):
            try:
                vecs = embedder.embed(batch)
                if len(vecs) != len(batch):
                    raise ValueError(f"向量数 {len(vecs)} 与块数 {len(batch)} 不符")
                all_vecs.extend(_l2_normalize(vecs))
                last_err = None
                break
            except Exception as e:  # noqa: BLE001 - 重试后仍失败由外层报错退出
                last_err = e
                if attempt < _EMBED_RETRIES - 1:
                    time.sleep(2**attempt)  # 1s / 2s 退避
        if last_err is not None:
            raise RuntimeError(f"向量化第 {i // EMBED_BATCH + 1} 批失败：{last_err}") from last_err
    return all_vecs


@dataclass(frozen=True)
class IngestSummary:
    """入库结果统计（CLI 打印与留档用）。"""

    docs: int
    chunks: int
    parents: int
    children: int
    embedded: bool
    tiers: dict[str, str]  # doc_id → 实际生效档位（策略链透明度）


def run_ingest(
    store,
    embedder,
    corpus_dir: Path,
    cfg: ChunkConfig,
    *,
    rebuild: bool = False,
    no_embed: bool = False,
    log=print,
) -> IngestSummary:
    """语料入库主流程。store 需实现 wipe/upsert_doc；embedder 需实现 embed
    （RagLLM 协议子集）。no_embed 只建 FTS 索引——检索侧会以
    MissingVectorsError 明确拒绝（不静默降级成"假成功"）。
    """
    if not no_embed and embedder is None:
        raise ValueError("未提供 embedder；确要仅建关键词（FTS）索引请显式传 no_embed=True")
    if rebuild:
        store.wipe()
        log("[ingest] 已清空旧索引（--rebuild）")

    files = sorted(corpus_dir.glob("*.md"))
    if not files:
        raise FileNotFoundError(f"未找到语料文件：{corpus_dir}/*.md")

    tiers: dict[str, str] = {}
    total = parents = children = 0
    for f in files:
        doc = parse_doc(f)
        doc_id = f.stem
        result: ChunkResult = chunk_document(doc.text, cfg)
        tiers[doc_id] = result.tier
        for fb in result.fallbacks:
            log(f"[ingest] {doc_id}: {fb}，降级处理")

        records: list[dict] = []
        child_texts = [(i, c.text) for i, c in enumerate(result.chunks) if not c.is_parent]
        vecs: list[list[float]] | None = None
        if not no_embed and child_texts:
            contents = [
                embed_content(doc.meta.get("title", doc_id), result.chunks[i].section_path, t)
                for i, t in child_texts
            ]
            vecs = embed_batched(embedder, contents)
        vi = 0
        for c in result.chunks:
            rec: dict = {
                "text": c.text,
                "section_path": c.section_path,
                "is_parent": c.is_parent,
                "parent_idx": c.parent_idx,
            }
            if not c.is_parent and vecs is not None:
                rec["vec"] = vecs[vi]
                vi += 1
            records.append(rec)

        title = doc.meta.get("title") or doc_id
        store.upsert_doc(
            {
                "id": doc_id,
                "title": title,
                "source": doc.meta.get("source", ""),
                "updated": doc.meta.get("updated", ""),
            },
            records,
        )
        n_parents = sum(1 for r in records if r["is_parent"])
        total += len(records)
        parents += n_parents
        children += len(records) - n_parents
        mode = "FTS+向量" if not no_embed else "仅FTS"
        log(
            f"  ✓ {doc_id}  {len(records)} 块（父 {n_parents}/子 {len(records) - n_parents}）"
            f"  [{result.tier} | {mode}]"
        )

    return IngestSummary(
        docs=len(files),
        chunks=total,
        parents=parents,
        children=children,
        embedded=not no_embed,
        tiers=tiers,
    )
