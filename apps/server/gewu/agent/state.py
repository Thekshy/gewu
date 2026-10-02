"""主图共享状态（P31-2 classic 退役后收敛：请求上下文 + agent 主循环跨轮字段）。

classic 链路字段（resolved/route/hits/tx_*/hybrid_then_tx/mem_block 装配）
随节点删除一并移除；tx 语义由 agent 侧 messages 对话 + HITL 中间件承载。
"""

from __future__ import annotations

from typing import Annotated, TypedDict

from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages


class ChatState(TypedDict, total=False):
    """一次 chat 的图状态。thread_id=session_id（checkpointer 持久化，跨请求恢复）。

    messages 为 agent-first 主循环（P17）的跨轮对话历史（add_messages 合并）。
    """

    # 请求上下文
    question: str  # 本轮问题（记忆存档/用户可见层用）
    mode: str
    role: str
    user: str
    session_id: str

    # agent-first 主循环（P17）：对话历史（add_messages 合并，checkpointer 持久化）
    messages: Annotated[list[BaseMessage], add_messages]

    # 引用与回答
    citations: list[dict]
    truncated: bool  # 主答案撞 max_tokens（done.reason=max_tokens 的依据）
    answer: str  # 本轮累积回答文本（记忆固化用）
    # P30 流式防重：agent 主循环最终轮已流式发出的文本（agent_done 等价校验用，
    # 轮起清零——与 citations 同款跨轮污染防御）
    answer_streamed: str


def new_state(question: str, mode: str, session_id: str, role: str, user: str) -> dict:
    """图入口输入（每个请求一次 invoke）。"""
    return {
        "question": question,
        "mode": mode,
        "session_id": session_id,
        "role": role,
        "user": user,
        "truncated": False,
        "answer": "",
    }
