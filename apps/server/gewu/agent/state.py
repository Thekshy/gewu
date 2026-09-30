"""主图共享状态（对齐 Go Deps.RunChat 的运行时上下文与 outcome 侧记）。"""

from __future__ import annotations

from typing import TypedDict


class ChatState(TypedDict, total=False):
    """一次 chat 的图状态。thread_id=session_id（checkpointer 持久化，跨请求恢复）。

    tx_* 为办理流程（知行执行层）跨轮状态——P8-1 的 sessions.db 语义由
    checkpointer 对 state 的持久化吸收（P14 Q4 拍板：引入原生机制）。
    """

    # 请求上下文
    question: str  # 原始问题（记忆存档/用户可见层用）
    resolved: str  # 补全后问题（贯通路由与检索）
    mode: str
    role: str
    user: str
    session_id: str

    # 路由决策（routing.RouteDecision 的 dict 形态）
    route: dict

    # 检索与回答
    hits: list[dict]
    citations: list[dict]
    truncated: bool  # 主答案撞 max_tokens（done.reason=max_tokens 的依据）
    answer: str  # 本轮累积回答文本（记忆固化用）

    # 办理流程跨轮状态（Phase: idle | collect | confirm）
    tx_tool: str
    tx_phase: str
    tx_slots: dict
    tx_last_asked: str

    # hybrid 链路标记：政策直答完成后转业务办理（answer_direct 条件边读）
    hybrid_then_tx: bool

    # 长期记忆块（入口装配一次，直答/深研/ReAct 共用）
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
