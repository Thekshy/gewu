"""Chat 模型工厂（langchain-openai）与响应解析。

对齐 Go internal/llm：双模型（主/小）、智谱 thinking 私有参数按开关注入、
finish_reason 与 usage 三元组解析（P10 契约的 Python 侧等价物）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from langchain_core.messages import BaseMessage
from langchain_openai import ChatOpenAI

if TYPE_CHECKING:
    from gewu.config import Settings

NOT_GIVEN = type("NotGiven", (), {"__repr__": lambda self: "NOT_GIVEN"})()


def make_chat_model(settings: Settings, *, small: bool = False) -> ChatOpenAI:
    """构造 Chat 模型：small=True 走辅助小模型（路由/抽取/改写/精排）。

    thinking 是智谱私有参数（OpenAI 等端点不识别会报错），与 Go 版一致按
    LLM_DISABLE_THINKING 开关注入 extra_body，缺省不发。
    """
    extra: dict[str, Any] = {}
    if settings.llm_disable_thinking:
        extra["extra_body"] = {"thinking": {"type": "disabled"}}
    return ChatOpenAI(
        model=settings.llm_small_model if small else settings.llm_model,
        api_key=settings.llm_api_key,
        base_url=settings.llm_base_url or None,
        timeout=60,
        max_retries=2,
        **extra,
    )


@dataclass(frozen=True)
class Usage:
    """usage 三元组（PARITY §3：prompt/completion/total）。"""

    prompt: int = 0
    completion: int = 0
    total: int = 0


def parse_finish_reason(message: BaseMessage) -> str:
    """从 AIMessage.response_metadata 提取 finish_reason（缺失返回空串）。"""
    meta = getattr(message, "response_metadata", None) or {}
    return str(meta.get("finish_reason", "") or "")


def parse_usage(message: BaseMessage) -> Usage:
    """从 AIMessage.usage_metadata 提取用量（缺失全零）。"""
    um = getattr(message, "usage_metadata", None) or {}
    return Usage(
        prompt=int(um.get("input_tokens", 0)),
        completion=int(um.get("output_tokens", 0)),
        total=int(um.get("total_tokens", 0)),
    )
