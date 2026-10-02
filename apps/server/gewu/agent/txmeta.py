"""业务办理槽位元数据（P31-2 自 tx.py 拆分：classic 流程节点退役后，agent 侧
中间件与工具注册仍依赖的槽位/确认摘要纯逻辑收拢于此）。

P33 起收缩为槽位解析器库：流程定义（label/必填/可选）已迁入 tools.py
注册表（单一真相源），本模块只保留 SLOT_ORDER/slot_meta/normalize_slot/
build_confirm 纯解析逻辑，流程数据经 flow_defs() 派生视图消费。

核心原则沿用（PARITY §9）：日期换算一律走确定性解析（gewu.dates）；
label/ask 文案逐字保留。流程编排（collect/confirm/interrupt）已随 classic
退役——agent 路径的收集在 messages 对话内完成（WriteSlotGate + HITL）。
"""

from __future__ import annotations

import re

from gewu.agent import events as ev
from gewu.agent.tools import flow_defs
from gewu.business.db import Business, approver_of, leave_days
from gewu.dates import parse_iso

# slotOrder 槽位遍历顺序（resume 桥确认阶段修改检测依赖此序；槽位解析器
# 存在性清单——槽位定义本体在 slot_meta，流程归属在注册表）。
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
    """模型给的原始参数值过确定性解析器归一（日期换算、场馆名→ID 等）。"""
    if slot in ("purpose", "reason", "booking_id", "ticket_id"):
        v = _raw_text(value)
        return (v or "", v is not None)
    m = meta.get(slot)
    if not m:
        return "", False
    v = m["parse"](value)
    return (v or "", v is not None)


def build_confirm(state: dict, business: Business) -> tuple[dict, str, str]:
    """确认摘要 → (pending_action 事件, 确认文案, note)（PARITY §9.3.3）。

    流程定义经 flow_defs() 派生视图消费（注册表单一真相源）；tool 取值为
    flow_id——run_flow 入口的确认卡片与专属路径同形（Q3 PARITY 零改动）。
    """
    meta = slot_meta(business)
    tool = state["tx_tool"]
    slots = state.get("tx_slots") or {}
    flow = flow_defs()[tool]
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
