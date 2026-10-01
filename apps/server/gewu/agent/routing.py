"""cascade 三级级联路由（移植自 Go internal/agent/routing：cascade.go + classic.go 启发式）。

L0 规则快路径（高置信精确 case，命中省一次 LLM）→ L1 小模型五类概率分布
（双阈值 + top1-top2 margin）→ L2 主模型灰度兜底。输出路由决策包（dict），
下游编排据此决定链路/工具集/模型档。classic/triage 两策略随 Go 退役不移植（P14 Q6）。
"""

from __future__ import annotations

import re

from gewu.agent.routing_prompts import ROUTER_SYSTEM, ROUTER_SYSTEM_CASCADE
from gewu.jsonx import EmptyJSONError, json_str, parse_json_object

CONF_HIGH = 0.80  # ≥ 且 margin 足够 → 直接路由
CONF_LOW = 0.55  # < → 不相信 L1，走 L2/兜底
MARGIN_MIN = 0.15  # top1-top2 概率间隔，小于则视为"类别纠缠"
L2_CONFIDENCE = 0.9
H_CONF = 0.5
REASON_LIMIT = 100  # reason 截断 100 rune

# routeOrder 固定类别序：解析与排序的确定性基础（平局按此序取先）。
ROUTE_ORDER = ["factual", "research", "transaction", "hybrid", "refusal"]
VALID_ROUTES = set(ROUTE_ORDER)

TRANSACTION_TOOLSET = [
    "query_venues",
    "my_bookings",
    "leave_status",
    "pending_leaves",
    "book_venue",
    "cancel_booking",
    "submit_leave",
    "approve_leave",
]

# exactTxRe L0 高置信精确规则：办理强动词开头 + 明确办理动作。
_EXACT_TX_RE = re.compile(
    r"^(帮我|我要|我想|给我|麻烦).*(预约|预订|请假|销假|退订|取消预约|提交请假)"
)
# campusDomainRe 校园领域实体词：命中时 L1 的 refusal 判定不可信，升级 L2 复核。
_CAMPUS_DOMAIN_RE = re.compile(
    r"转专业|绩点|保研|研究生|推免|奖学金|助学金|图书馆|借阅|宿舍|门禁|校历|学分|选课|考试|挂科|补考|辅修|双学位|交流|交换|论文|答辩|学位|体测|体育|医保|校医|心理咨询|一卡通|校园卡|请假|销假|场馆|体育馆|羽毛球|篮球|游泳|研讨间"
)
# reactPlanSignalRe "目标明确但路径不定"的办理信号（REACT_MODE=on 时转 ReAct）。
_REACT_PLAN_SIGNAL_RE = re.compile(r"安排|规划|推荐一下|帮我定|帮我挑|顺便|把.*都|一并")
# 启发式正则（classic.go，顺序判定不可调换）。
_TX_VERBS_RE = re.compile(
    r"预约|预订|退订|取消预约|请假|事假|病假|销假|假申请|我的预约|待审批|批准"
)
_REQ_RE = re.compile(r"帮我|给我|我想|我要|麻烦|想请|想约|想订|帮我查|帮我看")
_CONSULT_RE = re.compile(r"什么|怎么|为什么|是不是|需不需要|能不能|多少|谁|规定|要求|政策|意思")
_RESEARCH_HINTS = [
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
]


def react_plan_signal(question: str) -> bool:
    return bool(_REACT_PLAN_SIGNAL_RE.search(question))


def heuristic_route(question: str) -> dict:
    """免 LLM 的降级路由（PARITY §5.2，顺序判定不可调换）。"""
    q = question
    if _TX_VERBS_RE.search(q):
        wants_action = bool(_REQ_RE.search(q))
        consulting = bool(_CONSULT_RE.search(q))
        if wants_action and consulting:
            return {"route": "hybrid", "reason": "启发式：办理诉求 + 政策咨询"}
        if wants_action or not consulting:
            return {"route": "transaction", "reason": "启发式：业务办理诉求"}
        # 只咨询政策：落入知识问答
    if len(q) > 32 or any(h in q for h in _RESEARCH_HINTS):
        return {"route": "research", "reason": "启发式：长问题或含并列/多条件信号"}
    return {"route": "factual", "reason": "启发式：短事实型问题"}


def _dec(
    route: str,
    confidence: float,
    layer: str,
    reason: str,
    by_llm: bool,
    pre_rag: bool = False,
    toolset: list[str] | None = None,
    model_tier: str = "small",
) -> dict:
    return {
        "route": route,
        "confidence": confidence,
        "layer": layer,
        "reason": reason,
        "by_llm": by_llm,
        "pre_rag": pre_rag,
        "toolset": list(toolset or []),
        "model_tier": model_tier,
    }


def fill_policy(dec: dict) -> dict:
    """路由类别 → 处理策略（决策包的"策略"部分，集中维护）。"""
    r = dec["route"]
    if r == "factual":
        dec["pre_rag"], dec["model_tier"] = True, "standard"
    elif r == "research":
        dec["pre_rag"], dec["model_tier"] = True, "flagship"
    elif r in ("transaction", "hybrid"):
        dec["toolset"], dec["model_tier"] = list(TRANSACTION_TOOLSET), "small"
    elif r == "refusal":
        dec["model_tier"] = "small"
    return dec


def _clamp_reason(reason: str) -> str:
    return reason[:REASON_LIMIT]


def parse_route_scores(raw: str) -> tuple[dict, float, float]:
    """解析 {"scores":{五类概率},"reason":..}；按固定类别序稳定排序取 top1/top2。"""
    try:
        obj = parse_json_object(raw)
    except (ValueError, EmptyJSONError):
        h = heuristic_route(raw)
        return _dec(h["route"], H_CONF, "heuristic-fallback", h["reason"], False), H_CONF, 0.0
    scores = obj.get("scores")
    pairs: list[tuple[str, float]] = []
    if isinstance(scores, dict):
        for r in ROUTE_ORDER:
            v = scores.get(r)
            # 字符串数字不参与概率判定（模型未按格式输出时宁可走兜底）
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                pairs.append((r, float(v)))
    if not pairs:
        h = heuristic_route("")
        return _dec(h["route"], H_CONF, "heuristic-fallback", h["reason"], False), H_CONF, 0.0
    pairs.sort(key=lambda p: -p[1])  # 稳定排序：平局保持 routeOrder 先后
    top1 = pairs[0][1]
    top2 = pairs[1][1] if len(pairs) > 1 else 0.0
    reason = _clamp_reason(json_str(obj, "reason"))
    return _dec(pairs[0][0], top1, "", reason, False), top1, top2


class CascadeRouter:
    """三级级联路由器（llm 为 LLMService；无 key 退启发式）。"""

    def __init__(self, llm) -> None:
        self._llm = llm

    def route(self, question: str) -> dict:
        dec = self._decide(question)
        print(
            f"[routing] q={question[:40]!r} → {dec['route']}"
            f"（{dec.get('layer')}：{dec.get('reason')}）"
        )
        return dec

    def _decide(self, question: str) -> dict:
        llm = self._llm
        # L0：规则快路径（毫秒、零成本、高置信）
        if _EXACT_TX_RE.search(question):
            if _CONSULT_RE.search(question):
                return fill_policy(
                    _dec("hybrid", 1.0, "L0-rule", "规则快路径：办理诉求 + 政策咨询", False)
                )
            return _dec(
                "transaction",
                1.0,
                "L0-rule",
                "规则快路径：明确办理指令",
                False,
                toolset=TRANSACTION_TOOLSET,
            )

        # 无 key（正常启动已拦截，这里只为测试与健壮性）退启发式
        if llm is None or not llm.has_key():
            h = heuristic_route(question)
            return _dec(h["route"], H_CONF, "heuristic-fallback", h["reason"], False)

        # L1：小 LLM 输出概率分布
        dec, top1, top2 = self._l1_classify(question)
        margin = top1 - top2
        # 安全网：refusal 是代价最高的误路由。问题带办理/请求强动词或校园领域
        # 实体词时，refusal 判定与之直接矛盾——不直接采信，升级 L2 复核。
        if dec["route"] == "refusal" and (
            _REQ_RE.search(question)
            or _TX_VERBS_RE.search(question)
            or _CAMPUS_DOMAIN_RE.search(question)
        ):
            top1, margin = 0.0, 0.0  # 降入灰度区，走下方 L2 逻辑
        if top1 >= CONF_HIGH and margin >= MARGIN_MIN:
            dec["layer"], dec["by_llm"] = "L1-llm", True
            return fill_policy(dec)
        if top1 < CONF_LOW or margin < MARGIN_MIN:
            # L2：主模型二次判定（灰度区，只吃少量流量）
            l2 = self._l2_arbitrate(question)
            if l2 is not None:
                l2["layer"], l2["by_llm"] = "L2-main", True
                return fill_policy(l2)
            # 仍不确定：转通用链路并提高模型档（不自由发挥、不反问阻断）
            return _dec(
                "factual",
                top1,
                "L2-uncertain",
                "灰度区未决，转通用链路并提高模型档",
                True,
                pre_rag=True,
                model_tier="flagship",
            )
        # 中间带：置信足够且 margin 足够，直接采信 L1
        dec["layer"], dec["by_llm"] = "L1-llm", True
        return fill_policy(dec)

    def _l1_classify(self, q: str) -> tuple[dict, float, float]:
        try:
            raw = self._llm.chat(
                [("system", ROUTER_SYSTEM_CASCADE), ("user", q)],
                json_mode=True,
                small=True,
                max_tokens=200,
            )
        except Exception as e:  # noqa: BLE001 - 降级启发式
            print(f"[routing] L1 分类调用失败，降级启发式：{e}")
            h = heuristic_route(q)
            return _dec(h["route"], H_CONF, "heuristic-fallback", h["reason"], False), H_CONF, 0.0
        return parse_route_scores(raw)

    def _l2_arbitrate(self, q: str) -> dict | None:
        """主模型 few-shot 二次判定；解析失败返回 None 交由上层兜底。"""
        try:
            raw = self._llm.chat(
                [("system", ROUTER_SYSTEM), ("user", q)],
                json_mode=True,
                max_tokens=200,
            )
        except Exception as e:  # noqa: BLE001
            print(f"[routing] L2 二次判定调用失败：{e}")
            return None
        try:
            obj = parse_json_object(raw)
        except (ValueError, EmptyJSONError):
            return None
        route = json_str(obj, "route")
        if route not in VALID_ROUTES:
            return None
        reason = _clamp_reason(json_str(obj, "reason"))
        return _dec(route, L2_CONFIDENCE, "", reason, True)
