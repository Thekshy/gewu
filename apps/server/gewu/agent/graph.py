"""会话编排图（P17 agent-first 薄外壳；P31-2 classic 全量退役后收敛为单链）。

图结构（P31-3 将进一步塌缩为端点直调 create_agent 编译产物）：
  START → agent_in → agent（create_agent 子图）→ agent_done → END

done 事件在 chat 端点单点发射（Go RunChat 单点语义的等价物）。
"""

from __future__ import annotations

from langchain_core.messages import HumanMessage
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

from gewu.agent import events as ev
from gewu.agent.emitter import emit
from gewu.agent.mw import ai_content_text, last_ai_message, partial_answer
from gewu.agent.state import ChatState
from gewu.config import Settings
from gewu.llm.service import LLMService
from gewu.rag.retrieve import Retriever


def make_agent_in_node():
    """agent 子图入口：本轮问题入对话历史 + citations/流式标志清零。

    citations 通道按轮计作用域：外层字段跨轮持久化（checkpointer），
    不清零则上一轮来源漏进本轮事件（P26 真跑发现的跨轮污染）。
    answer_streamed 同款轮起清零（P30 防重标志不能跨轮残留）。
    """

    def agent_in(state: ChatState) -> dict:
        emit(ev.status_evt("正在理解问题…"))  # P30：guard/首 token 前的状态行
        return {
            "messages": [HumanMessage(content=state["question"])],
            "citations": [],
            "answer_streamed": "",
        }

    return agent_in


def make_agent_done_node():
    """agent 子图出口：answer 统一发射 + 截断标记 + 轮次耗尽兜底。

    确认门中断轮不到这里（turn 悬停在子图内，摘要已由 PendingAction 发出）；
    guard 短路轮也经过这里（消息由 guard 注入，answer 单点发射不重复）。
    P30 流式防重：最终轮文本已由 StreamingAnswerMiddleware 逐 delta 发出
    （state.answer_streamed 记录），等价时跳过全文重发；轮次耗尽兜底/错误轮
    与流式文本不等价 → 照发，天然兜住漏发。
    """

    def agent_done(state: ChatState) -> dict:
        msgs = state.get("messages") or []
        ai = last_ai_message(msgs)
        answer = ""
        if ai is not None:
            answer = ai_content_text(ai)
            if "Model call limits exceeded" in answer:
                answer = ""  # 轮次耗尽：换部分结论兜底
        if not answer.strip():
            answer = partial_answer(msgs) or "未能获取足够信息回答该问题，请换个说法或补充细节。"
        truncated = False
        if ai is not None:
            meta = getattr(ai, "response_metadata", None) or {}
            if str(meta.get("finish_reason", "") or "") == "length":
                truncated = True
                emit(ev.status_evt("回答已达长度上限，可能被截断"))
        citations = state.get("citations") or []
        streamed = state.get("answer_streamed") or ""
        if streamed.strip() != answer.strip():
            emit(ev.answer_evt(answer))
        emit(ev.citations_evt(citations))
        return {"answer": answer, "citations": citations, "truncated": truncated}

    return agent_done


# ---------- 装配 ----------


def build_graph(
    settings: Settings,
    retriever: Retriever,
    llm: LLMService,
    business=None,
    tools: dict | None = None,
    checkpointer=None,
    agent=None,
):
    """装配会话编排图（P31-2 单链外壳）。checkpointer 缺省内存版。

    tools 为业务工具表（权限矩阵）。agent 为 create_agent 子图（P17 主循环），
    缺省现场装配；测试可注入 fake 模型驱动的实例。
    """
    from gewu.agent.agent import build_agent
    from gewu.agent.tools import tools_for

    tools = tools if tools is not None else tools_for()
    if agent is None:
        agent = build_agent(settings, llm, retriever, business, tools)
    g: StateGraph = StateGraph(ChatState)
    g.add_node("agent_in", make_agent_in_node())
    g.add_node("agent", agent)
    g.add_node("agent_done", make_agent_done_node())
    g.add_edge(START, "agent_in")
    g.add_edge("agent_in", "agent")
    g.add_edge("agent", "agent_done")
    g.add_edge("agent_done", END)
    return g.compile(checkpointer=checkpointer or MemorySaver())
