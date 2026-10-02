"""主图共享状态（对齐 Go Deps.RunChat 的运行时上下文与 outcome 侧记）。"""

from __future__ import annotations

from typing import Annotated, TypedDict

from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages


class ChatState(TypedDict, total=False):
    """一次 chat 的图状态。thread_id=session_id（checkpointer 持久化，跨请求恢复）。

    tx_* 为办理流程（知行执行层）跨轮状态——P8-1 的 sessions.db 语义由
    checkpointer 对 state 的持久化吸收（P14 Q4 拍板：引入原生机制）。
    messages 为 agent-first 主循环（P17）的跨轮对话历史（classic 链路不读写）。
    """

    # 请求上下文
    question: str  # 原始问题（记忆存档/用户可见层用）
    resolved: str  # 补全后问题（贯通路由与检索）
    mode: str
    role: str
    user: str
    session_id: str

    # agent-first 主循环（P17）：对话历史（add_messages 合并，checkpointer 持久化）
    messages: Annotated[list[BaseMessage], add_messages]

    # 路由决策（routing.RouteDecision 的 dict 形态；classic 链路使用）
    route: dict

    # 检索与回答
    hits: list[dict]
    citations: list[dict]
    truncated: bool  # 主答案撞 max_tokens（done.reason=max_tokens 的依据）
    answer: str  # 本轮累积回答文本（记忆固化用）
    # P30 流式防重：agent 主循环最终轮已流式发出的文本（agent_done 等价校验用，
    # 轮起清零——与 citations 同款跨轮污染防御）
    answer_streamed: str

    # 办理流程跨轮状态（Phase: idle | collect | confirm）——classic workflow 链路；
    # agent 链路的办理收集在 messages 对话内完成，不落这些字段。
    tx_tool: str
    tx_phase: str
    tx_slots: dict
    tx_last_asked: str

    # hybrid 链路标记：政策直答完成后转业务办理（answer_direct 条件边读）
    hybrid_then_tx: bool

    # 长期记忆块（入口装配一次，直答/深研/agent 主循环共用）
    mem_block: str


def new_state(question: str, mode: str, session_id: str, role: str, user: str) -> dict:
    """图入口输入（每个请求一次 invoke）。"""
    return {
        "question": question,
        "resolved": question,
        "mode": mode,
        "session_id": session_id,
        "role": role,
        "user": user,
        "truncated": False,
        "answer": "",
        "mem_block": "",
    }
