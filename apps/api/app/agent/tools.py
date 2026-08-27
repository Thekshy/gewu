"""工具层：agent 可调用的业务动作，权限矩阵在此执行。

业务服务（business.service）不认识角色——权限判定统一收敛在工具层，
这是 agent 安全边界的单一出口：越权调用在进入业务系统之前被拒绝。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from ..business import service as _svc
from ..config import get_settings
from ..dates import today_iso

business = _svc.Business(get_settings().data_dir / "business.db")


@dataclass
class Tool:
    name: str
    label: str
    description: str
    roles: tuple[str, ...]
    read_only: bool
    fn: Callable[[dict, str], dict]


def _fmt_venues(args: dict, user: str) -> dict:
    date = args.get("date") or today_iso()
    lines = []
    for v in business.list_venues():
        rem = business.remaining(v["venue_id"], date)
        open_slots = "、".join(s for s, left in rem.items() if left > 0) or "（今日已约满）"
        lines.append(f"- {v['name']}（{v['kind']}，每时段 {v['capacity']} 组）：{open_slots}")
    text = f"{date} 可预约场馆：\n" + "\n".join(lines)
    return {"ok": True, "message": text}


def _book(args: dict, user: str) -> dict:
    return business.book_venue(
        args["venue"], args["date"], args["slot"], args.get("purpose", ""), user
    )


def _cancel(args: dict, user: str) -> dict:
    return business.cancel_booking(args["booking_id"], user)


def _leave(args: dict, user: str) -> dict:
    return business.submit_leave(
        user, args["leave_type"], args["start_date"], args["end_date"], args["reason"]
    )


def _leave_status(args: dict, user: str) -> dict:
    return business.leave_status(args["ticket_id"], user)


def _my_bookings(args: dict, user: str) -> dict:
    items = business.my_bookings(user)
    if not items:
        return {"ok": True, "message": "你目前没有有效预约。"}
    lines = [f"- {b['booking_id']}：{b['venue']} {b['date']} {b['slot']}" for b in items]
    return {"ok": True, "message": "你的有效预约：\n" + "\n".join(lines)}


def _pending(args: dict, user: str) -> dict:
    items = business.pending_leaves()
    if not items:
        return {"ok": True, "message": "当前没有待审批的请假申请。"}
    lines = [
        f"- {t['ticket']}：{t['user']} {t['leave_type']} {t['start']}~{t['end']}（{t['days']} 天，{t['approver']}审批）"
        for t in items
    ]
    return {"ok": True, "message": "待审批请假申请：\n" + "\n".join(lines)}


def _approve(args: dict, user: str) -> dict:
    return business.approve_leave(args["ticket_id"])


TOOLS: dict[str, Tool] = {
    t.name: t
    for t in [
        Tool("query_venues", "查询场馆", "查询某天可预约的场馆与余量", ("student", "counselor"), True, _fmt_venues),
        Tool("my_bookings", "我的预约", "查询本人当前有效预约", ("student", "counselor"), True, _my_bookings),
        Tool("leave_status", "请假单查询", "按请假单号查询审批状态", ("student", "counselor"), True, _leave_status),
        Tool("pending_leaves", "待审批请假", "查看所有待审批请假申请", ("counselor",), True, _pending),
        Tool("book_venue", "预约场馆", "预约场馆的某个时段（写操作，需确认）", ("student", "counselor"), False, _book),
        Tool("cancel_booking", "取消预约", "取消本人的预约（写操作，需确认）", ("student", "counselor"), False, _cancel),
        Tool("submit_leave", "请假申请", "提交请假申请（写操作，需确认）", ("student", "counselor"), False, _leave),
        Tool("approve_leave", "批准请假", "批准一张请假单（写操作，需确认，仅辅导员）", ("counselor",), False, _approve),
    ]
}


def tool_descriptions(role: str) -> str:
    """生成给 LLM 的工具清单（只含该角色可见的工具）。"""
    lines = []
    for t in TOOLS.values():
        if role in t.roles:
            lines.append(f"- {t.name}：{t.description}")
    return "\n".join(lines)


def call(name: str, args: dict, *, role: str, user: str) -> dict:
    tool = TOOLS.get(name)
    if tool is None:
        return {"ok": False, "error": "unknown_tool", "message": f"未知工具：{name}"}
    if role not in tool.roles:
        return {
            "ok": False,
            "error": "permission",
            "message": f"当前身份（{'学生' if role == 'student' else '辅导员'}）无权执行「{tool.label}」",
        }
    try:
        return tool.fn(args, user)
    except KeyError as exc:
        return {"ok": False, "error": "missing_arg", "message": f"缺少参数：{exc.args[0]}"}
