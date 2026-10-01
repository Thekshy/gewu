"""入库 CLI 装配入口（P15）。角色同 main.py：只做 config/llm/rag 装配与参数解析。

用法：uv run python ingest_main.py [--rebuild] [--no-embed]
（或 make ingest [REBUILD=1] [NO_EMBED=1]）
"""

from __future__ import annotations

import argparse
import sys

from gewu.config import Settings, load_dotenv
from gewu.llm.service import LLMService
from gewu.rag.chunker import ChunkConfig
from gewu.rag.ingest import run_ingest
from gewu.rag.store import Store


def main() -> int:
    ap = argparse.ArgumentParser(description="语料入库：corpus/*.md → 切片 → 向量化 → PG")
    ap.add_argument(
        "--rebuild", action="store_true", help="清空旧索引后重建（换切片/embedding 后必加）"
    )
    ap.add_argument(
        "--no-embed",
        action="store_true",
        help="只建关键词（FTS）索引（检索侧会明确拒绝向量缺失）",
    )
    args = ap.parse_args()

    load_dotenv()  # 入口负责 .env 发现（对齐 main.py 惯例）
    settings = Settings.load()
    cfg = ChunkConfig(
        strategy=settings.chunk_strategy,
        parent_limit=settings.chunk_parent_limit,
        child_limit=settings.chunk_child_limit,
        overlap=settings.chunk_overlap,
    )
    if not args.no_embed and not settings.embed_api_key:
        print("未配置 EMBED_API_KEY，无法向量化；确要仅建关键词（FTS）索引请显式加 --no-embed")
        return 1

    store = Store(settings.pg_dsn, ensure=True)
    try:
        llm = LLMService(settings)  # 不挂预算：入库向量化不占对话日预算
        summary = run_ingest(
            store,
            llm,
            settings.data_dir / "corpus",
            cfg,
            rebuild=args.rebuild,
            no_embed=args.no_embed,
        )
        stats = store.get_stats()
        print(
            f"[ingest] 完成：{summary.docs} 篇 / {summary.chunks} 块"
            f"（父 {summary.parents}/子 {summary.children}）"
            f"  库内 docs={stats.docs} chunks={stats.chunks}"
        )
        return 0
    finally:
        store.close()


if __name__ == "__main__":
    sys.exit(main())
