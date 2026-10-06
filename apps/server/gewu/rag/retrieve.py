"""混合检索管线（P15 起对齐 WeKnora：加权 RRF 融合 + 精排阈值容错）。

漏斗：BM25/向量各取 pool_n → 加权 RRF（向量 0.7/关键词 0.3，归一 [0,1]）→
（有 key 且开启 rerank）精排（复合分排序 + 阈值过滤，全滤空自动退化；P41 起
引擎可换：flash=LLM 打分 / bailian=百炼专用 rerank，见 build_reranker）→
按 parent_id 回取父块去重。查询侧口语→术语改写（有 key 即启用）。
向量路瞬时失败退化为纯关键词：FTS 分按列表最大值归一，分数标尺不失效。
"""

from __future__ import annotations

import json
import threading
from typing import Protocol

import httpx

from gewu.rag.store import ChunkRow, DocMeta, Hit, MissingVectorsError, Scored, rrf_fuse

POOL_N = 20
RRF_K = 60
# 精排容错（WeKnora rerank 同款语义）：阈值作用于 LLM 模型分（0~10），
# 全滤空时阈值 ×0.7 重试一次（下限 FLOOR），仍空保留 top1（须 ≥ FLOOR）。
RERANK_FLOOR = 1.5
RERANK_MODEL_WEIGHT = 0.7  # 复合分 = 0.7×(模型分/10) + 0.3×融合归一分


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


def _normalize_by_max(scored: list[Scored]) -> list[Scored]:
    """按列表最大值归一到 [0,1]（无界 FTS 分在单路退化时的统一标尺）。"""
    m = max((s.score for s in scored), default=0.0)
    if m <= 0:
        return [Scored(s.id, 0.0) for s in scored]
    return [Scored(s.id, s.score / m) for s in scored]


def _titled_passage(meta: DocMeta | None, row: ChunkRow | None) -> str:
    """titled 精排口径（P41）：title + 标题面包屑 + 正文，与 ingest.embed_content
    同形状——嵌入吃到标题上下文而裸正文打分认不出，等于精排在撤销向量侧的标题
    匹配（WeKnora ModelPassage 的原教训）。行缺失返空串（后续换空格送 API）。"""
    if row is None:
        return ""
    title = meta.title if meta and meta.title else row.doc_id
    lines = [title]
    if row.section_path:
        lines.append(row.section_path)
    lines.append(row.text)
    return "\n".join(lines)


class Retriever:
    """混合检索器：BM25 + 向量 → 加权 RRF 融合 →（可选）LLM 精排 → 父子扩展。"""

    def __init__(
        self,
        store: RetrievalStore,
        k: int,
        client: RagLLM | None,
        *,
        reranker: LLMReranker | BailianReranker | None = None,
        pool_n: int = POOL_N,
        rrf_k: int = RRF_K,
        vector_weight: float = 0.7,
        keyword_weight: float = 0.3,
        rerank_threshold: float = 2.0,
        rerank_passage: str = "body",
    ) -> None:
        self.store = store
        self.k = k
        self.client = client
        self.rewriter = Rewriter(client)
        self.reranker = reranker
        self.pool_n = pool_n
        self.rrf_k = rrf_k
        self.vector_weight = vector_weight
        self.keyword_weight = keyword_weight
        self.rerank_threshold = rerank_threshold
        self.rerank_passage = rerank_passage

    def search(self, query: str, k: int = 0, *, expand: bool = True) -> list[Hit]:
        """expand=False 供工具路径跳过二次改写——query 已是 LLM 提炼的关键词串，
        再过 rewriter 构成双重改写（P24-2；线上实证重复词「食堂位置」×2）。"""
        if k <= 0:
            k = self.k
        if expand:
            query = self.rewriter.expand(query)  # 口语 → 政策术语（无 key 时原样返回）

        has_emb = self.store.has_embeddings()
        if not has_emb:
            raise MissingVectorsError("向量索引缺失，请配好 EMBED_* 后用 --rebuild 重建索引")

        pool = max(self.pool_n, k)
        bm_scored = self.store.bm25_search(query, pool)
        vec_scored = self._vector_scored(query, pool)

        if vec_scored:
            fused = rrf_fuse(
                [[s.id for s in bm_scored], [s.id for s in vec_scored]],
                self.rrf_k,
                [self.keyword_weight, self.vector_weight],
            )
        else:
            # 单路退化（向量路瞬时失败/无 key）：FTS 分按列表最大值归一，
            # 保持 [0,1] 分数标尺（WeKnora keyword-only 同款）。
            fused = _normalize_by_max(bm_scored)

        ranked = self._rerank_or_keep(query, fused, k)
        hits = self._expand_to_parents([s.id for s in ranked], k)
        brief = " ".join(f"{h.doc_id}#{h.seq}" for h in hits) or "（空命中，走拒答路径）"
        print(f"[rag] q={query[:40]!r} 命中 {len(hits)}：{brief}")
        return hits

    def _vector_scored(self, query: str, k: int) -> list[Scored]:
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
            return self.store.vector_search(vecs[0], k)
        except Exception as e:  # noqa: BLE001
            print(f"[rag] 向量检索失败，本查询退化为纯 BM25：{e}")
            return []

    def _rerank_or_keep(self, query: str, fused: list[Scored], k: int) -> list[Scored]:
        """LLM 精排（复合分排序 + 阈值过滤）；失败/关闭/候选不足时原样截断。

        复合分 = RERANK_MODEL_WEIGHT×(模型分/10) + (1-w)×融合归一分，平局保持
        融合序；阈值作用于模型分，全滤空时按 WeKnora 语义退化（×0.7 重试、
        保底 top1），质量不足宁可少给——空命中走上游拒答路径。
        """
        if (
            self.reranker is None
            or self.client is None
            or not self.client.has_key()
            or len(fused) <= k
        ):
            return fused[:k]
        rows: dict[int, ChunkRow] = self.store.chunk_rows([s.id for s in fused])
        if self.rerank_passage == "titled":
            metas = self.store.doc_meta_map([r.doc_id for r in rows.values()])
            texts = [
                _titled_passage(
                    metas.get(rows[s.id].doc_id) if s.id in rows else None, rows.get(s.id)
                )
                for s in fused
            ]
        else:
            texts = [rows[s.id].text if s.id in rows else "" for s in fused]
        try:
            llm_scores = self.reranker.rerank(query, texts)
        except Exception as e:  # noqa: BLE001
            print(f"[rag] rerank 失败，退回 RRF 粗排顺序：{e}")
            return fused[:k]
        composite = [
            RERANK_MODEL_WEIGHT * (llm_scores[i] / 10.0)
            + (1.0 - RERANK_MODEL_WEIGHT) * fused[i].score
            for i in range(len(fused))
        ]
        order = sorted(range(len(fused)), key=lambda i: (-composite[i], i))
        kept = [i for i in order if llm_scores[i] >= self.rerank_threshold][:k]
        if not kept:
            relaxed = max(self.rerank_threshold * 0.7, RERANK_FLOOR)
            kept = [i for i in order if llm_scores[i] >= relaxed][:k]
            if kept:
                print(f"[rag] 阈值 {self.rerank_threshold} 全滤空，退化至 {relaxed:.2f} 后保留")
        if not kept and order and llm_scores[order[0]] >= RERANK_FLOOR:
            kept = [order[0]]  # 保底：最优候选勉强及格即保留一条
        return [fused[i] for i in kept]

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

    def __init__(self, client: RagLLM | None, *, enabled: bool = True) -> None:
        self._client = client
        self._enabled = enabled
        self._mu = threading.Lock()
        self._cache: dict[str, str] = {}

    def expand(self, query: str) -> str:
        if not self._enabled or self._client is None or not self._client.has_key():
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
        # 去重（P24-2）：改写串与原词按空格 token 保序去重（线上实证「食堂位置」×2）
        result = " ".join(dict.fromkeys(result.split())) if result else result
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


BAILIAN_RERANK_TIMEOUT = 5.0  # 冻结池实测 p95 414ms，>10x 余量；超时即异常走降级
BAILIAN_RERANK_PATH = "/api/v1/services/rerank/text-rerank/text-rerank"

# 与 RERANK_SYSTEM 的 10/5/0 锚点语义对齐（评测口径含此 instruct，省略会分数漂移）
BAILIAN_INSTRUCT = (
    "为校园制度查询评估候选段落相关性：直接包含回答查询所需核心条款/数字/流程"
    "的段落给最高分，主题相关但仅为背景信息的居中，与查询无关的给零分。"
)


class BailianReranker:
    """百炼 text-rerank 精排器（P41）：专用 rerank 替换 LLM 打分头。

    嵌套契约（qwen3.7-text-rerank / gte-rerank-v2；qwen3-rerank 才是平铺契约，
    用混即 404）。relevance_score ∈ [0,1] 且是请求内相对分（官方声明不可跨请求
    比较），×10 对齐 0-10 消费标尺。异常一律抛出交 _rerank_or_keep 现有降级链
    兜底；单次调用不重试——重试只拉长关键路径（flash_hard 教训：p95 8s→9.7s）。
    """

    def __init__(
        self,
        api_key: str,
        *,
        endpoint: str,
        model: str = "qwen3.7-text-rerank",
        client: httpx.Client | None = None,
        timeout: float = BAILIAN_RERANK_TIMEOUT,
    ) -> None:
        self._api_key = api_key
        self._endpoint = endpoint.rstrip("/")
        self._model = model
        self._client = client  # 注入缝：测试传 MockTransport 包装的 Client（websearch 同款）
        self._timeout = timeout

    def rerank(self, query: str, candidates: list[str]) -> list[float]:
        if not candidates:
            return []
        docs = [t or " " for t in candidates]  # 空串候选会被 API 拒，换空格
        body = {
            "model": self._model,
            "input": {"query": query, "documents": docs},
            "parameters": {
                # top_n 必须全量：截断会丢候选使 index 对位缺项（WeKnora 同注释）
                "top_n": len(docs),
                "return_documents": False,
                "instruct": BAILIAN_INSTRUCT,
            },
        }
        hc = self._client or httpx
        resp = hc.post(
            f"{self._endpoint}{BAILIAN_RERANK_PATH}",
            json=body,
            headers={"Authorization": f"Bearer {self._api_key}"},
            timeout=self._timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("code"):
            # DashScope 部分失败以 HTTP 200 + 顶层 code/message 返回（WeKnora 同款检查）
            raise ValueError(f"bailian rerank code={data['code']}: {data.get('message', '')}")
        results = (data.get("output") or {}).get("results") or []
        return _parse_bailian_scores(results, len(docs))


def _parse_bailian_scores(results: list, n: int) -> list[float]:
    """results 按 index 对位映射为 0-10 分数；数量/越界/重复/分数非法即抛错。

    从严不补 0：补 0 会让缺失候选被阈值静默滤掉，掩盖契约漂移（对齐
    parse_scores 的严格惯例，异常走 RRF 降级链）。
    """
    if len(results) != n:
        raise ValueError(f"bailian results 数量 {len(results)} 与候选数 {n} 不符")
    scores: list[float | None] = [None] * n
    for r in results:
        i = r.get("index")
        if not isinstance(i, int) or isinstance(i, bool) or not 0 <= i < n or scores[i] is not None:
            raise ValueError(f"bailian results index 非法或重复: {i!r}")
        v = r.get("relevance_score")
        if not isinstance(v, (int, float)) or isinstance(v, bool) or not 0.0 <= v <= 1.0:
            raise ValueError(f"bailian relevance_score 非法: {v!r}")
        scores[i] = float(v) * 10.0
    return scores  # type: ignore[return-value]  # 数量已校验，无 None 残留


def build_reranker(
    mode: str,
    llm: RagLLM | None,
    *,
    api_key: str = "",
    endpoint: str = "",
    model: str = "qwen3.7-text-rerank",
) -> LLMReranker | BailianReranker | None:
    """RERANK_MODE → 精排器（app 装配与评测脚本共用收口，别各写一份）。

    off / 缺凭证返 None（= 不精排）；bailian 缺 key 告警后退化为 RRF 直跑——
    不崩不偷换 flash（对齐 IQS_API_KEY 空值关断惯例）。
    """
    if mode == "off":
        return None
    if mode == "flash":
        return LLMReranker(llm) if llm is not None else None
    if mode == "bailian":
        if not api_key:
            print("[rag] RERANK_MODE=bailian 但 DASHSCOPE_API_KEY 为空，退化为不精排（RRF 直跑）")
            return None
        return BailianReranker(api_key, endpoint=endpoint, model=model)
    raise ValueError(f"未知 RERANK_MODE {mode!r}（合法：flash|bailian|off，on=flash 别名）")
