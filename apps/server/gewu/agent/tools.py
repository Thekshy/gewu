"""agent 工具层（移植自 Go internal/agent/tools.go）。

业务系统不认识角色——权限判定统一收敛在工具层（安全边界单一出口）：
未知工具 / 越权 / 缺参数在进入业务系统之前被拦截。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from gewu.business import db as biz
from gewu.business.db import Business, Result
from gewu.dates import today_iso


def _need(args: dict[str, str], key: str) -> tuple[str, Result | None]:
    """取必填参数，缺失时返回 missing_arg。"""
    if key not in args:
        return "", Result(err="missing_arg", message=f"缺少参数：{key}")
    return args[key], None


@dataclass
class Tool:
    """agent 可调用的业务动作（权限矩阵在 CallTool 执行）。"""

    name: str
    label: str
    description: str
    roles: list[str]
    read_only: bool
    fn: Callable[[Business, dict[str, str], str], Result]


def _fmt_venues(b: Business, args: dict[str, str], _user: str) -> Result:
    date_ = args.get("date") or today_iso()
    venues = b.list_venues()
    lines = []
    for v in venues:
        rem = b.remaining(v["venue_id"], date_)
        open_slots = [s for s in biz.SLOTS if rem.get(s, 0) > 0]
        open_text = "、".join(open_slots) or "（今日已约满）"
        lines.append(f"- {v['name']}（{v['kind']}，每时段 {v['capacity']} 组）：{open_text}")
    return Result(ok=True, message=f"{date_} 可预约场馆：\n" + "\n".join(lines))


def _my_bookings(b: Business, _args: dict[str, str], user: str) -> Result:
    items = b.my_bookings(user)
    if not items:
        return Result(ok=True, message="你目前没有有效预约。")
    lines = [f"- {i['booking_id']}：{i['venue']} {i['date']} {i['slot']}" for i in items]
    return Result(ok=True, message="你的有效预约：\n" + "\n".join(lines))


def _pending_leaves(b: Business, _args: dict[str, str], _user: str) -> Result:
    items = b.pending_leaves()
    if not items:
        return Result(ok=True, message="当前没有待审批的请假申请。")
    lines = [
        f"- {t['ticket']}：{t['user']} {t['leave_type']} {t['start']}~{t['end']}"
        f"（{t['days']} 天，{t['approver']}审批）"
        for t in items
    ]
    return Result(ok=True, message="待审批请假申请：\n" + "\n".join(lines))


def _book(b: Business, args: dict[str, str], user: str) -> Result:
    venue, bad = _need(args, "venue")
    if bad:
        return bad
    date_, bad = _need(args, "date")
    if bad:
        return bad
    slot, bad = _need(args, "slot")
    if bad:
        return bad
    return b.book_venue(venue, date_, slot, args.get("purpose", ""), user)


def _cancel(b: Business, args: dict[str, str], user: str) -> Result:
    booking_id, bad = _need(args, "booking_id")
    if bad:
        return bad
    return b.cancel_booking(booking_id, user)


def _submit_leave(b: Business, args: dict[str, str], user: str) -> Result:
    leave_type, bad = _need(args, "leave_type")
    if bad:
        return bad
    start, bad = _need(args, "start_date")
    if bad:
        return bad
    end, bad = _need(args, "end_date")
    if bad:
        return bad
    reason, bad = _need(args, "reason")
    if bad:
        return bad
    return b.submit_leave(user, leave_type, start, end, reason)


def _leave_status(b: Business, args: dict[str, str], user: str) -> Result:
    ticket_id, bad = _need(args, "ticket_id")
    if bad:
        return bad
    return b.leave_status(ticket_id, user)


def _approve_leave(b: Business, args: dict[str, str], _user: str) -> Result:
    ticket_id, bad = _need(args, "ticket_id")
    if bad:
        return bad
    return b.approve_leave(ticket_id)


# toolOrder 工具表遍历顺序（影响给 LLM 的清单顺序）。
TOOL_ORDER = [
    "query_venues",
    "my_bookings",
    "leave_status",
    "pending_leaves",
    "book_venue",
    "cancel_booking",
    "submit_leave",
    "approve_leave",
]


def tools_for() -> dict[str, Tool]:
    """构造工具表（权限矩阵，PARITY §10）。"""
    specs = [
        Tool(
            "query_venues",
            "查询场馆",
            "查询某天可预约的场馆与余量",
            ["student", "counselor"],
            True,
            _fmt_venues,
        ),
        Tool(
            "my_bookings",
            "我的预约",
            "查询本人当前有效预约",
            ["student", "counselor"],
            True,
            _my_bookings,
        ),
        Tool(
            "leave_status",
            "请假单查询",
            "按请假单号查询审批状态",
            ["student", "counselor"],
            True,
            _leave_status,
        ),
        Tool(
            "pending_leaves",
            "待审批请假",
            "查看所有待审批请假申请",
            ["counselor"],
            True,
            _pending_leaves,
        ),
        Tool(
            "book_venue",
            "预约场馆",
            "预约场馆的某个时段（写操作，需确认）",
            ["student", "counselor"],
            False,
            _book,
        ),
        Tool(
            "cancel_booking",
            "取消预约",
            "取消本人的预约（写操作，需确认）",
            ["student", "counselor"],
            False,
            _cancel,
        ),
        Tool(
            "submit_leave",
            "请假申请",
            "提交请假申请（写操作，需确认）",
            ["student", "counselor"],
            False,
            _submit_leave,
        ),
        Tool(
            "approve_leave",
            "批准请假",
            "批准一张请假单（写操作，需确认，仅辅导员）",
            ["counselor"],
            False,
            _approve_leave,
        ),
    ]
    return {t.name: t for t in specs}


def role_label(role: str) -> str:
    """角色中文名（越权提示文案用）。"""
    return "学生" if role == "student" else "辅导员"


def has_role(roles: list[str], role: str) -> bool:
    return role in roles


def tool_descriptions(tools: dict[str, Tool], role: str) -> str:
    """生成给 LLM 的工具清单（只含该角色可见的工具）。"""
    lines = [
        f"- {name}：{tools[name].description}"
        for name in TOOL_ORDER
        if name in tools and has_role(tools[name].roles, role)
    ]
    return "\n".join(lines)


def call_tool(
    tools: dict[str, Tool],
    business: Business,
    name: str,
    args: dict[str, str],
    role: str,
    user: str,
) -> Result:
    """工具层单一出口：未知工具 / 越权 / 缺参数在进入业务系统前拦截。"""
    t = tools.get(name)
    if t is None:
        return Result(err="unknown_tool", message=f"未知工具：{name}")
    if not has_role(t.roles, role):
        return Result(
            err="permission",
            message=f"当前身份（{role_label(role)}）无权执行「{t.label}」",
        )
    return t.fn(business, args, user)
