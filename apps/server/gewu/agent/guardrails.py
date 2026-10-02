"""GuardMiddleware：agent-first 链路的入口安检（P17-1；P28 收窄；P31-1 退码闸）。

设计对照 chat-langchain guardrails（lenient 三原则）：默认放行 /
greetings·身份·能力一律放行 / 拿不准放行。P31-1 拍板（§0 Q3/Q4）：guard 全链
退关键词码闸——P28 收窄后 LLM 分类的净收益 ≈ 拦变体危险话术，代价 = 首问
串行一跳 + flash 判定非确定（两次线上实证的掷骰子问题）。三分支：

  in_conversation → 直通 allow（会话感知：上下文判断交主循环）；
  GREETING_RE 命中 → allow + provisional route=chitchat（零成本徽章早亮）；
  DANGER_RE 命中 → block（GUARD_BLOCK_ANSWER + route=refusal + jump_to=end）。

其余一律放行：软寒暄/能力问交主循环自然回答（一次主模型调用，质量优于
flash 生成的 reply），meta 出口随 LLM 判定删除。拦截纵深 = 关键词硬红线 +
prompt 墙（AGENT_SYSTEM 第 9 条）+ HITL 代码闸。
"""

from __future__ import annotations

import re
from typing import Any

from langchain.agents.middleware import AgentMiddleware, hook_config
from langchain_core.messages import AIMessage, HumanMessage

from gewu.agent import events as ev
from gewu.agent.emitter import emit
from gewu.agent.prompts import GUARD_BLOCK_ANSWER

# 纯问候/身份问（整句匹配，零成本快路径）——放行进主循环，由模型自然寒暄。
GREETING_RE = re.compile(
    r"^(你好|您好|嗨|哈喽|hi|hello|hey|早上好|上午好|下午好|晚上好|晚安|在吗|"
    r"谢谢|多谢|辛苦了|再见|拜拜|你是谁|你叫什么|你能[帮]?做什么|你能干什么|"
    r"你可以做什么|你都会什么|怎么用|怎么玩)[呀啊哈哦~！!。．，,？?\s]*$"
)

# 危险/违规硬红线词表（§0 Q4 首版：制毒/黑客/诈骗/代写/作弊核心词，求正确
# 与零成本不求召回——漏放的爆炸半径被 prompt 墙 + HITL 兜底；词表丰富化挂账
# 独立小票）。黑客/诈骗用实施性复合词（受害者求助「我被骗了」与防范咨询
# 「怎么防诈骗」不误拦）；代写/作弊挂实施性后缀（处分/认定等政策咨询不误拦）。
DANGER_RE = re.compile(
    r"制毒|冰毒|摇头丸|(制作|制造|提炼)毒品|毒品(制作|制造|配方|提炼)"
    r"|黑客(攻击|入侵|教程)|入侵(系统|服务器|网站|电脑|他人|别人|学校)"
    r"|木马(程序|病毒)|DDoS|ddos"
    r"|诈骗(话术|剧本|教程|技巧|套路)|洗钱"
    r"|代写.{0,6}(论文|作业|毕业|报告|查重)|论文代写|代考|替考"
    r"|作弊.{0,8}(方法|技巧|手段|器|教程|不被发现|蒙混|混过|逃过|骗过)"
)


def last_human_text(messages: list) -> str:
    """取最后一条用户消息文本（guard 检查对象）。"""
    for m in reversed(messages or []):
        if isinstance(m, HumanMessage):
            c = m.content
            return (
                c
                if isinstance(c, str)
                else "".join(
                    seg.get("text", "") if isinstance(seg, dict) else str(seg) for seg in c
                )
            )
    return ""


def guard_update(question: str, in_conversation: bool = False) -> dict[str, Any] | None:
    """安检判定 → 状态更新（纯逻辑，钩子与单测共用）。

    会话感知（chat-langchain "NOT a follow-up" 条款）：对话已在进行中
    （历史存在 AI 消息，如办理槽位收集的短回复轮）时只放行不安检——
    上下文判断交给主循环；guard 只负责首轮触达的范围安检。
    """
    if not question:
        return None
    if in_conversation:
        return {"guard_action": "allow"}

    # 快路径：纯问候放行（寒暄由主循环一次调用自然生成，不吐静态话术）
    if GREETING_RE.match(question.strip()):
        emit(_provisional("chitchat", "正则快路径：纯问候放行", by_llm=False))
        return {"guard_action": "allow"}

    # 硬红线：危险/违规关键词命中即拦（话术沿用 P28，不新造）
    if DANGER_RE.search(question):
        emit(_provisional("refusal", "guard：危险/违规内容", by_llm=False))
        return {
            "guard_action": "block",
            "jump_to": "end",
            "messages": [AIMessage(content=GUARD_BLOCK_ANSWER)],
        }
    return {"guard_action": "allow"}


class GuardMiddleware(AgentMiddleware):
    """入口安检中间件：before_agent 钩子，block 时 jump_to=end 短路。"""

    @hook_config(can_jump_to=["end"])
    def before_agent(self, state, runtime) -> dict[str, Any] | None:
        msgs = state.get("messages") or []
        in_conversation = any(isinstance(m, AIMessage) for m in msgs[:-1])
        return guard_update(last_human_text(msgs), in_conversation)


def _provisional(route: str, reason: str, *, by_llm: bool) -> dict:
    """guard 的 provisional route 事件（两段式第一段，前端徽章早亮）。"""
    return ev.route_decision_evt(
        {
            "route": route,
            "confidence": 0.7,
            "layer": "guard",
            "reason": reason,
            "by_llm": by_llm,
        }
    )
