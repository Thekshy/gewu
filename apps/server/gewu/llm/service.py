"""LLMService：检索/路由等辅助调用的统一门面（Go llm.Client 最小面的 Python 等价物）。

rag 层只依赖 has_key/chat/embed 三件（internal/rag LLMer 接口同款），chat 层
（P14-2 起）复用同一工厂。用量记账随 P14-7 预算域接入。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from gewu.llm.chat import Usage, make_chat_model, parse_finish_reason, parse_usage
from gewu.llm.embed import GewuEmbeddings

if TYPE_CHECKING:
    from gewu.config import Settings


class LLMService:
    """双模型缓存 + 门面方法。线程安全：ChatOpenAI invoke 可并发，模型惰性建一次。"""

    def __init__(self, settings: Settings) -> None:
        self._s = settings
        self._models: dict[bool, object] = {}
        self._embeddings: GewuEmbeddings | None = None

    # ---------- Chat ----------

    def _model(self, small: bool):
        if small not in self._models:
            self._models[small] = make_chat_model(self._s, small=small)
        return self._models[small]

    def chat(
        self,
        messages: list[tuple[str, str]],
        *,
        small: bool = False,
        json_mode: bool = False,
        temperature: float = 0.0,
        max_tokens: int = 2048,
    ) -> str:
        """一次补全调用，返回文本内容。messages 为 (role, content) 二元组列表。"""
        kwargs: dict = {"temperature": temperature, "max_tokens": max_tokens}
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        lc_messages = [
            SystemMessage(content=c) if role == "system" else HumanMessage(content=c)
            for role, c in messages
        ]
        resp = self._model(small).bind(**kwargs).invoke(lc_messages)
        return _content_text(resp)

    def chat_full(
        self,
        messages: list[tuple[str, str]],
        *,
        small: bool = False,
        temperature: float = 0.0,
        max_tokens: int = 2048,
    ) -> AIMessage:
        """同 chat 但返回完整 AIMessage（finish_reason/usage 解析用；P14-2 SSE 侧主通道）。"""
        model = self._model(small).bind(temperature=temperature, max_tokens=max_tokens)
        lc_messages = [
            SystemMessage(content=c) if role == "system" else HumanMessage(content=c)
            for role, c in messages
        ]
        return model.invoke(lc_messages)  # type: ignore[return-value]

    # ---------- Embed ----------

    def embeddings(self) -> GewuEmbeddings:
        if self._embeddings is None:
            self._embeddings = GewuEmbeddings(
                api_key=self._s.embed_api_key,
                base_url=self._s.embed_base_url,
                model=self._s.embed_model,
                mode=self._s.embed_mode,
            )
        return self._embeddings

    def embed(self, texts: list[str]) -> list[list[float]]:
        return self.embeddings().embed_documents(texts)

    # ---------- 键状态（health / 检索降级判定） ----------

    def has_key(self) -> bool:
        return bool(self._s.llm_api_key)

    def has_embed_key(self) -> bool:
        return bool(self._s.embed_api_key)


def _content_text(message: AIMessage) -> str:
    """AIMessage.content 归一为纯文本（兼容 string 与分段列表两种形态）。"""
    c = message.content
    if isinstance(c, str):
        return c
    parts: list[str] = []
    for seg in c:
        if isinstance(seg, dict):
            parts.append(str(seg.get("text", "")))
        else:
            parts.append(str(seg))
    return "".join(parts)


__all__ = ["LLMService", "Usage", "parse_finish_reason", "parse_usage"]
