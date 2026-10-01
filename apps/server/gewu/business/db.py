"""mock 校内业务库（PostgreSQL）：场馆预约与请假审批（P21-2 自 SQLite 迁 PG）。

真实学校里这是独立的业务后端；本项目内用同进程模块模拟，agent 只能通过
agent 工具层访问它，模块边界与生产架构一致。业务规则与语料保持一致：
请假 1—3 天辅导员批、3 天以上 7 天以内学院批、超过 7 天教务处批。

连接形态对齐 rag.Store（psycopg ConnectionPool）；book_venue 这类
「检查→写入」复合操作持进程锁串行化，保持 SQLite 单连接时代的语义
（跨进程竞态由事务行锁兜底）。方法签名/返回形状/权限语义零变化。
date/slot/created_at 维持 TEXT 存储（ISO 串字典序比较，无时区换算需求）。

PG 迁移注意：`user` 是 PG 保留字，列名与全部 SQL 引用必须带双引号 "user"。
"""

from __future__ import annotations

import dataclasses
import threading
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from psycopg_pool import ConnectionPool

from gewu.dates import today_iso

CN_TZ = timezone(timedelta(hours=8), name="UTC+8")

# Slots 全部可预约时段。
SLOTS = ["08:00-10:00", "10:00-12:00", "14:00-16:00", "16:00-18:00", "19:00-21:00"]
SLOTS_SET = set(SLOTS)

_VENUE_SEED = [
    ("venue-badminton", "羽毛球馆", "体育场馆", 2),
    ("venue-basketball", "篮球场", "体育场馆", 1),
    ("venue-room301", "研讨间301", "图书馆研讨间", 1),
    ("venue-room302", "研讨间302", "图书馆研讨间", 1),
]

_SCHEMA = """
CREATE TABLE IF NOT EXISTS venues (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    kind TEXT NOT NULL,
    capacity INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS bookings (
    id BIGSERIAL PRIMARY KEY,
    venue_id TEXT NOT NULL,
    date TEXT NOT NULL,
    slot TEXT NOT NULL,
    purpose TEXT NOT NULL DEFAULT '',
    "user" TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT '有效',
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS leave_tickets (
    id BIGSERIAL PRIMARY KEY,
    "user" TEXT NOT NULL,
    leave_type TEXT NOT NULL,
    start_date TEXT NOT NULL,
    end_date TEXT NOT NULL,
    days INTEGER NOT NULL,
    reason TEXT NOT NULL,
    approver_level TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT '待审批',
    created_at TEXT NOT NULL
);
"""


def receipt_id(prefix: str, id_: int) -> str:
    """单号格式与 Go receiptID 一致：VE-0001 / LV-0001。"""
    return f"{prefix}-{id_:04d}"


def receipt_num(receipt: str) -> int | None:
    """单号 → 数字部分（'VE-0001' → 1；非法输入 None=查无此单）。

    SQLite 时代靠整数亲和等值匹配 id，PG 无隐式转换，统一在 Python 侧归一。
    """
    tail = receipt.split("-")[-1] if "-" in receipt else receipt
    try:
        return int(tail)
    except ValueError:
        return None


def num_part(receipt: str) -> str:
    """兼容保留（旧内部名）：单号数字文本部分。"""
    return receipt.split("-")[-1] if "-" in receipt else receipt


def now_cn_iso() -> str:
    return datetime.now(CN_TZ).strftime("%Y-%m-%d %H:%M:%S")


def approver_of(days: int) -> str:
    """按请假天数映射审批层级：≤3 辅导员；≤7 学院；>7 教务处。"""
    if days <= 3:
        return "辅导员"
    if days <= 7:
        return "学院"
    return "教务处"


def leave_days(start: str, end: str) -> int:
    """计算请假天数（含首尾）；end<start 或日期非法返回 -1。"""
    try:
        s = date.fromisoformat(start)
        e = date.fromisoformat(end)
    except ValueError:
        return -1
    if e < s:
        return -1
    return (e - s).days + 1


@dataclass
class Result:
    """业务操作结果（PARITY §8，与工具层约定的字段一一对应）。

    注意：属性名 `field`（对齐 Go Result.Field）会遮蔽 dataclasses.field，
    因此本类内默认工厂用全限定调用。
    """

    ok: bool = False
    err: str = ""  # "" | invalid | quota | conflict | not_found | permission | internal | missing_arg | unknown_tool
    field: str = ""  # 字段级失败（恢复流程据此重新追问）
    message: str = ""
    receipt: str = ""  # VE-XXXX / LV-XXXX
    alternatives: list[str] = dataclasses.field(default_factory=list)  # 冲突时可选时段
    days: int = 0  # submit_leave 成功时的天数
    approver: str = ""


def ok_msg(message: str) -> Result:
    return Result(ok=True, message=message)


def fail(err: str, field_: str, message: str) -> Result:
    return Result(err=err, field=field_, message=message)


@dataclass(frozen=True)
class BookingFull:
    booking_id: str
    venue: str
    date: str
    slot: str
    user: str


@dataclass(frozen=True)
class TicketView:
    ticket: str
    user: str
    leave_type: str
    start: str
    end: str
    days: int
    approver: str
    status: str


class Business:
    """业务库（PG 连接池）：操作级进程锁 + 事务（连接上下文=事务边界）。"""

    def __init__(self, dsn: str) -> None:
        self._lock = threading.Lock()
        self._pool = ConnectionPool(dsn, min_size=1, max_size=4, open=True, name="gewu-biz")
        with self._pool.connection() as conn:
            conn.execute(_SCHEMA)
            conn.cursor().executemany(
                "INSERT INTO venues (id, name, kind, capacity) VALUES (%s, %s, %s, %s)"
                " ON CONFLICT (id) DO NOTHING",
                _VENUE_SEED,
            )

    def close(self) -> None:
        self._pool.close()

    def wipe(self) -> None:
        """测试专用：清运行数据 + 序列归零（保证 VE-0001/LV-0001 可断言）。

        与 reset()（PARITY 端点）的区别：SQLite AUTOINCREMENT 在 DELETE 后
        计数不归零，reset 沿用该语义；wipe 是测试的干净库语义。
        """
        with self._lock, self._pool.connection() as conn:
            conn.execute("DELETE FROM bookings")
            conn.execute("DELETE FROM leave_tickets")
            conn.execute("SELECT setval('bookings_id_seq', 1, false)")
            conn.execute("SELECT setval('leave_tickets_id_seq', 1, false)")

    # ---------- 场馆 ----------

    def list_venues(self) -> list[dict]:
        """全部场馆（按 id 升序）。"""
        with self._pool.connection() as conn:
            rows = conn.execute(
                "SELECT id, name, kind, capacity FROM venues ORDER BY id"
            ).fetchall()
        return [{"venue_id": r[0], "name": r[1], "kind": r[2], "capacity": int(r[3])} for r in rows]

    def venue_by_name(self, text: str) -> dict | None:
        """按名称子串匹配场馆（agent 侧解析用户口语用）。"""
        for v in self.list_venues():
            if v["name"] in text:
                return v
        return None

    def remaining(self, venue_id: str, date_: str) -> dict[str, int]:
        """场馆某日各时段余量；场馆不存在返回空 dict。"""
        with self._pool.connection() as conn:
            row = conn.execute("SELECT capacity FROM venues WHERE id = %s", (venue_id,)).fetchone()
            if row is None:
                return {}
            capacity = int(row[0])
            out = {s: capacity for s in SLOTS}
            used_rows = conn.execute(
                "SELECT slot, COUNT(*) AS used FROM bookings"
                " WHERE venue_id = %s AND date = %s AND status = '有效' GROUP BY slot",
                (venue_id, date_),
            ).fetchall()
        for r in used_rows:
            out[r[0]] = max(capacity - int(r[1]), 0)
        return out

    def book_venue(self, venue_id: str, date_: str, slot: str, purpose: str, user: str) -> Result:
        """预约场馆（校验顺序与返回值见 PARITY §8.1；全程持锁单事务）。"""
        with self._lock, self._pool.connection() as conn:
            row = conn.execute(
                "SELECT name, capacity FROM venues WHERE id = %s", (venue_id,)
            ).fetchone()
            if row is None:
                return fail("invalid", "", "场馆不存在")
            name, capacity = row[0], int(row[1])
            if slot not in SLOTS_SET:
                return fail("invalid", "slot", "时段不合法")
            if date_ < today_iso():
                return fail("invalid", "date", "不能预约过去的日期")
            per_day = int(
                conn.execute(
                    "SELECT COUNT(*) FROM bookings"
                    " WHERE \"user\" = %s AND date = %s AND status = '有效'",
                    (user, date_),
                ).fetchone()[0]
            )
            if per_day >= 2:
                return fail("quota", "", "每人每天最多预约 2 个时段")
            used = dict(
                conn.execute(
                    "SELECT slot, COUNT(*) FROM bookings"
                    " WHERE venue_id = %s AND date = %s AND status = '有效' GROUP BY slot",
                    (venue_id, date_),
                ).fetchall()
            )
            rem = {s: max(capacity - int(used.get(s, 0)), 0) for s in SLOTS}
            if rem.get(slot, 0) <= 0:
                alts = [s for s in SLOTS if rem.get(s, 0) > 0]  # 固定时段序，输出稳定
                return Result(
                    err="conflict",
                    field="slot",
                    message=f"{name} {date_} 的 {slot} 已约满",
                    alternatives=alts,
                )
            new_id = int(
                conn.execute(
                    "INSERT INTO bookings"
                    ' (venue_id, date, slot, purpose, "user", created_at)'
                    " VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
                    (venue_id, date_, slot, purpose, user, now_cn_iso()),
                ).fetchone()[0]
            )
        return Result(
            ok=True,
            receipt=receipt_id("VE", new_id),
            message=f"已预约 {name} {date_} {slot}",
        )

    def cancel_booking(self, booking_id: str, user: str) -> Result:
        """取消预约（仅本人、仅有效状态）。"""
        num = receipt_num(booking_id)
        if num is None:
            return fail("not_found", "", "预约记录不存在或已取消")
        with self._lock, self._pool.connection() as conn:
            row = conn.execute(
                'SELECT id, "user", status FROM bookings WHERE id = %s', (num,)
            ).fetchone()
            if row is None or row[2] != "有效":
                return fail("not_found", "", "预约记录不存在或已取消")
            if row[1] != user:
                return fail("permission", "", "只能取消本人的预约")
            conn.execute("UPDATE bookings SET status = '已取消' WHERE id = %s", (row[0],))
        return ok_msg(f"预约 {booking_id} 已取消")

    def my_bookings(self, user: str) -> list[dict]:
        """本人当前有效预约（按 date, slot 排序）。"""
        with self._pool.connection() as conn:
            rows = conn.execute(
                "SELECT b.id, v.name, b.date, b.slot, b.purpose FROM bookings b"
                " JOIN venues v ON v.id = b.venue_id"
                " WHERE b.\"user\" = %s AND b.status = '有效' ORDER BY b.date, b.slot",
                (user,),
            ).fetchall()
        return [
            {
                "booking_id": receipt_id("VE", int(r[0])),
                "venue": r[1],
                "date": r[2],
                "slot": r[3],
                "purpose": r[4],
            }
            for r in rows
        ]

    # ---------- 请假 ----------

    def submit_leave(self, user: str, leave_type: str, start: str, end: str, reason: str) -> Result:
        days = leave_days(start, end)
        if days < 1:
            return fail("invalid", "end_date", "结束日期不能早于开始日期")
        if start < today_iso():
            return fail("invalid", "start_date", "开始日期不能是过去")
        with self._lock, self._pool.connection() as conn:
            new_id = int(
                conn.execute(
                    "INSERT INTO leave_tickets"
                    ' ("user", leave_type, start_date, end_date, days, reason,'
                    " approver_level, created_at)"
                    " VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
                    (user, leave_type, start, end, days, reason, approver_of(days), now_cn_iso()),
                ).fetchone()[0]
            )
        return Result(
            ok=True,
            receipt=receipt_id("LV", new_id),
            days=days,
            approver=approver_of(days),
            message=f"请假申请已提交（{days} 天），按学校规定将由{approver_of(days)}审批",
        )

    def leave_status(self, ticket_id: str, user: str) -> Result:
        """按单号查询本人请假单。"""
        num = receipt_num(ticket_id)
        if num is None:
            return fail("not_found", "", "请假单不存在")
        with self._pool.connection() as conn:
            row = conn.execute(
                'SELECT id, "user", leave_type, start_date, end_date, days,'
                " approver_level, status FROM leave_tickets WHERE id = %s",
                (num,),
            ).fetchone()
        if row is None:
            return fail("not_found", "", "请假单不存在")
        if row[1] != user:
            return fail("permission", "", "只能查询本人的请假单")
        rid = receipt_id("LV", int(row[0]))
        return Result(
            ok=True,
            days=int(row[5]),
            approver=row[6],
            receipt=rid,
            message=f"{rid} {row[2]} {row[3]}~{row[4]}（{row[5]} 天，{row[6]}审批，{row[7]}）",
        )

    def pending_leaves(self) -> list[dict]:
        """全部待审批请假单（按 id 升序）。"""
        with self._pool.connection() as conn:
            rows = conn.execute(
                'SELECT id, "user", leave_type, start_date, end_date, days,'
                " approver_level FROM leave_tickets WHERE status = '待审批' ORDER BY id"
            ).fetchall()
        return [
            {
                "ticket": receipt_id("LV", int(r[0])),
                "user": r[1],
                "leave_type": r[2],
                "start": r[3],
                "end": r[4],
                "days": int(r[5]),
                "approver": r[6],
            }
            for r in rows
        ]

    def approve_leave(self, ticket_id: str) -> Result:
        """批准请假单。"""
        num = receipt_num(ticket_id)
        if num is None:
            return fail("not_found", "", "请假单不存在")
        with self._lock, self._pool.connection() as conn:
            row = conn.execute(
                "SELECT id, status FROM leave_tickets WHERE id = %s", (num,)
            ).fetchone()
            if row is None:
                return fail("not_found", "", "请假单不存在")
            if row[1] != "待审批":
                return fail("invalid", "", "该请假单已处理")
            conn.execute("UPDATE leave_tickets SET status = '已通过' WHERE id = %s", (row[0],))
        return ok_msg(f"请假单 {ticket_id} 已通过")

    # ---------- 调试 / 评测 ----------

    def reset(self) -> None:
        """清空运行数据（评测与演示用，场馆表保留；单号计数沿用 SQLite 语义不归零）。"""
        with self._lock, self._pool.connection() as conn:
            conn.execute("DELETE FROM bookings")
            conn.execute("DELETE FROM leave_tickets")

    def all_bookings(self) -> list[BookingFull]:
        with self._pool.connection() as conn:
            rows = conn.execute(
                'SELECT b.id, v.name, b.date, b.slot, b."user" FROM bookings b'
                " JOIN venues v ON v.id = b.venue_id"
                " WHERE b.status = '有效' ORDER BY b.id"
            ).fetchall()
        return [
            BookingFull(
                booking_id=receipt_id("VE", int(r[0])),
                venue=r[1],
                date=r[2],
                slot=r[3],
                user=r[4],
            )
            for r in rows
        ]

    def all_tickets(self) -> list[TicketView]:
        with self._pool.connection() as conn:
            rows = conn.execute(
                'SELECT id, "user", leave_type, start_date, end_date, days,'
                " approver_level, status FROM leave_tickets ORDER BY id"
            ).fetchall()
        return [
            TicketView(
                ticket=receipt_id("LV", int(r[0])),
                user=r[1],
                leave_type=r[2],
                start=r[3],
                end=r[4],
                days=int(r[5]),
                approver=r[6],
                status=r[7],
            )
            for r in rows
        ]
