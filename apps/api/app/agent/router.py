"""问题路由：优先 LLM 分类，无 key 或调用失败时降级为启发式规则。"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass

from .. import llm
from .prompts import ROUTER_SYSTEM

logger = logging.getLogger(__name__)

# 复合问题的信号词：出现并列/递进/多条件时倾向于研究链路
_RESEARCH_HINTS = (
    "并且",
    "同时",
    "以及",
    "分别",
    "然后",
    "还要",
    "再加上",
    "又想",
    "还能",
    "会不会",
    "能不能",
    "影响",
)

# 办理诉求 vs 知识咨询的区分：办理动词 + 第一人称请求 → 办理；只是问政策 → 知识
_TX_VERBS_RE = re.compile(r"预约|预订|退订|取消预约|请假|事假|病假|销假|假申请|我的预约|待审批|批准")
_REQ_RE = re.compile(r"帮我|给我|我想|我要|麻烦|想请|想约|想订|帮我查|帮我看")
_CONSULT_RE = re.compile(r"什么|怎么|为什么|是不是|需不需要|能不能|多少|谁|规定|要求|政策|意思")


@dataclass
class RouteResult:
    route: str  # factual | research | refusal | transaction | hybrid
    reason: str
    by_llm: bool


def heuristic_route(question: str) -> RouteResult:
    """免 LLM 的降级路由。"""
    q = question.strip()
    if _TX_VERBS_RE.search(q):
        wants_action = bool(_REQ_RE.search(q))
        consulting = bool(_CONSULT_RE.search(q))
        if wants_action and consulting:
            return RouteResult("hybrid", "启发式：办理诉求 + 政策咨询", False)
        if wants_action or not consulting:
            return RouteResult("transaction", "启发式：业务办理诉求", False)
        # 只咨询政策：落入知识问答
    if len(q) > 32 or any(h in q for h in _RESEARCH_HINTS):
        return RouteResult("research", "启发式：长问题或含并列/多条件信号", False)
    return RouteResult("factual", "启发式：短事实型问题", False)


def route_question(question: str) -> RouteResult:
    if not llm.has_key():
        return heuristic_route(question)
    try:
        raw = llm.chat(
            [
                {"role": "system", "content": ROUTER_SYSTEM},
                {"role": "user", "content": question},
            ],
            json_mode=True,
            temperature=0.0,
            max_tokens=200,
        )
        data = json.loads(raw)
        route = data.get("route")
        if route in ("factual", "research", "refusal", "transaction", "hybrid"):
            return RouteResult(route, str(data.get("reason", ""))[:100], True)
    except Exception as exc:  # noqa: BLE001
        logger.warning("路由器 LLM 调用失败，降级启发式路由：%s", exc)
    return heuristic_route(question)
