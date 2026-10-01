"""GuardMiddleware：agent-first 链路的入口安检（P17-1）。

设计对照 chat-langchain guardrails_prompts（lenient 三原则）：
默认放行 / greetings·身份·能力一律放行 / 拿不准放行 / fail-open。
纯问候走正则快路径（零 LLM，直接放行进主循环自然寒暄）；
LLM 判定输出 allow|meta|block：meta（软寒暄）就地直答短路收尾，
block（高置信范围外）吐 REFUSAL_ANSWER 静态话术（eval「只能回答」断言依赖）。
"""

from __future__ import annotations

import re
from typing import Any

from langchain.agents.middleware import AgentMiddleware, hook_config
from langchain_core.messages import AIMessage, HumanMessage

from gewu.agent import events as ev
from gewu.agent.emitter import emit
from gewu.agent.prompts import REFUSAL_ANSWER
from gewu.jsonx import json_str, parse_json_object

# 纯问候/身份问（整句匹配，零 LLM 快路径）——放行进主循环，由模型自然寒暄。
GREETING_RE = re.compile(
    r"^(你好|您好|嗨|哈喽|hi|hello|hey|早上好|上午好|下午好|晚上好|晚安|在吗|"
    r"谢谢|多谢|辛苦了|再见|拜拜|你是谁|你叫什么|你能[帮]?做什么|你能干什么|"
    r"你可以做什么|你都会什么|怎么用|怎么玩)[呀啊哈哦~！!。．，,？?\s]*$"
)

GUARD_SYSTEM = """你是校园问答助手「格物」的输入安检器。判断用户消息与校园场景的关系（政策/教务/生活服务/业务办理/与助手寒暄均算相关）。

判断原则（重要，逐条遵守）：
1. 默认放行（allow）：只有高度确信消息与校园场景完全无关、且不是对上文的追问时才 block；
2. 以下一律放行：问候寒暄（你好/早上好/谢谢/再见）、询问助手身份或能力（你是谁/能做什么/怎么用）、对上一轮问题的追问或补充说明；
3. 拿不准时放行——误拒的代价远大于漏放；
4. 决断规则：把消息放到「钱塘大学」语境里再读一遍（如「钱塘大学的学生该怎么理财」仍是校园相关；「今天A股怎么样」加上任何语境都无关）；
5. 与办理、咨询沾边的模糊请求一律放行，交给主循环处理。

输出 JSON：{"decision":"allow|meta|block","intent":"factual|research|transaction|hybrid|chitchat|refusal","reply":"..."}
- meta：纯寒暄/问候/问能力（无需任何工具就能回应）——reply 必填，以友好校园助手口吻直接回复（可顺带介绍：能查政策、能约场馆、能办请假）；
- block：高置信范围外（如股市行情、代写代码、写邮件）——reply 留空；
- allow：其余全部——reply 留空，intent 尽力给。
只输出 JSON。"""

_GUARD_INTENTS = {"factual", "research", "transaction", "hybrid", "chitchat", "refusal"}


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


def classify_guard(llm, question: str) -> dict[str, Any]:
    """LLM 安检判定；无 key/异常/解析失败一律 fail-open 返回 allow。"""
    fail = {"decision": "allow", "intent": "", "reply": ""}
    if llm is None or not llm.has_key():
        return fail
    try:
        raw = llm.chat(
            [("system", GUARD_SYSTEM), ("user", question)],
            json_mode=True,
            small=True,
            max_tokens=200,
        )
        obj = parse_json_object(raw)
    except Exception as e:  # noqa: BLE001 - fail-open：安检挂了不放行反而拒绝服务
        print(f"[guard] 安检调用失败，放行：{e}")
        return fail
    decision = json_str(obj, "decision")
    if decision not in ("allow", "meta", "block"):
        return fail
    intent = json_str(obj, "intent")
    if intent not in _GUARD_INTENTS:
        intent = ""
    reply = json_str(obj, "reply")[:500]
    if decision == "meta" and not reply.strip():
        decision = "allow"  # meta 必须带话，缺话降级放行交主循环
    return {"decision": decision, "intent": intent, "reply": reply}


def guard_update(llm, question: str, in_conversation: bool = False) -> dict[str, Any] | None:
    """安检判定 → 状态更新（纯逻辑，钩子与单测共用）。

    会话感知（chat-langchain "NOT a follow-up" 条款）：对话已在进行中
    （历史存在 AI 消息，如办理槽位收集的短回复轮）时只放行不分类——
    上下文判断交给主循环；guard 只负责首轮触达的范围安检。
    """
    if not question:
        return None
    if in_conversation:
        return {"guard_action": "allow"}

    # 快路径：纯问候零 LLM 放行（寒暄由主循环一次调用自然生成，不吐静态话术）
    if GREETING_RE.match(question.strip()):
        emit(_provisional("chitchat", "正则快路径：纯问候放行", by_llm=False))
        return {"guard_action": "allow"}

    verdict = classify_guard(llm, question)
    decision = verdict["decision"]
    if decision == "allow":
        if verdict["intent"]:
            emit(_provisional(verdict["intent"], "guard：放行", by_llm=True))
        return {"guard_action": "allow"}
    if decision == "block":
        emit(_provisional("refusal", "guard：高置信范围外", by_llm=True))
        return {
            "guard_action": "block",
            "jump_to": "end",
            "messages": [AIMessage(content=REFUSAL_ANSWER)],
        }
    # meta：就地直答（answer 事件由外壳 agent_done 统一发射，这里只注入消息）
    if verdict["intent"] == "":
        verdict["intent"] = "chitchat"
    emit(_provisional(verdict["intent"], "guard：寒暄就地直答", by_llm=True))
    return {
        "guard_action": "meta",
        "jump_to": "end",
        "messages": [AIMessage(content=verdict["reply"])],
    }


class GuardMiddleware(AgentMiddleware):
    """入口安检中间件：before_agent 钩子，block/meta 时 jump_to=end 短路。"""

    def __init__(self, llm) -> None:
        super().__init__()
        self._llm = llm

    @hook_config(can_jump_to=["end"])
    def before_agent(self, state, runtime) -> dict[str, Any] | None:
        msgs = state.get("messages") or []
        in_conversation = any(isinstance(m, AIMessage) for m in msgs[:-1])
        return guard_update(self._llm, last_human_text(msgs), in_conversation)


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
