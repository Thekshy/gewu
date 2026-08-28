"""知行执行层：工具识别 → 槽位收集 → 确认 → 执行 → 冲突/失败恢复。

核心原则：
- 读操作直接执行；写操作必须经过「确认摘要 → 用户确认 → 执行 → 回执」；
- 日期换算一律走确定性解析（dates.py），LLM 只负责"找出"表述；
- 执行失败不是终点：冲突给可选项、字段非法重新追问，恢复也是流程的一部分。
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import re
from collections.abc import Iterator

from .. import llm
from ..business import service as _service
from ..dates import parse as parse_date
from ..dates import parse_all, today_iso
from ..llm import has_key
from . import tools
from .prompts import SLOT_EXTRACT_SYSTEM
from .session import TxSession, sessions

logger = logging.getLogger(__name__)

biz = tools.business  # mock 业务系统实例（经工具层同一入口的数据视图）

# ---------- 工具识别（离线启发式；有 key 时 LLM 判定，识别不到再落回这里） ----------

_TOOL_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("cancel_booking", re.compile(r"取消预约|退订")),
    ("approve_leave", re.compile(r"批准|通过.*(请假|申请)")),
    ("pending_leaves", re.compile(r"待审批|审批.*(请假|申请)|谁.*请了假")),
    ("leave_status", re.compile(r"请假.*(单号|进度|状态|批了没)|LV-\d+")),
    ("my_bookings", re.compile(r"我的预约|我预约了|我订了")),
    ("query_venues", re.compile(r"(有|哪些|什么|能).*(场馆|场地|研讨间)|场馆.*(有|能|可)")),
    ("submit_leave", re.compile(r"请假|事假|病假|销假|休.*假|请.*天.*假")),
    ("book_venue", re.compile(r"预约|预订|订.*(馆|场|间)")),
]

READ_TOOLS = {"query_venues", "my_bookings", "leave_status", "pending_leaves"}

_CN_NUM = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
_DAYS_PHRASE_RE = re.compile(r"([一二三四五六七八九]|\d+)\s*天")


def _phrase_days(text: str) -> int | None:
    m = _DAYS_PHRASE_RE.search(text)
    if not m:
        return None
    v = m.group(1)
    return _CN_NUM.get(v) if not v.isdigit() else int(v)


def detect_tool(question: str) -> str | None:
    for name, pat in _TOOL_PATTERNS:
        if pat.search(question):
            return name
    return None


# ---------- 槽位定义 ----------

PERIOD_MAP: dict[str, list[str]] = {
    "上午": ["08:00-10:00", "10:00-12:00"],
    "中午": ["14:00-16:00"],
    "下午": ["14:00-16:00", "16:00-18:00"],
    "晚上": ["19:00-21:00"],
    "傍晚": ["16:00-18:00", "19:00-21:00"],
}

_HOUR_SLOT = {8: "08:00-10:00", 10: "10:00-12:00", 14: "14:00-16:00", 16: "16:00-18:00", 19: "19:00-21:00"}
_HOUR_TEXT_RE = re.compile(r"(\d{1,2})\s*[点:：时]\s*(\d{2})?")


def parse_slot(text: str) -> str | None:
    for slot in _service.SLOTS:
        if slot in text or slot[:5] in text:
            return slot
    m = _HOUR_TEXT_RE.search(text)
    if m:
        return _HOUR_SLOT.get(int(m.group(1)) % 12 or 12) or _HOUR_SLOT.get(int(m.group(1)))
    for word, slots in PERIOD_MAP.items():
        if word in text and len(slots) == 1:
            return slots[0]
    return None


def parse_venue(text: str) -> str | None:
    v = biz.venue_by_name(text)
    return v["venue_id"] if v else None


def _norm_venue(venue_id: str) -> str:
    for v in biz.list_venues():
        if v["venue_id"] == venue_id:
            return v["name"]
    return venue_id


def parse_leave_type(text: str) -> str | None:
    for t in ("事假", "病假", "其他"):
        if t in text:
            return t
    return None


def _raw(text: str) -> str | None:
    t = text.strip()
    return t if t else None


SLOT_META: dict[str, dict] = {
    "venue": {"label": "场馆", "ask": "想预约哪个场馆？可选：羽毛球馆、篮球场、研讨间301、研讨间302", "parse": parse_venue},
    "date": {"label": "日期", "ask": "预约哪一天？（如：明天、周三、9月2日）", "parse": lambda t: _iso(parse_date(t))},
    "slot": {"label": "时段", "ask": "预约哪个时段？可选：08:00-10:00 / 10:00-12:00 / 14:00-16:00 / 16:00-18:00 / 19:00-21:00（也可回复上午/下午/晚上）", "parse": parse_slot},
    "purpose": {"label": "用途", "ask": "预约用途是什么？（如：班级活动、训练）", "parse": _raw},
    "leave_type": {"label": "类型", "ask": "请假类型是？（事假 / 病假 / 其他）", "parse": parse_leave_type},
    "start_date": {"label": "开始日期", "ask": "从哪一天开始请假？（如：明天、下周一）", "parse": lambda t: _iso(parse_date(t))},
    "end_date": {"label": "结束日期", "ask": "请到哪一天？（含当天，如：下周二）", "parse": lambda t: _iso(parse_date(t))},
    "reason": {"label": "事由", "ask": "请简要说明请假事由", "parse": _raw},
    "booking_id": {"label": "预约单号", "ask": "要取消的预约单号是？（形如 VE-0001，可先查「我的预约」）", "parse": lambda t: _first(r"VE-\d+", t)},
    "ticket_id": {"label": "请假单号", "ask": "请假单号是？（形如 LV-0001）", "parse": lambda t: _first(r"LV-\d+", t)},
}


def _first(pattern: str, text: str) -> str | None:
    m = re.search(pattern, text)
    return m.group(0) if m else None


def _iso(d: dt.date | None) -> str | None:
    return d.isoformat() if d else None


FLOW_DEFS: dict[str, dict] = {
    "book_venue": {"label": "预约场馆", "required": ["venue", "date", "slot"], "optional": ["purpose"]},
    "submit_leave": {"label": "请假申请", "required": ["leave_type", "start_date", "end_date", "reason"]},
    "cancel_booking": {"label": "取消预约", "required": ["booking_id"]},
    "approve_leave": {"label": "批准请假", "required": ["ticket_id"]},
    "leave_status": {"label": "请假单查询", "required": ["ticket_id"]},
}


# ---------- LLM 槽位抽取 ----------

def _llm_extract(tool: str, text: str, collected: dict) -> dict:
    fields = {s: SLOT_META[s]["label"] for s in FLOW_DEFS[tool]["required"] + FLOW_DEFS[tool].get("optional", [])}
    try:
        raw = llm.chat(
            [
                {"role": "system", "content": SLOT_EXTRACT_SYSTEM},
                {
                    "role": "user",
                    "content": (
                        f"今天是 {today_iso()}。工具：{tool}（{FLOW_DEFS[tool]['label']}）\n"
                        f"字段定义：{json.dumps(fields, ensure_ascii=False)}\n"
                        f"已收集：{json.dumps(collected, ensure_ascii=False)}\n"
                        f"用户消息：{text}"
                    ),
                },
            ],
            json_mode=True,
            temperature=0.0,
            max_tokens=300,
            small=True,
        )
        slots = json.loads(raw).get("slots", {})
        return slots if isinstance(slots, dict) else {}
    except Exception as exc:  # noqa: BLE001
        logger.warning("槽位抽取 LLM 调用失败，退化为启发式：%s", exc)
        return {}


def _normalize(tool: str, slot: str, value) -> str | None:
    """LLM 抽出的原始值过确定性解析器归一（日期换算、场馆名→ID 等）。"""
    if value is None:
        return None
    if slot in ("purpose", "reason", "booking_id", "ticket_id"):
        s = str(value).strip()
        return s or None
    parsed = SLOT_META[slot]["parse"](str(value))
    return str(parsed) if parsed else None


# ---------- 状态机 ----------

def start_flow(question: str, role: str, user: str, session_id: str) -> Iterator[dict]:
    """路由判定为 transaction 后的入口。"""
    # 启发式优先（确定性强），LLM 只兜底启发式没识别出口语化表述的情况
    tool = detect_tool(question)
    if tool is None and has_key():
        tool = _llm_extract_tool(question, role)
    if tool is None or tool not in tools.TOOLS:
        yield from _fallback_knowledge(question)
        return

    if tool in READ_TOOLS or tool not in FLOW_DEFS:
        args = {}
        if tool == "query_venues":
            d = parse_date(question)
            args = {"date": d.isoformat()} if d else {}
        result = tools.call(tool, args, role=role, user=user)
        yield {"type": "action_result", "tool": tool, "success": bool(result.get("ok")),
               "message": result.get("message", ""), "receipt": result.get("receipt")}
        if result.get("ok"):
            yield {"type": "answer_delta", "text": result["message"]}
        else:
            yield {"type": "answer_delta",
                   "text": f"办理未完成：{result.get('message', '未知错误')}。"}
        return

    sess = sessions.ensure(session_id, role, user)
    sess.tool, sess.phase, sess.slots, sess.last_asked = tool, "collect", {}, ""
    yield from _advance(sess, question)


def _llm_extract_tool(question: str, role: str) -> str | None:
    try:
        raw = llm.chat(
            [
                {"role": "system",
                 "content": f"根据用户消息选择最匹配的工具，只输出工具名 JSON：{{\"tool\": \"...\"}}。可选工具：\n{tools.tool_descriptions(role)}"},
                {"role": "user", "content": question},
            ],
            json_mode=True, temperature=0.0, max_tokens=100, small=True,
        )
        name = json.loads(raw).get("tool")
        return name if name in tools.TOOLS else None
    except Exception as exc:  # noqa: BLE001
        logger.warning("工具识别 LLM 调用失败：%s", exc)
        return None


def _fallback_knowledge(question: str) -> Iterator[dict]:
    yield {"type": "answer_delta", "text": "这个问题我理解为你想咨询校园信息，为你转知识库检索："}
    from . import direct
    from .pipeline import get_retriever

    yield from direct.answer_direct(question, get_retriever())


def _advance(sess: TxSession, user_text: str) -> Iterator[dict]:
    """collect 阶段：吸收新信息 → 齐了进确认，缺则追问。"""
    flow = FLOW_DEFS[sess.tool]
    required = flow["required"]

    if has_key():
        extracted = _llm_extract(sess.tool, user_text, sess.slots)
        for slot, value in extracted.items():
            if slot in SLOT_META and slot not in sess.slots:
                norm = _normalize(sess.tool, slot, value)
                if norm:
                    sess.slots[slot] = norm
    else:
        # 离线：针对上一轮追问的字段解析；首轮则对全文做结构化字段的机会性抽取
        if sess.last_asked and sess.last_asked in SLOT_META:
            value = SLOT_META[sess.last_asked]["parse"](user_text)
            if value:
                sess.slots[sess.last_asked] = str(value)
        else:
            _opportunistic_fill(sess, user_text)

    _apply_days_phrase(sess, user_text)

    missing = [s for s in required if s not in sess.slots]
    if missing:
        next_slot = missing[0]
        sess.last_asked = next_slot
        question_text = SLOT_META[next_slot]["ask"]
        yield {"type": "slot_question", "slot": next_slot, "question": question_text}
        yield {"type": "answer_delta", "text": question_text}
        return

    sess.phase = "confirm"
    sess.last_asked = ""
    yield from _emit_confirm(sess)


def _opportunistic_fill(sess: TxSession, text: str) -> None:
    """离线首轮：从原句里直接抽取结构化字段（场馆/日期/时段/类型）。"""
    for slot in FLOW_DEFS[sess.tool]["required"] + FLOW_DEFS[sess.tool].get("optional", []):
        if slot in sess.slots or slot in ("purpose", "reason", "booking_id", "ticket_id"):
            continue  # 自由文本字段不猜测，留给追问
        if slot == "date" and sess.tool == "book_venue":
            d = parse_date(text)
            if d:
                sess.slots["date"] = d.isoformat()
        elif slot in ("start_date", "end_date") and sess.tool == "submit_leave":
            dates = [d.isoformat() for d in parse_all(text)]
            if slot == "start_date" and dates:
                sess.slots["start_date"] = dates[0]
            if slot == "end_date" and len(dates) >= 2:
                sess.slots["end_date"] = dates[-1]
        else:
            value = SLOT_META[slot]["parse"](text)
            if value:
                sess.slots[slot] = str(value)


def _apply_days_phrase(sess: TxSession, text: str) -> None:
    """「请一天假 / 请三天假」：给了开始日期时直接换算结束日期。"""
    if sess.tool != "submit_leave" or "start_date" not in sess.slots:
        return
    n = _phrase_days(text)
    if not n or "end_date" in sess.slots:
        return
    start = dt.date.fromisoformat(sess.slots["start_date"])
    sess.slots["end_date"] = (start + dt.timedelta(days=n - 1)).isoformat()


def _emit_confirm(sess: TxSession) -> Iterator[dict]:
    flow = FLOW_DEFS[sess.tool]
    args = {SLOT_META[s]["label"]: sess.slots[s] for s in flow["required"] + flow.get("optional", []) if s in sess.slots}
    note = ""
    if sess.tool == "submit_leave":
        days = _service.Business.leave_days(sess.slots["start_date"], sess.slots["end_date"])
        if days >= 1:
            level = _service.approver_of(days)
            args["共"] = f"{days} 天"
            args["审批"] = f"{level}（按学校规定）"
            note = "病假超过 3 天建议附医院证明。" if sess.slots.get("leave_type") == "病假" and days > 3 else ""
    if sess.tool == "book_venue":
        args["场馆"] = _norm_venue(sess.slots["venue"])
    summary = "；".join(f"{k}：{v}" for k, v in args.items())
    yield {"type": "pending_action", "tool": sess.tool, "label": flow["label"], "args": args}
    text = f"请确认{flow['label']}信息——{summary}。{note}回复「确认」提交，或直接告诉我需要修改的地方。"
    yield {"type": "answer_delta", "text": text.strip()}


def handle_reply(sess: TxSession, user_text: str) -> Iterator[dict]:
    """collect/confirm 阶段收到用户回复后的处理（由 pipeline 在续轮调用）。"""
    if sess.phase == "collect":
        yield from _advance(sess, user_text)
        return

    # confirm 阶段：先尝试理解为「修改」，再判确认/取消
    modified = False
    for slot in list(SLOT_META):
        if slot in ("purpose", "reason"):
            continue
        if slot in FLOW_DEFS[sess.tool]["required"] or slot in sess.slots:
            value = SLOT_META[slot]["parse"](user_text) if slot != "venue" else parse_venue(user_text)
            if value and str(value) != sess.slots.get(slot):
                sess.slots[slot] = str(value)
                modified = True
    if modified:
        sess.phase = "confirm"
        yield {"type": "status", "text": "已更新，请重新确认："}
        yield from _emit_confirm(sess)
        return

    if re.search(r"确认|确定|好的|可以|提交|是的|对", user_text):
        yield from _execute(sess)
        return
    if re.search(r"取消|算了|不办|不要", user_text):
        sessions.clear(sess.session_id)
        yield {"type": "answer_delta", "text": "好的，已取消本次办理。有别的事随时找我。"}
        return
    yield {"type": "answer_delta",
           "text": "没太听懂——请回复「确认」提交，或「取消」放弃，也可以直接告诉我需要修改的日期、时段等信息。"}


def _execute(sess: TxSession) -> Iterator[dict]:
    result = tools.call(sess.tool, dict(sess.slots), role=sess.role, user=sess.user)
    ok = bool(result.get("ok"))

    if ok:
        sessions.clear(sess.session_id)
        yield {"type": "action_result", "tool": sess.tool, "success": True,
               "message": result.get("message", ""), "receipt": result.get("receipt")}
        receipt = f"（凭证号：{result['receipt']}）" if result.get("receipt") else ""
        yield {"type": "answer_delta", "text": f"办理成功：{result.get('message', '')}{receipt}"}
        return

    # 失败恢复：字段级问题重新追问该字段，其余失败结束流程并说明
    field = result.get("field")
    if field and field in SLOT_META:
        sess.phase = "collect"
        sess.last_asked = field
        sess.slots.pop(field, None)
        hint = ""
        if result.get("alternatives"):
            hint = "可选时段：" + "、".join(result["alternatives"])
        question_text = (SLOT_META[field]["ask"] + hint).strip()
        yield {"type": "action_result", "tool": sess.tool, "success": False,
               "message": result.get("message", ""), "receipt": None}
        yield {"type": "slot_question", "slot": field, "question": question_text}
        yield {"type": "answer_delta", "text": f"{result.get('message', '执行失败')}。{question_text}"}
        return

    sessions.clear(sess.session_id)
    yield {"type": "action_result", "tool": sess.tool, "success": False,
           "message": result.get("message", ""), "receipt": None}
    yield {"type": "answer_delta", "text": f"办理未完成：{result.get('message', '未知错误')}。如需继续请重新发起。"}


# ---------- 续轮意图判定 ----------

def classify_reply(user_text: str, sess: TxSession) -> str:
    """判断用户回复是继续流程、取消流程、还是切换新话题。"""
    if has_key():
        try:
            raw = llm.chat(
                [
                    {"role": "system",
                     "content": (
                         "用户正在办理业务，系统处于「" + ("确认" if sess.phase == "confirm" else "补充信息")
                         + "」阶段，已收集：" + json.dumps(sess.slots, ensure_ascii=False)
                         + "。判断用户这条消息是：continue（提供信息/确认/修改，继续流程）、"
                         "cancel（明确取消本次办理）、new_topic（转移话题问别的事）。"
                         "只输出 JSON：{\"intent\": \"continue|cancel|new_topic\"}"
                     )},
                    {"role": "user", "content": user_text},
                ],
                json_mode=True, temperature=0.0, max_tokens=60, small=True,
            )
            intent = json.loads(raw).get("intent")
            if intent in ("continue", "cancel", "new_topic"):
                return intent
        except Exception as exc:  # noqa: BLE001
            logger.warning("续轮意图 LLM 调用失败，退化为启发式：%s", exc)

    if re.search(r"取消|算了|不办了|不要了", user_text):
        return "cancel"
    if re.search(r"确认|确定|好的|可以|提交", user_text):
        return "continue"
    if re.search(r"什么|怎么|为什么|几点|哪|谁|吗", user_text):
        return "new_topic"
    if sess.last_asked and SLOT_META.get(sess.last_asked) and SLOT_META[sess.last_asked]["parse"](user_text):
        return "continue"
    for slot in sess.slots:
        if (
            SLOT_META.get(slot)
            and slot not in ("purpose", "reason")
            and SLOT_META[slot]["parse"](user_text)
        ):
            return "continue"
    if len(user_text) <= 12 and "？" not in user_text and "?" not in user_text:
        return "continue"  # 短句大概率是在回答追问
    return "new_topic"
