"""节点内事件发射：LangGraph custom stream writer 的安全包装。

节点调用 emit(evt) 把 PARITY 事件送入 custom 流（graph.stream(stream_mode="custom")
消费）；脱离图运行时静默跳过（单测直接调节点函数不产事件）。
"""

from __future__ import annotations

from typing import Any

from langgraph.config import get_stream_writer


def emit(evt: dict[str, Any]) -> None:
    try:
        writer = get_stream_writer()
    except Exception:  # noqa: BLE001 - 无图运行时（单测直调节点）
        return
    try:
        writer(evt)
    except Exception:  # noqa: BLE001 - 写事件失败不中断节点逻辑
        return
