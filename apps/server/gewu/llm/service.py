"""LLMService：检索/路由等辅助调用的统一门面（Go llm.Client 最小面的 Python 等价物）。

rag 层只依赖 has_key/chat/embed 三件（internal/rag LLMer 接口同款），agent 编排层
复用同一工厂（流式/工具调用随 P14-2/4 接入）。用量记账随 P14-7 预算域接入；
P23 起双写：全局 budget（闸）+ per-user usage（账，归属读 usage.current_user）。
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import TYPE_CHECKING

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage

# embed 走 module-form：langchain_core 导入后同进程 from-import 子模块偶发被
# lazy-import 机制劫持成空壳（P23 撞坑，详见任务书 §6.4）——module-form 稳定。
import gewu.llm.embed as llm_embed
from gewu.llm.chat import Usage, make_chat_model, parse_finish_reason, parse_usage
from gewu.obs import current_tracer
from gewu.usage import current_user

if TYPE_CHECKING:
    from gewu.config import Settings


def to_lc_messages(messages: list[tuple[str, str]]) -> list[BaseMessage]:
    """(role, content) 二元组 → langchain 消息（Go llm.Message 的等价转换）。"""
    return [
        SystemMessage(content=c) if role == "system" else HumanMessage(content=c)
        for role, c in messages
    ]


def ctx_profile(messages: list[tuple[str, str]] | list[BaseMessage]) -> str:
    """送入模型的上下文概况（线上排障：条数/字符量/角色分布一行可见）。"""
    counts: dict[str, int] = {}
    chars = 0
    for m in messages:
        role, content = (m[0], m[1]) if isinstance(m, tuple) else (type(m).__name__, m.content)
        counts[role] = counts.get(role, 0) + 1
        chars += len(str(content))
    dist = ",".join(f"{r}×{n}" for r, n in counts.items())
    return f"msgs={len(messages)} chars={chars}（{dist}）"


class LLMService:
    """双模型缓存 + 门面方法。线程安全：ChatOpenAI invoke 可并发，模型惰性建一次。"""

    def __init__(self, settings: Settings, budget=None, usage=None) -> None:
        self._s = settings
        self._models: dict[bool, object] = {}
        self._embeddings: llm_embed.GewuEmbeddings | None = None
        self._budget = budget  # TokenBudget（None = 不记账，测试替身用）
        self._usage = usage  # UsageStore（None = 不记 per-user，测试替身用）

    def _record(self, usage) -> None:
        self._record_both(int(getattr(usage, "total", 0) or 0))

    def _record_both(self, total: int) -> None:
        """双写记账：全局闸 + per-user 账（归属读 contextvar；失败不影响主链路）。"""
        if total <= 0:
            return
        if self._budget is not None:
            try:
                self._budget.add(total)
            except Exception:  # noqa: BLE001
                pass
        if self._usage is not None:
            user = current_user.get()
            if user:
                try:
                    self._usage.add(user, total)
                except Exception:  # noqa: BLE001
                    pass

    # ---------- Chat ----------

    def _model(self, small: bool):
        if small not in self._models:
            self._models[small] = make_chat_model(self._s, small=small)
        return self._models[small]

    def agent_model(self, *, small: bool = False, max_tokens: int = 1200):
        """agent-first 主循环的模型实例（create_agent 直接调用；温度/上限固化在实例）。"""
        return make_chat_model(self._s, small=small, temperature=0.0, max_tokens=max_tokens)

    def record_usage(self, total_tokens: int) -> None:
        """主循环 middleware 的记账口（与 chat/chat_stream 同一预算闸，双写）。"""
        self._record_both(int(total_tokens))

    def chat(
        self,
        messages: list[tuple[str, str]],
        *,
        small: bool = False,
        json_mode: bool = False,
        temperature: float = 0.0,
        max_tokens: int = 2048,
    ) -> str:
        """一次补全调用，返回文本内容。messages 为 (role, content) 二元组列表。

        P27-2 兼任 llm span：guard/routing/槽位抽取/followups 等小模型全族
        与 classic 直答经此一处接线全覆盖（agent 主循环另有 UsageRecordMiddleware）。
        """
        kwargs: dict = {"temperature": temperature, "max_tokens": max_tokens}
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        tracer = current_tracer()
        model = self._model(small).bind(**kwargs)
        lc_msgs = to_lc_messages(messages)
        profile = {
            "msgs": len(messages),
            "chars": sum(len(c) for _, c in messages),
            "small": small,
            "json_mode": json_mode,
        }
        if tracer is None:
            resp = model.invoke(lc_msgs)
            self._record(getattr(resp, "usage_metadata", None))
            return _content_text(resp)
        with tracer.span("llm", self._model_name(small), input=profile) as sp:
            resp = model.invoke(lc_msgs)
            usage = getattr(resp, "usage_metadata", None)
            self._record(usage)
            text = _content_text(resp)
            sp.tokens = int(usage.get("total_tokens", 0) or 0) if usage else None
            sp.output = {"chars": len(text)}
            return text

    def _model_name(self, small: bool) -> str:
        return self._s.llm_small_model if small else self._s.llm_model

    def chat_full(
        self,
        messages: list[tuple[str, str]],
        *,
        small: bool = False,
        temperature: float = 0.0,
        max_tokens: int = 2048,
    ) -> AIMessage:
        """同 chat 但返回完整 AIMessage（finish_reason/usage 解析用）。"""
        model = self._model(small).bind(temperature=temperature, max_tokens=max_tokens)
        resp = model.invoke(to_lc_messages(messages))
        self._record(getattr(resp, "usage_metadata", None))
        return resp  # type: ignore[return-value]

    def chat_stream(
        self,
        messages: list[tuple[str, str]],
        *,
        small: bool = False,
        temperature: float = 0.0,
        max_tokens: int = 2048,
    ) -> ChatStreamResult:
        """流式补全（Go ChatStream 等价）：迭代取文本增量，结束读 finish_reason。"""
        print(f"[llm] 直答上下文 {ctx_profile(messages)}")
        model = self._model(small).bind(temperature=temperature, max_tokens=max_tokens)
        return ChatStreamResult(
            model,
            to_lc_messages(messages),
            record=self._record_both,
            name=self._model_name(small),
            profile=ctx_profile(messages),
        )

    def chat_with_tools(
        self,
        messages: list[BaseMessage],
        tools: list[dict],
        *,
        small: bool = False,
        temperature: float = 0.0,
        max_tokens: int = 2048,
    ) -> AIMessage:
        """原生 tool-calling（Go ChatWithTools 等价）：返回含 tool_calls 的 AIMessage。"""
        print(f"[llm] agent 上下文 {ctx_profile(messages)}")
        model = (
            self._model(small)
            .bind(temperature=temperature, max_tokens=max_tokens)
            .bind_tools(tools)
        )
        return model.invoke(messages)  # type: ignore[return-value]

    # ---------- Embed ----------

    def embeddings(self) -> llm_embed.GewuEmbeddings:
        if self._embeddings is None:
            self._embeddings = llm_embed.GewuEmbeddings(
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


class ChatStreamResult:
    """流式补全的可迭代结果：逐块产出文本增量，结束后 finish_reason/usage 就位。

    P27-2：迭代全程包一个 llm span（span 在调用方 Context 内启停——直答节点
    运行于 graph 迭代中，tracer 经 chat.py 的 re-set 可见）。
    """

    def __init__(
        self, model, messages: list[BaseMessage], record=None, *, name: str = "", profile: str = ""
    ) -> None:
        self._chunks: Iterator = model.stream(messages)
        self._record = record  # 记账回调（LLMService._record_both；None=测试替身）
        self._name = name
        self._profile = profile
        self.finish_reason = ""
        self.usage = Usage()
        self.produced_chars = 0

    def __iter__(self) -> Iterator[str]:
        from gewu.obs import current_tracer  # noqa: PLC0415

        tracer = current_tracer()
        if tracer is None:
            yield from self._drain()
            return
        with tracer.span("llm", self._name, input={"profile": self._profile}) as sp:
            yield from self._drain()
            sp.tokens = self.usage.total or None
            sp.output = {"finish": self.finish_reason, "chars": self.produced_chars}

    def _drain(self) -> Iterator[str]:
        for chunk in self._chunks:
            meta = getattr(chunk, "response_metadata", None) or {}
            if meta.get("finish_reason"):
                self.finish_reason = str(meta["finish_reason"])
            um = getattr(chunk, "usage_metadata", None)
            if um:
                self.usage = Usage(
                    prompt=int(um.get("input_tokens", 0)),
                    completion=int(um.get("output_tokens", 0)),
                    total=int(um.get("total_tokens", 0)),
                )
            text = _content_text(chunk)
            if text:
                self.produced_chars += len(text)
                yield text
        if self._record is not None and self.usage.total:
            try:
                self._record(self.usage.total)
            except Exception:  # noqa: BLE001 - 记账失败不影响主链路
                pass


def _content_text(message) -> str:
    """消息 content 归一为纯文本（兼容 string 与分段列表两种形态）。"""
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


__all__ = ["LLMService", "ChatStreamResult", "Usage", "parse_finish_reason", "parse_usage"]
