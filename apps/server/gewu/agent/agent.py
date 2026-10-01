"""agent-first 主循环装配（P17-2）：LangChain 1.x create_agent + middleware 底座。

底座拍板（任务书 Q6-B）：auto 主路换官方 harness（生产级循环 + 中间件家族），
classic 分支保持手写图不动（对照组双底座叙事）。中间件栈（wrap_* 外层=列表
在前者；after_model 链执行序=列表倒序）：

    GuardMiddleware            before_agent：lenient 安检，block/meta jump_to=end
    ModelCallLimitMiddleware   wrap_model_call：轮次上限（REACT_MAX_TURNS 等价）
    TruncationDefenseMiddleware wrap_model_call：P10 截断防御（Pi 式回填重调）
    UsageRecordMiddleware      wrap_model_call：token 记账（预算闸口径统一）
    AgentPromptMiddleware      wrap_model_call：system prompt + 记忆块
    HumanInTheLoopMiddleware   after_model：写工具四件 interrupt 确认门
    PendingActionMiddleware    after_model：确认摘要先于中断发射
    WriteSlotGateMiddleware    wrap_tool_call：缺必填参数 → slot_question 收集
    SearchQueryGuardMiddleware wrap_tool_call：检索词零重合拼回原话（P24-3）
    RouteEventMiddleware       after_agent：effective route 合成补发
    SummarizationMiddleware    上下文压缩（P13 顺延线收口）
"""

from __future__ import annotations

from langchain.agents import create_agent
from langchain.agents.middleware import (
    HumanInTheLoopMiddleware,
    ModelCallLimitMiddleware,
    SummarizationMiddleware,
)
from langchain.agents.middleware.human_in_the_loop import InterruptOnConfig

from gewu.agent.agenttools import build_agent_tools
from gewu.agent.guardrails import GuardMiddleware
from gewu.agent.mw import (
    WRITE_TOOLS,
    AgentPromptMiddleware,
    GewuAgentState,
    PendingActionMiddleware,
    ResearchLimitMiddleware,
    RouteEventMiddleware,
    SearchQueryGuardMiddleware,
    TruncationDefenseMiddleware,
    UsageRecordMiddleware,
    WriteSlotGateMiddleware,
    write_call_ready,
)

AGENT_MAX_TURNS = 8  # 轮次上限（react.py REACT_MAX_TURNS 同值，引擎退役语义平移）
AGENT_MAX_TOKENS = 1200  # 单次模型调用上限（react.py 同值）
SUMMARY_TRIGGER_TOKENS = 30_000  # 上下文压缩触发阈值（P13 收口）
SUMMARY_KEEP_MESSAGES = 20  # 压缩后保留的最近消息条数


def build_agent(settings, llm, retriever, business, tools: dict):
    """装配主循环子图（compiled StateGraph，由外壳图 add_node 嵌套挂载）。

    model 由 llm.agent_model() 提供（测试注入 fake chat model 的接缝）；
    checkpointer 不在此挂——外壳图统一挂 PostgresSaver，interrupt 从子图
    冒泡到顶层暂停（P17-3 真跑验证点）。
    """
    model = llm.agent_model(max_tokens=AGENT_MAX_TOKENS)
    small_model = llm.agent_model(small=True, max_tokens=800)  # 压缩摘要用小档
    tool_list = build_agent_tools(llm, business, tools, retriever)

    write_cfg = {
        name: InterruptOnConfig(
            allowed_decisions=["approve", "reject", "respond"],
            when=lambda req: write_call_ready(business, req.tool_call),
        )
        for name in sorted(WRITE_TOOLS)
    }

    return create_agent(
        model,
        tool_list,
        middleware=[
            GuardMiddleware(llm),
            ModelCallLimitMiddleware(run_limit=AGENT_MAX_TURNS),
            TruncationDefenseMiddleware(),
            UsageRecordMiddleware(llm),
            AgentPromptMiddleware(),
            HumanInTheLoopMiddleware(interrupt_on=write_cfg),
            PendingActionMiddleware(business),
            WriteSlotGateMiddleware(business),
            ResearchLimitMiddleware(),
            SearchQueryGuardMiddleware(),
            RouteEventMiddleware(),
            SummarizationMiddleware(
                model=small_model,
                trigger=("tokens", SUMMARY_TRIGGER_TOKENS),
                keep=("messages", SUMMARY_KEEP_MESSAGES),
            ),
        ],
        state_schema=GewuAgentState,
    )
