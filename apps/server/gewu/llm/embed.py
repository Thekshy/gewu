"""自定义 Embeddings：text（标准 /embeddings 批量）与 ark_multimodal（火山方舟逐条）。

「轮子白嫖、验收自写」：ark_multimodal 无预置实现（接口不支持批量、返回结构与
标准不同），故统一自写 HTTP 客户端，行为对齐 Go internal/llm 的 embedText /
embedMultimodal——按 index 归位、缺 index 明确报错、multimodal 固定并发 4、
首错即停不烧整批配额。
"""

from __future__ import annotations

import queue
import threading
from concurrent.futures import ThreadPoolExecutor

import httpx

EMBED_CONCURRENCY = 4
EMBED_TIMEOUT = 45.0


class EmbedError(Exception):
    """向量化失败（网络/响应结构/维度异常）。"""


def _endpoint(base: str, path: str) -> str:
    return base.rstrip("/") + path


class GewuEmbeddings:
    """查询/入库向量化的统一入口（P14-1 供检索向量化；入库随 ingest ticket 接入）。"""

    def __init__(self, *, api_key: str, base_url: str, model: str, mode: str) -> None:
        if mode not in ("text", "ark_multimodal"):
            raise EmbedError(f"未知 EMBED_MODE: {mode}")
        self._api_key = api_key
        self._base = base_url
        self._model = model
        self._mode = mode

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """批量向量化（text 模式一次请求；ark 模式内部并发逐条，保序回填）。"""
        if not texts:
            return []
        if self._mode == "ark_multimodal":
            return self._embed_multimodal(texts)
        return self._embed_text(texts)

    def embed_query(self, text: str) -> list[float]:
        return self.embed_documents([text])[0]

    # ---------- text 模式：标准 OpenAI /embeddings，按 data[].index 归位 ----------

    def _embed_text(self, texts: list[str]) -> list[list[float]]:
        resp = self._post("/embeddings", {"model": self._model, "input": texts}).json()
        out: list[list[float]] = [[] for _ in texts]
        for d in resp.get("data", []):
            idx = d.get("index", -1)
            if isinstance(idx, int) and 0 <= idx < len(out):
                out[idx] = d.get("embedding", [])
        for i, v in enumerate(out):
            if not v:
                raise EmbedError(f"第 {i} 条向量缺失（响应未返回 index={i}）")
        return out

    # ---------- ark_multimodal：/embeddings/multimodal 不支持批量，逐条发 ----------

    def _embed_multimodal(self, texts: list[str]) -> list[list[float]]:
        out: list[list[float]] = [[] for _ in texts]
        jobs: queue.Queue[int] = queue.Queue()
        for i in range(len(texts)):
            jobs.put(i)
        first_err: list[Exception | None] = [None]
        mu = threading.Lock()

        def worker() -> None:
            while True:
                try:
                    i = jobs.get_nowait()
                except queue.Empty:
                    return
                with mu:
                    if first_err[0] is not None:
                        return  # 首错即停：不再发新请求
                try:
                    out[i] = self._embed_one_multimodal(texts[i])
                except Exception as e:  # noqa: BLE001 - 任一条失败取消整批
                    with mu:
                        if first_err[0] is None:
                            first_err[0] = EmbedError(f"第 {i} 条向量化失败: {e}")

        workers = min(EMBED_CONCURRENCY, len(texts))
        with ThreadPoolExecutor(max_workers=workers) as ex:
            for _ in range(workers):
                ex.submit(worker)
        if first_err[0] is not None:
            raise first_err[0]
        for i, v in enumerate(out):
            if not v:
                raise EmbedError(f"第 {i} 条向量缺失")
        return out

    def _embed_one_multimodal(self, text: str) -> list[float]:
        resp = self._post(
            "/embeddings/multimodal",
            {
                "model": self._model,
                "encoding_format": "float",
                "input": [{"type": "text", "text": text}],
            },
        ).json()
        data = resp.get("data") or {}
        emb = data.get("embedding") or []
        if not emb:
            raise EmbedError("向量响应为空")
        return emb

    # ---------- 公共 HTTP ----------

    def _post(self, path: str, body: dict) -> httpx.Response:
        if not self._api_key:
            raise EmbedError("EMBED_API_KEY 未配置")
        try:
            r = httpx.post(
                _endpoint(self._base, path),
                json=body,
                headers={"Authorization": f"Bearer {self._api_key}"},
                timeout=EMBED_TIMEOUT,
            )
            r.raise_for_status()
            return r
        except httpx.HTTPError as e:
            raise EmbedError(str(e)) from e
