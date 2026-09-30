"""mock 校内业务库（SQLite）：场馆预约与请假审批（移植自 Go internal/business）。

真实学校里这是独立的业务后端；本项目内用同进程模块模拟，agent 只能通过
agent 工具层访问它，模块边界与生产架构一致。业务规则与语料保持一致：
请假 1—3 天辅导员批、3 天以上 7 天以内学院批、超过 7 天教务处批。
"""

from __future__ import annotations

import dataclasses
import sqlite3
import threading
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

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
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    venue_id TEXT NOT NULL,
    date TEXT NOT NULL,
    slot TEXT NOT NULL,
    purpose TEXT NOT NULL DEFAULT '',
    user TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT '有效',
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS leave_tickets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user TEXT NOT NULL,
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


def num_part(receipt: str) -> str:
    """单号 → 数字部分（'VE-0001' → '0001'，SQLite 整数亲和等值匹配 id）。"""
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
    """业务库连接：单连接 + 锁（操作全程持锁，对齐 Go 版单连接语义）。"""

    def __init__(self, path: Path) -> None:
        self._lock = threading.Lock()
        self._path = path
        self._conn: sqlite3.Connection | None = None
        self._init_schema()

    def _open(self) -> sqlite3.Connection:
        """取连接（须持锁调用）。"""
        if self._conn is None:
            self._conn = sqlite3.connect(self._path, check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
        return self._conn

    def _init_schema(self) -> None:
        with self._lock:
            conn = self._open()
            conn.executescript(_SCHEMA)
            conn.commit()
            n = conn.execute("SELECT COUNT(*) FROM venues").fetchone()[0]
            if n == 0:
                conn.executemany(
                    "INSERT INTO venues (id, name, kind, capacity) VALUES (?, ?, ?, ?)",
                    _VENUE_SEED,
                )
                conn.commit()

    # ---------- 场馆 ----------

    def list_venues(self) -> list[dict]:
        """全部场馆（按 id 升序）。"""
        with self._lock:
            return self._list_venues_locked()

    def _list_venues_locked(self) -> list[dict]:
        rows = (
            self._open()
            .execute("SELECT id, name, kind, capacity FROM venues ORDER BY id")
            .fetchall()
        )
        return [
            {"venue_id": r["id"], "name": r["name"], "kind": r["kind"], "capacity": r["capacity"]}
            for r in rows
        ]

    def venue_by_name(self, text: str) -> dict | None:
        """按名称子串匹配场馆（agent 侧解析用户口语用）。"""
        with self._lock:
            for v in self._list_venues_locked():
                if v["name"] in text:
                    return v
        return None

    def remaining(self, venue_id: str, date_: str) -> dict[str, int]:
        """场馆某日各时段余量；场馆不存在返回空 dict。"""
        with self._lock:
            return self._remaining_locked(venue_id, date_)

    def _remaining_locked(self, venue_id: str, date_: str) -> dict[str, int]:
        if True:
            row = (
                self._open()
                .execute("SELECT capacity FROM venues WHERE id = ?", (venue_id,))
                .fetchone()
            )
            if row is None:
                return {}
            capacity = row["capacity"]
            out = {s: capacity for s in SLOTS}
            used_rows = (
                self._open()
                .execute(
                    "SELECT slot, COUNT(*) AS used FROM bookings "
                    "WHERE venue_id = ? AND date = ? AND status = '有效' GROUP BY slot",
                    (venue_id, date_),
                )
                .fetchall()
            )
        for r in used_rows:
            out[r["slot"]] = max(capacity - r["used"], 0)
        return out

    def book_venue(self, venue_id: str, date_: str, slot: str, purpose: str, user: str) -> Result:
        """预约场馆（校验顺序与返回值见 PARITY §8.1）。"""
        with self._lock:
            row = (
                self._open().execute("SELECT name FROM venues WHERE id = ?", (venue_id,)).fetchone()
            )
            if row is None:
                return fail("invalid", "", "场馆不存在")
            name = row["name"]
            if slot not in SLOTS_SET:
                return fail("invalid", "slot", "时段不合法")
            if date_ < today_iso():
                return fail("invalid", "date", "不能预约过去的日期")
            per_day = (
                self._open()
                .execute(
                    "SELECT COUNT(*) FROM bookings WHERE user = ? AND date = ? AND status = '有效'",
                    (user, date_),
                )
                .fetchone()[0]
            )
            if per_day >= 2:
                return fail("quota", "", "每人每天最多预约 2 个时段")
            rem = self._remaining_locked(venue_id, date_)
            if rem.get(slot, 0) <= 0:
                alts = [s for s in SLOTS if rem.get(s, 0) > 0]  # 固定时段序，输出稳定
                return Result(
                    err="conflict",
                    field="slot",
                    message=f"{name} {date_} 的 {slot} 已约满",
                    alternatives=alts,
                )
            cur = self._open().execute(
                "INSERT INTO bookings (venue_id, date, slot, purpose, user, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (venue_id, date_, slot, purpose, user, now_cn_iso()),
            )
            self._conn.commit()
        return Result(
            ok=True,
            receipt=receipt_id("VE", cur.lastrowid),
            message=f"已预约 {name} {date_} {slot}",
        )

    def cancel_booking(self, booking_id: str, user: str) -> Result:
        """取消预约（仅本人、仅有效状态）。"""
        with self._lock:
            row = (
                self._open()
                .execute(
                    "SELECT id, user, status FROM bookings WHERE id = ?", (num_part(booking_id),)
                )
                .fetchone()
            )
            if row is None or row["status"] != "有效":
                return fail("not_found", "", "预约记录不存在或已取消")
            if row["user"] != user:
                return fail("permission", "", "只能取消本人的预约")
            self._open().execute("UPDATE bookings SET status = '已取消' WHERE id = ?", (row["id"],))
            self._conn.commit()
        return ok_msg(f"预约 {booking_id} 已取消")

    def my_bookings(self, user: str) -> list[dict]:
        """本人当前有效预约（按 date, slot 排序）。"""
        with self._lock:
            rows = (
                self._open()
                .execute(
                    "SELECT b.id, v.name, b.date, b.slot, b.purpose FROM bookings b "
                    "JOIN venues v ON v.id = b.venue_id "
                    "WHERE b.user = ? AND b.status = '有效' ORDER BY b.date, b.slot",
                    (user,),
                )
                .fetchall()
            )
        return [
            {
                "booking_id": receipt_id("VE", r["id"]),
                "venue": r["name"],
                "date": r["date"],
                "slot": r["slot"],
                "purpose": r["purpose"],
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
        with self._lock:
            cur = self._open().execute(
                "INSERT INTO leave_tickets (user, leave_type, start_date, end_date, days, reason, approver_level, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (user, leave_type, start, end, days, reason, approver_of(days), now_cn_iso()),
            )
            self._conn.commit()
        return Result(
            ok=True,
            receipt=receipt_id("LV", cur.lastrowid),
            days=days,
            approver=approver_of(days),
            message=f"请假申请已提交（{days} 天），按学校规定将由{approver_of(days)}审批",
        )

    def leave_status(self, ticket_id: str, user: str) -> Result:
        """按单号查询本人请假单。"""
        with self._lock:
            row = (
                self._open()
                .execute(
                    "SELECT id, user, leave_type, start_date, end_date, days, approver_level, status "
                    "FROM leave_tickets WHERE id = ?",
                    (num_part(ticket_id),),
                )
                .fetchone()
            )
        if row is None:
            return fail("not_found", "", "请假单不存在")
        if row["user"] != user:
            return fail("permission", "", "只能查询本人的请假单")
        rid = receipt_id("LV", row["id"])
        return Result(
            ok=True,
            days=row["days"],
            approver=row["approver_level"],
            receipt=rid,
            message=(
                f"{rid} {row['leave_type']} {row['start_date']}~{row['end_date']}"
                f"（{row['days']} 天，{row['approver_level']}审批，{row['status']}）"
            ),
        )

    def pending_leaves(self) -> list[dict]:
        """全部待审批请假单（按 id 升序）。"""
        with self._lock:
            rows = (
                self._open()
                .execute(
                    "SELECT id, user, leave_type, start_date, end_date, days, approver_level "
                    "FROM leave_tickets WHERE status = '待审批' ORDER BY id"
                )
                .fetchall()
            )
        return [
            {
                "ticket": receipt_id("LV", r["id"]),
                "user": r["user"],
                "leave_type": r["leave_type"],
                "start": r["start_date"],
                "end": r["end_date"],
                "days": r["days"],
                "approver": r["approver_level"],
            }
            for r in rows
        ]

    def approve_leave(self, ticket_id: str) -> Result:
        """批准请假单。"""
        with self._lock:
            row = (
                self._open()
                .execute(
                    "SELECT id, status FROM leave_tickets WHERE id = ?", (num_part(ticket_id),)
                )
                .fetchone()
            )
            if row is None:
                return fail("not_found", "", "请假单不存在")
            if row["status"] != "待审批":
                return fail("invalid", "", "该请假单已处理")
            self._open().execute(
                "UPDATE leave_tickets SET status = '已通过' WHERE id = ?", (row["id"],)
            )
            self._conn.commit()
        return ok_msg(f"请假单 {ticket_id} 已通过")

    # ---------- 调试 / 评测 ----------

    def reset(self) -> None:
        """清空运行数据（评测与演示用，场馆表保留）。"""
        with self._lock:
            self._open().execute("DELETE FROM bookings")
            self._open().execute("DELETE FROM leave_tickets")
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None

    def all_bookings(self) -> list[BookingFull]:
        sql = (
            "SELECT b.id, v.name, b.date, b.slot, b.user FROM bookings b "
            "JOIN venues v ON v.id = b.venue_id WHERE b.status = '有效' ORDER BY b.id"
        )
        with self._lock:
            rows = self._open().execute(sql).fetchall()
        return [
            BookingFull(
                booking_id=receipt_id("VE", r["id"]),
                venue=r["name"],
                date=r["date"],
                slot=r["slot"],
                user=r["user"],
            )
            for r in rows
        ]

    def all_tickets(self) -> list[TicketView]:
        sql = (
            "SELECT id, user, leave_type, start_date, end_date, days, "
            "approver_level, status FROM leave_tickets ORDER BY id"
        )
        with self._lock:
            rows = self._open().execute(sql).fetchall()
        return [
            TicketView(
                ticket=receipt_id("LV", r["id"]),
                user=r["user"],
                leave_type=r["leave_type"],
                start=r["start_date"],
                end=r["end_date"],
                days=r["days"],
                approver=r["approver_level"],
                status=r["status"],
            )
            for r in rows
        ]
