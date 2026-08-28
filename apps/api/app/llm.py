"""LLM 访问层：统一封装 OpenAI 兼容端点（智谱 / DeepSeek / OpenAI 等均可直换）。"""

from __future__ import annotations

from collections.abc import Iterator

from openai import OpenAI

from .budget import budget
from .config import get_settings

_client: OpenAI | None = None


def has_key() -> bool:
    return bool(get_settings().llm_api_key)


def client() -> OpenAI:
    global _client
    if _client is None:
        s = get_settings()
        if not s.llm_api_key:
            raise RuntimeError("未配置 LLM_API_KEY，无法调用模型")
        _client = OpenAI(api_key=s.llm_api_key, base_url=s.llm_base_url)
    return _client


def chat(
    messages: list[dict],
    *,
    json_mode: bool = False,
    temperature: float = 0.2,
    max_tokens: int = 2048,
    small: bool = False,
) -> str:
    """同步补全，返回完整文本；实际用量计入每日预算。

    small=True 使用轻量模型（路由/抽取/改写等辅助调用），主答案生成用主模型。
    """
    s = get_settings()
    budget.ensure()
    kwargs = {}
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}
    if s.llm_disable_thinking:
        kwargs["extra_body"] = {"thinking": {"type": "disabled"}}
    resp = client().chat.completions.create(
        model=s.llm_small_model if small else s.llm_model,
        messages=messages,
        temperature=temperature,
        max_tokens=max_tokens,
        **kwargs,
    )
    usage = getattr(resp, "usage", None)
    budget.add(usage.total_tokens if usage else 0)
    return resp.choices[0].message.content or ""


def chat_stream(
    messages: list[dict],
    *,
    temperature: float = 0.2,
    max_tokens: int = 2048,
) -> Iterator[str]:
    """流式补全，逐段产出文本增量。

    不同兼容端点对 stream_options 支持不一，流式用量按「字符数/2」保守估算入账。
    """
    s = get_settings()
    budget.ensure()
    kwargs = {}
    if s.llm_disable_thinking:
        kwargs["extra_body"] = {"thinking": {"type": "disabled"}}
    stream = client().chat.completions.create(
        model=s.llm_model,
        messages=messages,
        temperature=temperature,
        max_tokens=max_tokens,
        stream=True,
        **kwargs,
    )
    chars = 0
    for chunk in stream:
        if chunk.choices and chunk.choices[0].delta and chunk.choices[0].delta.content:
            text = chunk.choices[0].delta.content
            chars += len(text)
            yield text
    budget.add(max(1, chars // 2))


def embed(texts: list[str]) -> list[list[float]]:
    """文本向量化，调用方自行做归一化。"""
    s = get_settings()
    budget.ensure()
    resp = client().embeddings.create(model=s.embed_model, input=texts)
    usage = getattr(resp, "usage", None)
    budget.add(usage.total_tokens if usage else sum(len(t) for t in texts) // 2)
    data = sorted(resp.data, key=lambda d: d.index)
    return [d.embedding for d in data]
