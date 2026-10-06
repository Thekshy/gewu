#!/usr/bin/env python3
"""检索层独立评测（P15）：Recall@k / MRR / NDCG@k（指标族参考 WeKnora metric 包）。

数据源 eval/dataset.jsonl 的 factual + multi_hop 题（复用 expected_docs 文档级
gold，零重标）。进程内构建 Retriever 直打（不起服务）：
  cd apps/server && uv run python ../../eval/run_retrieval_eval.py --tag before
开关 --no-rewrite / --no-rerank 用于分离改写与精排的方差（GLM 温度 0 仍非确定，
归因须可分离）；--rerank-mode 选择精排引擎（P41：flash / bailian，缺省跟随
RERANK_MODE），并统计 rerank 跳延迟 p50/p95 与降级次数。报告落
eval/reports/retrieval-<时间戳>-<tag>.json。
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "apps" / "server"))

from gewu.config import Settings, load_dotenv  # noqa: E402
from gewu.llm.service import LLMService  # noqa: E402
from gewu.rag.retrieve import Retriever, build_reranker  # noqa: E402
from gewu.rag.store import Store  # noqa: E402

EVAL_TYPES = ("factual", "multi_hop")


class TimedReranker:
    """评测包装：统计 rerank 跳墙钟与降级次数（只计打分，不含召回/改写）。"""

    def __init__(self, inner) -> None:
        self.inner = inner
        self.lat_ms: list[float] = []
        self.fallbacks = 0

    def rerank(self, query: str, texts: list[str]) -> list[float]:
        t0 = time.perf_counter()
        try:
            return self.inner.rerank(query, texts)
        except Exception:
            self.fallbacks += 1
            raise
        finally:
            self.lat_ms.append((time.perf_counter() - t0) * 1000)

    def latency_summary(self) -> dict | None:
        if not self.lat_ms:
            return None
        lat = sorted(self.lat_ms)
        return {
            "p50": round(lat[len(lat) // 2]),
            "p95": round(lat[min(int(len(lat) * 0.95), len(lat) - 1)]),
        }


# ---------- 指标（doc 级、二值相关；WeKnora metric 的 Python 重写） ----------


def recall_at_k(ranked: list[str], gold: set[str], k: int) -> float:
    """前 k 命中 gold 的比例。"""
    if not gold:
        return 0.0
    return len(set(ranked[:k]) & gold) / len(gold)


def mrr(ranked: list[str], gold: set[str]) -> float:
    """首个 gold 命中位次的倒数。"""
    for i, d in enumerate(ranked, start=1):
        if d in gold:
            return 1.0 / i
    return 0.0


def ndcg_at_k(ranked: list[str], gold: set[str], k: int) -> float:
    """二值相关的 NDCG@k（对数折减增益）。"""
    dcg = sum(
        1.0 / (i ** 0.5) if i > 1 else 1.0
        for i, d in enumerate(ranked[:k], start=1)
        if d in gold
    )
    ideal = sum(1.0 / (i ** 0.5) if i > 1 else 1.0 for i in range(1, min(len(gold), k) + 1))
    return dcg / ideal if ideal > 0 else 0.0


# ---------- 评测主体 ----------


def load_cases(dataset: Path, extra: Path | None) -> list[dict]:
    cases = []
    for line in dataset.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if row.get("type") in EVAL_TYPES and row.get("expected_docs"):
            cases.append(
                {
                    "id": row["id"],
                    "type": row["type"],
                    "question": row["question"],
                    "gold": set(row["expected_docs"]),
                    "tier": row.get("tier", "must_pass"),  # P40-2 三层分化，缺省必过
                }
            )
    if extra is not None and extra.is_file():
        for line in extra.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            if row.get("query") and row.get("gold"):
                cases.append(
                    {
                        "id": f"{row.get('src_id', 'x')}-v{len(cases)}",
                        "type": "variant",
                        "question": row["query"],
                        "gold": set(row["gold"]),
                        "tier": "must_pass",
                    }
                )
    return cases


def main() -> int:
    ap = argparse.ArgumentParser(description="检索层评测：Recall@k / MRR / NDCG@k")
    ap.add_argument("--tag", default="", help="报告标注（如 before / after）")
    ap.add_argument("--k", type=int, default=0, help="检索条数（缺省 RETRIEVAL_K）")
    ap.add_argument("--no-rewrite", action="store_true", help="跳过查询改写（分离方差）")
    ap.add_argument("--no-rerank", action="store_true", help="关闭精排")
    ap.add_argument(
        "--rerank-mode",
        choices=("flash", "bailian"),
        default=None,
        help="精排引擎（P41；缺省跟随 .env 的 RERANK_MODE，非法/无 key 时实际退 off）",
    )
    ap.add_argument("--rounds", type=int, default=1, help="重复轮数（非确定性方差观测）")
    ap.add_argument(
        "--extra",
        type=Path,
        default=None,
        help="附加查询集（如 eval/retrieval-queries.jsonl 口语化变体，gold 随行）",
    )
    args = ap.parse_args()

    load_dotenv()  # 入口负责 .env 发现（对齐 main.py 惯例）
    settings = Settings.load()
    k = args.k or settings.retrieval_k
    store = Store(settings.pg_dsn)
    llm = LLMService(settings)
    mode = "off" if args.no_rerank else (args.rerank_mode or settings.rerank_mode)
    reranker = build_reranker(
        mode,
        llm,
        api_key=settings.dashscope_api_key,
        endpoint=settings.bailian_rerank_endpoint,
        model=settings.bailian_rerank_model,
    )
    timed = TimedReranker(reranker) if reranker is not None else None
    engine = mode if reranker is not None else "off"
    retriever = Retriever(store, k, llm, reranker=timed, rerank_passage=settings.rerank_passage)
    retriever.rewriter._enabled = not args.no_rewrite  # noqa: SLF001 - 评测开关

    cases = load_cases(REPO_ROOT / "eval" / "dataset.jsonl", args.extra)
    if not cases:
        print("没有可评测的题目（需要 factual/multi_hop 且带 expected_docs）")
        return 1

    per_query: list[dict] = []
    rounds_metrics: list[dict[str, float]] = []
    for r in range(args.rounds):
        sums = {"recall": 0.0, "mrr": 0.0, "ndcg": 0.0}
        for case in cases:
            hits = retriever.search(case["question"], k)
            ranked = list(dict.fromkeys(h.doc_id for h in hits))
            rec = recall_at_k(ranked, case["gold"], k)
            m = mrr(ranked, case["gold"])
            n = ndcg_at_k(ranked, case["gold"], k)
            sums["recall"] += rec
            sums["mrr"] += m
            sums["ndcg"] += n
            if r == 0:
                per_query.append(
                    {
                        "id": case["id"],
                        "type": case["type"],
                        "tier": case["tier"],
                        "gold": sorted(case["gold"]),
                        "ranked_docs": ranked,
                        "recall": round(rec, 4),
                        "mrr": round(m, 4),
                        "ndcg": round(n, 4),
                    }
                )
        rounds_metrics.append({name: v / len(cases) for name, v in sums.items()})

    n_cases = len(cases)
    avg = {
        name: sum(r[name] for r in rounds_metrics) / args.rounds
        for name in ("recall", "mrr", "ndcg")
    }
    # P40-2 分 tier 汇总（第一轮的逐题结果聚合）
    tiers: dict[str, dict] = {}
    for pq in per_query:
        t = tiers.setdefault(
            pq["tier"], {"cases": 0, "recall": 0.0, "mrr": 0.0, "ndcg": 0.0}
        )
        t["cases"] += 1
        t["recall"] += pq["recall"]
        t["mrr"] += pq["mrr"]
        t["ndcg"] += pq["ndcg"]
    for t in tiers.values():
        for m in ("recall", "mrr", "ndcg"):
            t[m] = round(t[m] / t["cases"], 4)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    report = {
        "tag": args.tag,
        "timestamp": stamp,
        "k": k,
        "cases": n_cases,
        "rounds": args.rounds,
        "no_rewrite": args.no_rewrite,
        "no_rerank": args.no_rerank,
        "rerank_engine": engine,
        "rerank_passage": settings.rerank_passage,
        "rerank_latency_ms": timed.latency_summary() if timed else None,
        "rerank_fallbacks": timed.fallbacks if timed else 0,
        "extra": str(args.extra) if args.extra else "",
        "metrics": {f"{name}@{k}" if name != "mrr" else "mrr": round(v, 4)
                    for name, v in avg.items()},
        "by_tier": tiers,
        "per_round": [{name: round(v, 4) for name, v in r.items()} for r in rounds_metrics],
        "per_query": per_query,
    }
    out = REPO_ROOT / "eval" / "reports" / f"retrieval-{stamp}{'-' + args.tag if args.tag else ''}.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    label = f"[{args.tag}] " if args.tag else ""
    print(f"{label}检索评测：{n_cases} 题 × {args.rounds} 轮  k={k}"
          f"  rewrite={'off' if args.no_rewrite else 'on'}  rerank={engine}"
          f"  passage={settings.rerank_passage}")
    print(f"  Recall@{k}={avg['recall']:.4f}  MRR={avg['mrr']:.4f}  NDCG@{k}={avg['ndcg']:.4f}")
    for tname, t in sorted(tiers.items()):
        print(f"  [{tname}] n={t['cases']}  R={t['recall']:.4f}  MRR={t['mrr']:.4f}  NDCG={t['ndcg']:.4f}")
    if timed:
        print(f"  rerank 跳延迟：{timed.latency_summary()}  fallbacks={timed.fallbacks}")
    print(f"  报告：{out}")
    store.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
