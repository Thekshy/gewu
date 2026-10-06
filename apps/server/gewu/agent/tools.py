"""agent 工具层（移植自 Go internal/agent/tools.go；P33 起为办理流程注册表）。

业务系统不认识角色——权限判定统一收敛在工具层（安全边界单一出口）：
未知工具 / 越权 / 缺参数在进入业务系统之前被拦截。

P33 注册表单一真相源：ToolSpec 一行内嵌写性（read_only）/ 槽位
（slots_required/slots_optional）/ 触发词与业务域（triggers/domain，检索
台阶数据）。原 txmeta.FLOW_DEFS 与 mw.WRITE_TOOLS 两个静态常量改由
flow_defs() / write_tools() 派生视图替代——加新办理流程只动本表一行，
漏登静默失去确认门的三源陷阱结构性消灭。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from gewu.business import db as biz
from gewu.business.db import Business, Result
from gewu.dates import today_iso


def _need(args: dict[str, str], key: str) -> tuple[str, Result | None]:
    """取必填参数，缺失时返回 missing_arg。"""
    if key not in args:
        return "", Result(err="missing_arg", message=f"缺少参数：{key}")
    return args[key], None


@dataclass
class ToolSpec:
    """agent 可调用的业务动作 + 内嵌流程定义（注册表行，权限矩阵在 CallTool 执行）。

    slots_required 非空即「流程工具」（可经 run_flow 收编执行，query_flows
    可见）；read_only=False 且有流程定义 = 写流程（HITL 确认门对象）。
    """

    name: str
    label: str
    description: str
    roles: list[str]
    read_only: bool
    fn: Callable[[Business, dict[str, str], str], Result]
    slots_required: list[str] = field(default_factory=list)
    slots_optional: list[str] = field(default_factory=list)
    triggers: list[str] = field(default_factory=list)  # 触发词（检索台阶数据，Q6 只就位）
    domain: str = ""  # 业务域标签（booking/leave，检索过滤用）


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


def tools_for() -> dict[str, ToolSpec]:
    """构造注册表（工具 + 流程定义单一真相源，PARITY §10 权限矩阵）。

    P33 数据口径：slots_required 非空 = 流程工具（与原 FLOW_DEFS 成员集
    等价：五件含 leave_status 读流程）；triggers 全量就位供检索台阶消费。
    """
    specs = [
        ToolSpec(
            "query_venues",
            "查询场馆",
            "查询某天可预约的场馆与余量",
            ["student", "counselor", "guest"],
            True,
            _fmt_venues,
            triggers=["场馆", "可预约", "余量", "场地", "空场"],
            domain="booking",
        ),
        ToolSpec(
            "my_bookings",
            "我的预约",
            "查询本人当前有效预约",
            ["student", "counselor", "guest"],
            True,
            _my_bookings,
            triggers=["我的预约", "预约记录", "订了什么"],
            domain="booking",
        ),
        ToolSpec(
            "leave_status",
            "请假单查询",
            "按请假单号查询审批状态",
            ["student", "counselor", "guest"],
            True,
            _leave_status,
            slots_required=["ticket_id"],
            triggers=["请假单", "审批进度", "审批状态", "请假进度", "批了吗"],
            domain="leave",
        ),
        ToolSpec(
            "pending_leaves",
            "待审批请假",
            "查看所有待审批请假申请",
            ["counselor"],
            True,
            _pending_leaves,
            triggers=["待审批", "待办请假", "等待审批"],
            domain="leave",
        ),
        ToolSpec(
            "book_venue",
            "预约场馆",
            "预约场馆的某个时段（写操作，需确认）",
            ["student", "counselor", "guest"],
            False,
            _book,
            slots_required=["venue", "date", "slot"],
            slots_optional=["purpose"],
            triggers=["预约", "订场馆", "订场地", "约场地", "占场"],
            domain="booking",
        ),
        ToolSpec(
            "cancel_booking",
            "取消预约",
            "取消本人的预约（写操作，需确认）",
            ["student", "counselor", "guest"],
            False,
            _cancel,
            slots_required=["booking_id"],
            triggers=["取消预约", "退订", "不约了"],
            domain="booking",
        ),
        ToolSpec(
            "submit_leave",
            "请假申请",
            "提交请假申请（写操作，需确认）",
            ["student", "counselor", "guest"],
            False,
            _submit_leave,
            slots_required=["leave_type", "start_date", "end_date", "reason"],
            triggers=["请假", "请事假", "请病假", "提交请假", "休个假"],
            domain="leave",
        ),
        ToolSpec(
            "approve_leave",
            "批准请假",
            "批准一张请假单（写操作，需确认，仅辅导员）",
            ["counselor"],
            False,
            _approve_leave,
            slots_required=["ticket_id"],
            triggers=["批准", "审批通过", "同意请假", "通过请假"],
            domain="leave",
        ),
    ]
    return {t.name: t for t in specs}


def flow_defs() -> dict[str, dict]:
    """流程定义派生视图（原 txmeta.FLOW_DEFS 常量的活体替代）。

    name → {label, required, optional}，mw 槽位门/HITL、txmeta 确认摘要、
    resume 桥统一消费；注册表是唯一真相源，本视图只读不缓存。
    """
    return {
        t.name: {
            "label": t.label,
            "required": list(t.slots_required),
            "optional": list(t.slots_optional),
        }
        for t in tools_for().values()
        if t.slots_required
    }


def write_tools() -> set[str]:
    """写性判定派生视图（原 mw.WRITE_TOOLS 静态集合的替代）。

    口径：read_only=False 且有流程定义（一致性测试闸保证写工具必有
    slots_required，两条件恒等价，双写防未来加无槽位写工具时静默设门）。
    """
    return {t.name for t in tools_for().values() if not t.read_only and t.slots_required}


def resolve_flow(tool_call: dict) -> ToolSpec | None:
    """闸动态解析（Q4）：tool_call → 流程型 ToolSpec；非流程/未知 → None。

    run_flow 入口解开 args.flow_id 查注册表，专属工具按 name 直查——两形态
    经同一判定收敛，闸从「工具名静态判定」升级为「解开参数动态判定」。
    None = 闸放行（读流程直执行 / 未知 flow_id 由执行层返回 unknown_tool
    回执，执行层仍是单一出口）。
    """
    name = str(tool_call.get("name", "") or "")
    key = name
    if name == "run_flow":
        args = tool_call.get("args") or {}
        key = str(args.get("flow_id", "") or "").strip()
    t = tools_for().get(key) if key else None
    return t if t is not None and t.slots_required else None


def flow_args(tool_call: dict) -> dict:
    """tool_call → 平铺槽位参数（专属工具=args 原样；run_flow=解出内层 slots）。

    与 resolve_flow 配对使用：spec 定流程、本函数定槽位。run_flow 形态的
    槽位键主名 slots（注：@tool 参数不可名 args——pydantic schema 会改写
    为 v__args），兼容模型偶发的 args 键；内层非 dict（畸形参数）返回 {}，
    交给槽位门/missing_arg 回执兜底。
    """
    args = tool_call.get("args") or {}
    if tool_call.get("name") == "run_flow":
        inner = args.get("slots", args.get("args"))
        return inner if isinstance(inner, dict) else {}
    return args if isinstance(args, dict) else {}


def role_label(role: str) -> str:
    """角色中文名（越权提示文案用）。

    P37 修复：原实现是 `"学生" if role == "student" else "辅导员"`——admin 也被
    标成「辅导员」，于是管理员演示办理时会收到自相矛盾的回执（右上角写着管理员，
    回执说你是辅导员且无权预约）。
    """
    labels = {"student": "学生", "counselor": "辅导员", "admin": "管理员", "guest": "游客"}
    return labels.get(role) or role or "未知身份"


def has_role(roles: list[str], role: str) -> bool:
    """权限矩阵（P37 拍板 A）：admin 视为超集——可代学生办理，也可审批，
    服务演示与运维；其余角色严格按 ToolSpec.roles 判定。"""
    return role == "admin" or role in roles


def tool_descriptions(tools: dict[str, ToolSpec], role: str) -> str:
    """生成给 LLM 的工具清单（只含该角色可见的工具）。"""
    lines = [
        f"- {name}：{tools[name].description}"
        for name in TOOL_ORDER
        if name in tools and has_role(tools[name].roles, role)
    ]
    return "\n".join(lines)


def call_tool(
    tools: dict[str, ToolSpec],
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
