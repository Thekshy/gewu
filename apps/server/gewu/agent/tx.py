"""知行执行层（移植自 Go internal/agent/transaction.go）。

工具识别 → 槽位收集 → 确认 → 执行 → 冲突/失败恢复。核心原则（PARITY §9）：
- 读操作直接执行；写操作必须经过「确认摘要 → 用户确认 → 执行 → 回执」；
- 日期换算一律走确定性解析（gewu.dates），LLM 只负责"找出"表述；
- 执行失败不是终点：冲突给可选项、字段非法重新追问，恢复也是流程的一部分。

本模块只含纯逻辑与事件构造；图接线（interrupt/续轮入口）在 graph.py。
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from typing import Any

from gewu.agent import events as ev
from gewu.agent.jsonx import json_str, parse_json_object
from gewu.agent.prompts import CLASSIFY_REPLY_SYSTEM, LLM_EXTRACT_TOOL_SYSTEM, SLOT_EXTRACT_SYSTEM
from gewu.business.db import Business, Result, approver_of, leave_days, receipt_id
from gewu.dates import parse_all, parse_iso, today_iso
from gewu.llm.service import LLMService

# ---------- 工具识别（离线启发式，按序首个命中；顺序是契约） ----------

_TOOL_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("cancel_booking", re.compile(r"取消预约|退订")),
    ("approve_leave", re.compile(r"批准|通过.*(请假|申请)")),
    ("pending_leaves", re.compile(r"待审批|审批.*(请假|申请)|谁.*请了假")),
    ("leave_status", re.compile(r"请假.*(单号|进度|状态|批了没)|LV-\d+")),
    ("my_bookings", re.compile(r"我的预约|我预约了|我订了")),
    ("query_venues", re.compile(r"(有|哪些|什么|能).*(场馆|场地|研讨间)|场馆.*(有|能|可)")),
    ("submit_leave", re.compile(r"请假|事假|病假|销假|休.*假|请.*天.*假")),
    # book_venue 必须是"办理动词 + 场馆类宾语共现"：裸 `预约` 会把"预约心理咨询"
    # 一切"预约X"都误选成场馆工具（P7 修复）。
    (
        "book_venue",
        re.compile(r"(预约|预订|订).*(馆|场|间|羽毛球|篮球|游泳|乒乓|网球|健身|研讨|教室|场地)"),
    ),
]

# nonVenueRe 负向双保险：咨询/就医类词与"预约"共现时不选场馆工具。
_NON_VENUE_RE = re.compile(r"心理咨询|心理辅导|心理咨询室|挂号|看医生|校医|咨询老师|辅导员")

_READ_TOOLS = {"query_venues", "my_bookings", "leave_status", "pending_leaves"}

_CN_NUM = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
_DAYS_PHRASE_RE = re.compile(r"([一二三四五六七八九]|[0-9]+)\s*天")

_BOOKING_ID_RE = re.compile(r"VE-\d+")
_TICKET_ID_RE = re.compile(r"LV-\d+")
_HOUR_TEXT_RE = re.compile(r"(\d{1,2})\s*[点:：时]\s*(\d{2})?")

# 时段口语映射（多选词不唯一时不命中，须指明具体时段）。
_PERIOD_MAP = {
    "上午": ["08:00-10:00", "10:00-12:00"],
    "中午": ["14:00-16:00"],
    "下午": ["14:00-16:00", "16:00-18:00"],
    "晚上": ["19:00-21:00"],
    "傍晚": ["16:00-18:00", "19:00-21:00"],
}
_HOUR_SLOT = {
    8: "08:00-10:00",
    10: "10:00-12:00",
    14: "14:00-16:00",
    16: "16:00-18:00",
    19: "19:00-21:00",
}

# confirm 阶段的确认/取消词。
_CONFIRM_MODIFY_RE = re.compile(r"确认|确定|好的|可以|提交|是的|对")
_CANCEL_WORD_RE = re.compile(r"取消|算了|不办|不要")
_REPLY_CANCEL_RE = re.compile(r"取消|算了|不办了|不要了")
_REPLY_CONFIRM_RE = re.compile(r"确认|确定|好的|可以|提交")
_REPLY_TOPIC_RE = re.compile(r"什么|怎么|为什么|几点|哪|谁|吗")

# slotOrder 槽位遍历顺序（confirm 修改检测依赖此序）。
SLOT_ORDER = [
    "venue",
    "date",
    "slot",
    "purpose",
    "leave_type",
    "start_date",
    "end_date",
    "reason",
    "booking_id",
    "ticket_id",
]

# flowOrder / flowDefs 办理流程定义。
FLOW_ORDER = ["book_venue", "submit_leave", "cancel_booking", "approve_leave", "leave_status"]
FLOW_DEFS: dict[str, dict] = {
    "book_venue": {
        "label": "预约场馆",
        "required": ["venue", "date", "slot"],
        "optional": ["purpose"],
    },
    "submit_leave": {
        "label": "请假申请",
        "required": ["leave_type", "start_date", "end_date", "reason"],
        "optional": [],
    },
    "cancel_booking": {"label": "取消预约", "required": ["booking_id"], "optional": []},
    "approve_leave": {"label": "批准请假", "required": ["ticket_id"], "optional": []},
    "leave_status": {"label": "请假单查询", "required": ["ticket_id"], "optional": []},
}


def detect_tool(question: str) -> str:
    """启发式工具识别（确定性强，LLM 只兜底口语化表述）。"""
    for name, pattern in _TOOL_PATTERNS:
        if not pattern.search(question):
            continue
        if name == "book_venue" and _NON_VENUE_RE.search(question):
            continue
        return name
    return ""


def is_read_tool(tool: str) -> bool:
    return tool in _READ_TOOLS


def phrase_days(text: str) -> int | None:
    """「请一天假 / 请三天假」的天数。"""
    m = _DAYS_PHRASE_RE.search(text)
    if not m:
        return None
    if m.group(1) in _CN_NUM:
        return _CN_NUM[m.group(1)]
    try:
        return int(m.group(1))
    except ValueError:
        return None


def is_question_mark(s: str) -> bool:
    return "？" in s or "?" in s


# ---------- 槽位解析 ----------


def parse_slot(text: str) -> str:
    """时段解析（PARITY §9.3.1）。"""
    for slot in ["08:00-10:00", "10:00-12:00", "14:00-16:00", "16:00-18:00", "19:00-21:00"]:
        if slot in text:
            return slot
        if len(slot) >= 5 and slot[:5] in text:
            return slot
    m = _HOUR_TEXT_RE.search(text)
    if m:
        h = int(m.group(1))
        if h % 12 in _HOUR_SLOT and h % 12 != 0:
            return _HOUR_SLOT[h % 12]
        if h in _HOUR_SLOT:
            return _HOUR_SLOT[h]
    for word, slots in _PERIOD_MAP.items():
        if word in text and len(slots) == 1:
            return slots[0]
    return ""


def parse_leave_type(text: str) -> str:
    for t in ("事假", "病假", "其他"):
        if t in text:
            return t
    return ""


def _raw_text(text: str) -> str | None:
    t = text.strip()
    return t or None


def _first_match(pattern: re.Pattern, text: str) -> str | None:
    m = pattern.search(text)
    return m.group(0) if m else None


def _parse_venue(business: Business, text: str) -> str | None:
    v = business.venue_by_name(text)
    return v["venue_id"] if v else None


def _parse_date_slot(text: str) -> str | None:
    iso = parse_iso(text)
    return iso or None


# slotMetaTable：label/ask 文案逐字保留（PARITY §9.3 表格）。
def slot_meta(business: Business) -> dict[str, dict]:
    return {
        "venue": {
            "label": "场馆",
            "ask": "想预约哪个场馆？可选：羽毛球馆、篮球场、研讨间301、研讨间302",
            "parse": lambda t: (_p := _parse_venue(business, t)) and _p or None,
        },
        "date": {
            "label": "日期",
            "ask": "预约哪一天？（如：明天、周三、9月2日）",
            "parse": _parse_date_slot,
        },
        "slot": {
            "label": "时段",
            "ask": "预约哪个时段？可选：08:00-10:00 / 10:00-12:00 / 14:00-16:00 / 16:00-18:00 / 19:00-21:00（也可回复上午/下午/晚上）",
            "parse": lambda t: (_s := parse_slot(t)) or None,
        },
        "purpose": {
            "label": "用途",
            "ask": "预约用途是什么？（如：班级活动、训练）",
            "parse": _raw_text,
        },
        "leave_type": {
            "label": "类型",
            "ask": "请假类型是？（事假 / 病假 / 其他）",
            "parse": lambda t: (_t := parse_leave_type(t)) or None,
        },
        "start_date": {
            "label": "开始日期",
            "ask": "从哪一天开始请假？（如：明天、下周一）",
            "parse": _parse_date_slot,
        },
        "end_date": {
            "label": "结束日期",
            "ask": "请到哪一天？（含当天，如：下周二）",
            "parse": _parse_date_slot,
        },
        "reason": {"label": "事由", "ask": "请简要说明请假事由", "parse": _raw_text},
        "booking_id": {
            "label": "预约单号",
            "ask": "要取消的预约单号是？（形如 VE-0001，可先查「我的预约」）",
            "parse": lambda t: _first_match(_BOOKING_ID_RE, t),
        },
        "ticket_id": {
            "label": "请假单号",
            "ask": "请假单号是？（形如 LV-0001）",
            "parse": lambda t: _first_match(_TICKET_ID_RE, t),
        },
    }


def normalize_slot(meta: dict, slot: str, value: str) -> tuple[str, bool]:
    """LLM 抽出的原始值过确定性解析器归一（日期换算、场馆名→ID 等）。"""
    if slot in ("purpose", "reason", "booking_id", "ticket_id"):
        v = _raw_text(value)
        return (v or "", v is not None)
    m = meta.get(slot)
    if not m:
        return "", False
    v = m["parse"](value)
    return (v or "", v is not None)


def missing_slots(flow: dict, slots: dict[str, str]) -> list[str]:
    """按流程必填顺序找缺失槽位。"""
    return [s for s in flow["required"] if s not in slots]


def apply_days_phrase(state: dict, text: str) -> None:
    """「请一天假 / 请三天假」：给了开始日期时直接换算结束日期（就地改 tx_slots）。"""
    if state.get("tx_tool") != "submit_leave":
        return
    slots = state.get("tx_slots") or {}
    start = slots.get("start_date")
    if not start or "end_date" in slots:
        return
    n = phrase_days(text)
    if not n or n <= 0:
        return
    from datetime import date, timedelta  # noqa: PLC0415

    try:
        sd = date.fromisoformat(start)
    except ValueError:
        return
    slots["end_date"] = (sd + timedelta(days=n - 1)).isoformat()
    state["tx_slots"] = slots


# ---------- 确认摘要（PARITY §9.3.3） ----------


def build_confirm(state: dict, business: Business) -> tuple[dict, str, str]:
    """确认摘要 → (pending_action 事件, 确认文案, note)。"""
    meta = slot_meta(business)
    tool = state["tx_tool"]
    slots = state.get("tx_slots") or {}
    flow = FLOW_DEFS[tool]
    args: dict[str, str] = {}
    for s in flow["required"] + flow["optional"]:
        if s in slots:
            args[meta[s]["label"]] = slots[s]
    note = ""
    if tool == "submit_leave":
        days = leave_days(slots.get("start_date", ""), slots.get("end_date", ""))
        if days >= 1:
            level = approver_of(days)
            args["共"] = f"{days} 天"
            args["审批"] = f"{level}（按学校规定）"
            if slots.get("leave_type") == "病假" and days > 3:
                note = "病假超过 3 天建议附医院证明。"
    if tool == "book_venue":
        v = business.venue_by_name(slots.get("venue", ""))
        if not v:
            for venue in business.list_venues():
                if venue["venue_id"] == slots.get("venue"):
                    v = venue
                    break
        args["场馆"] = v["name"] if v else slots.get("venue", "")
    summary = "；".join(f"{k}：{v}" for k, v in args.items())
    text = f"请确认{flow['label']}信息——{summary}。{note}回复「确认」提交，或直接告诉我需要修改的地方。"
    return ev.pending_action_evt(tool, flow["label"], args), text.strip(), note


# ---------- 续轮意图判定（PARITY §9.5） ----------


def classify_reply(llm: LLMService | None, meta: dict, user_text: str, state: dict) -> str:
    """判断用户回复是继续流程、取消流程、还是切换新话题（LLM 优先，启发式兜底）。"""
    if llm is not None and llm.has_key():
        phase = "确认" if state.get("tx_phase") == "confirm" else "补充信息"
        prompt = CLASSIFY_REPLY_SYSTEM.format(
            phase=phase, slots=json.dumps(state.get("tx_slots") or {}, ensure_ascii=False)
        )
        try:
            raw = llm.chat(
                [("system", prompt), ("user", user_text)],
                json_mode=True,
                small=True,
                max_tokens=60,
            )
            obj = parse_json_object(raw)
            intent = json_str(obj, "intent")
            if intent in ("continue", "cancel", "new_topic"):
                return intent
        except Exception:  # noqa: BLE001 - 退化为启发式
            pass
    if _REPLY_CANCEL_RE.search(user_text):
        return "cancel"
    if _REPLY_CONFIRM_RE.search(user_text):
        return "continue"
    if _REPLY_TOPIC_RE.search(user_text):
        return "new_topic"
    if state.get("tx_last_asked"):
        m = meta.get(state["tx_last_asked"])
        if m and m["parse"](user_text):
            return "continue"
    for slot in SLOT_ORDER:
        if slot in ("purpose", "reason"):
            continue
        if slot not in (state.get("tx_slots") or {}):
            continue
        m = meta.get(slot)
        if m and m["parse"](user_text):
            return "continue"
    if len(user_text) <= 12 and not is_question_mark(user_text):
        return "continue"  # 短句大概率是在回答追问
    return "new_topic"


def llm_extract_tool(
    llm: LLMService, tools: dict, business: Business, question: str, role: str, meta: dict
) -> str:
    """LLM 选工具（启发式未识别且有 key 时）。"""
    from gewu.agent.tools import tool_descriptions  # noqa: PLC0415

    try:
        raw = llm.chat(
            [
                ("system", LLM_EXTRACT_TOOL_SYSTEM.format(tools=tool_descriptions(tools, role))),
                ("user", question),
            ],
            json_mode=True,
            small=True,
            max_tokens=100,
        )
        name = json_str(parse_json_object(raw), "tool")
    except Exception:  # noqa: BLE001
        return ""
    if name == "book_venue" and _NON_VENUE_RE.search(question):
        return ""  # 负向双保险对 LLM 路径同样生效（P7-1）
    return name if name in tools else ""


def llm_extract_slots(
    llm: LLMService, meta: dict, tool: str, text: str, collected: dict[str, str]
) -> dict[str, str]:
    """LLM 槽位抽取（失败退化为空 map，由确定性路径兜底）。"""
    flow = FLOW_DEFS[tool]
    fields = {s: meta[s]["label"] for s in flow["required"] + flow["optional"]}
    user_msg = (
        f"今天是 {today_iso()}。工具：{tool}（{flow['label']}）\n"
        f"字段定义：{json.dumps(fields, ensure_ascii=False)}\n"
        f"已收集：{json.dumps(collected, ensure_ascii=False)}\n用户消息：{text}"
    )
    try:
        raw = llm.chat(
            [("system", SLOT_EXTRACT_SYSTEM), ("user", user_msg)],
            json_mode=True,
            small=True,
            max_tokens=300,
        )
        obj = parse_json_object(raw)
    except Exception:  # noqa: BLE001
        return {}
    out: dict[str, str] = {}
    for k, v in obj.get("slots", {}).items():
        if isinstance(v, str):
            out[k] = v
    return out


def execute_tool(llm_tools: dict, business: Business, state: dict, role: str, user: str) -> Result:
    """确认后的执行（CallTool 单一出口）。"""
    from gewu.agent.tools import call_tool  # noqa: PLC0415

    return call_tool(llm_tools, business, state["tx_tool"], state.get("tx_slots") or {}, role, user)


def opportunistic_fill(state: dict, meta: dict, text: str) -> None:
    """离线首轮：从原句里直接抽取结构化字段（场馆/日期/时段/类型）。

    自由文本字段（purpose/reason/单号）不猜测，留给追问。
    """
    flow = FLOW_DEFS[state["tx_tool"]]
    slots = state.get("tx_slots") or {}
    for slot in flow["required"] + flow["optional"]:
        if slot in slots:
            continue
        if slot in ("purpose", "reason", "booking_id", "ticket_id"):
            continue
        if slot == "date" and state["tx_tool"] == "book_venue":
            iso = _parse_date_slot(text)
            if iso:
                slots["date"] = iso
        elif slot in ("start_date", "end_date") and state["tx_tool"] == "submit_leave":
            dates_list = parse_all(text)
            if slot == "start_date" and dates_list:
                slots["start_date"] = dates_list[0].isoformat()
            if slot == "end_date" and len(dates_list) >= 2:
                slots["end_date"] = dates_list[-1].isoformat()
        else:
            m = meta.get(slot)
            if m:
                v = m["parse"](text)
                if v:
                    slots[slot] = v
    state["tx_slots"] = slots


_ = receipt_id  # 保留导入（单号格式经 business.receipt_id 统一出口）
_ = (Any, Callable)  # 类型引用占位
