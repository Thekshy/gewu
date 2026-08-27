"""mock 校内业务系统：场馆预约与请假审批。

真实学校里这是独立的业务后端；本项目内用同进程模块模拟，agent 只能通过
tools.py 的工具层访问它，模块边界与生产架构一致。所有 SQL 均为单行静态
语句 + 参数绑定。

业务规则与语料保持一致（data/corpus/0010-leave.md）：
请假 1—3 天辅导员批、3 天以上 7 天以内学院批、超过 7 天教务处批。
"""

from __future__ import annotations

import datetime as dt
import sqlite3
from pathlib import Path

from ..dates import now_cn, today_iso

SLOTS = ("08:00-10:00", "10:00-12:00", "14:00-16:00", "16:00-18:00", "19:00-21:00")

SEED_VENUES = [
    ("venue-badminton", "羽毛球馆", "体育场馆", 2),
    ("venue-basketball", "篮球场", "体育场馆", 1),
    ("venue-room301", "研讨间301", "图书馆研讨间", 1),
    ("venue-room302", "研讨间302", "图书馆研讨间", 1),
]

SCHEMA = """
CREATE TABLE IF NOT EXISTS venues (
    id TEXT PRIMARY KEY, name TEXT NOT NULL, kind TEXT NOT NULL, capacity INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS bookings (
    id INTEGER PRIMARY KEY AUTOINCREMENT, venue_id TEXT NOT NULL, date TEXT NOT NULL,
    slot TEXT NOT NULL, purpose TEXT NOT NULL DEFAULT '', user TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT '有效', created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS leave_tickets (
    id INTEGER PRIMARY KEY AUTOINCREMENT, user TEXT NOT NULL, leave_type TEXT NOT NULL,
    start_date TEXT NOT NULL, end_date TEXT NOT NULL, days INTEGER NOT NULL,
    reason TEXT NOT NULL, approver_level TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT '待审批', created_at TEXT NOT NULL
);
"""


def approver_of(days: int) -> str:
    if days <= 3:
        return "辅导员"
    if days <= 7:
        return "学院"
    return "教务处"


class Business:
    def __init__(self, path: Path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.executescript(SCHEMA)
        self._seed()

    def _seed(self) -> None:
        (n,) = self.conn.execute("SELECT COUNT(*) FROM venues").fetchone()
        if n == 0:
            with self.conn:
                for v in SEED_VENUES:
                    self.conn.execute(
                        "INSERT INTO venues (id, name, kind, capacity) VALUES (?, ?, ?, ?)", v
                    )

    # ---------- 场馆 ----------

    def list_venues(self) -> list[dict]:
        rows = self.conn.execute("SELECT id, name, kind, capacity FROM venues ORDER BY id").fetchall()
        return [{"venue_id": r[0], "name": r[1], "kind": r[2], "capacity": r[3]} for r in rows]

    def venue_by_name(self, text: str) -> dict | None:
        """按名称子串匹配场馆（agent 侧解析用户口语用）。"""
        for v in self.list_venues():
            if v["name"] in text:
                return v
        return None

    def remaining(self, venue_id: str, date: str) -> dict[str, int]:
        row = self.conn.execute("SELECT capacity FROM venues WHERE id = ?", (venue_id,)).fetchone()
        if row is None:
            return {}
        out = {slot: row[0] for slot in SLOTS}
        used_rows = self.conn.execute("SELECT slot, COUNT(*) FROM bookings WHERE venue_id = ? AND date = ? AND status = '有效' GROUP BY slot", (venue_id, date)).fetchall()
        for slot, used in used_rows:
            out[slot] = max(0, row[0] - used)
        return out

    def book_venue(self, venue_id: str, date: str, slot: str, purpose: str, user: str) -> dict:
        row = self.conn.execute("SELECT name FROM venues WHERE id = ?", (venue_id,)).fetchone()
        if row is None:
            return {"ok": False, "error": "invalid", "message": "场馆不存在"}
        name = row[0]
        if slot not in SLOTS:
            return {"ok": False, "error": "invalid", "field": "slot", "message": "时段不合法"}
        if date < today_iso():
            return {"ok": False, "error": "invalid", "field": "date", "message": "不能预约过去的日期"}
        per_day = self.conn.execute("SELECT COUNT(*) FROM bookings WHERE user = ? AND date = ? AND status = '有效'", (user, date)).fetchone()[0]
        if per_day >= 2:
            return {"ok": False, "error": "quota", "message": "每人每天最多预约 2 个时段"}
        rem = self.remaining(venue_id, date)
        if rem.get(slot, 0) <= 0:
            alts = [s for s, left in rem.items() if left > 0]
            return {
                "ok": False,
                "error": "conflict",
                "field": "slot",
                "message": f"{name} {date} 的 {slot} 已约满",
                "alternatives": alts,
            }
        with self.conn:
            cur = self.conn.execute("INSERT INTO bookings (venue_id, date, slot, purpose, user, created_at) VALUES (?, ?, ?, ?, ?, ?)", (venue_id, date, slot, purpose, user, now_cn().isoformat(timespec="seconds")))
        return {"ok": True, "receipt": f"VE-{cur.lastrowid:04d}", "message": f"已预约 {name} {date} {slot}"}

    def cancel_booking(self, booking_id: str, user: str) -> dict:
        row = self.conn.execute("SELECT id, user, status FROM bookings WHERE id = ?", (self._num(booking_id),)).fetchone()
        if row is None or row[2] != "有效":
            return {"ok": False, "error": "not_found", "message": "预约记录不存在或已取消"}
        if row[1] != user:
            return {"ok": False, "error": "permission", "message": "只能取消本人的预约"}
        with self.conn:
            self.conn.execute("UPDATE bookings SET status = '已取消' WHERE id = ?", (row[0],))
        return {"ok": True, "message": f"预约 {booking_id} 已取消"}

    @staticmethod
    def _num(id_text: str) -> int:
        digits = "".join(ch for ch in str(id_text) if ch.isdigit())
        return int(digits) if digits else -1

    def my_bookings(self, user: str) -> list[dict]:
        rows = self.conn.execute("SELECT b.id, v.name, b.date, b.slot, b.purpose FROM bookings b JOIN venues v ON v.id = b.venue_id WHERE b.user = ? AND b.status = '有效' ORDER BY b.date, b.slot", (user,)).fetchall()
        return [
            {"booking_id": f"VE-{r[0]:04d}", "venue": r[1], "date": r[2], "slot": r[3], "purpose": r[4]}
            for r in rows
        ]

    # ---------- 请假 ----------

    @staticmethod
    def leave_days(start: str, end: str) -> int:
        try:
            s = dt.date.fromisoformat(start)
            e = dt.date.fromisoformat(end)
        except ValueError:
            return -1
        return (e - s).days + 1 if e >= s else -1

    def submit_leave(self, user: str, leave_type: str, start: str, end: str, reason: str) -> dict:
        days = self.leave_days(start, end)
        if days < 1:
            return {"ok": False, "error": "invalid", "field": "end_date", "message": "结束日期不能早于开始日期"}
        if start < today_iso():
            return {"ok": False, "error": "invalid", "field": "start_date", "message": "开始日期不能是过去"}
        with self.conn:
            cur = self.conn.execute("INSERT INTO leave_tickets (user, leave_type, start_date, end_date, days, reason, approver_level, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (user, leave_type, start, end, days, reason, approver_of(days), now_cn().isoformat(timespec="seconds")))
        return {
            "ok": True,
            "receipt": f"LV-{cur.lastrowid:04d}",
            "days": days,
            "approver": approver_of(days),
            "message": f"请假申请已提交（{days} 天），按学校规定将由{approver_of(days)}审批",
        }

    def leave_status(self, ticket_id: str, user: str) -> dict:
        row = self.conn.execute("SELECT id, user, leave_type, start_date, end_date, days, approver_level, status FROM leave_tickets WHERE id = ?", (self._num(ticket_id),)).fetchone()
        if row is None:
            return {"ok": False, "error": "not_found", "message": "请假单不存在"}
        if row[1] != user:
            return {"ok": False, "error": "permission", "message": "只能查询本人的请假单"}
        return {
            "ok": True,
            "ticket": f"LV-{row[0]:04d}", "leave_type": row[2], "start": row[3], "end": row[4],
            "days": row[5], "approver": row[6], "status": row[7],
        }

    def pending_leaves(self) -> list[dict]:
        rows = self.conn.execute("SELECT id, user, leave_type, start_date, end_date, days, approver_level FROM leave_tickets WHERE status = '待审批' ORDER BY id").fetchall()
        return [
            {"ticket": f"LV-{r[0]:04d}", "user": r[1], "leave_type": r[2], "start": r[3],
             "end": r[4], "days": r[5], "approver": r[6]}
            for r in rows
        ]

    def approve_leave(self, ticket_id: str) -> dict:
        row = self.conn.execute("SELECT id, status FROM leave_tickets WHERE id = ?", (self._num(ticket_id),)).fetchone()
        if row is None:
            return {"ok": False, "error": "not_found", "message": "请假单不存在"}
        if row[1] != "待审批":
            return {"ok": False, "error": "invalid", "message": "该请假单已处理"}
        with self.conn:
            self.conn.execute("UPDATE leave_tickets SET status = '已通过' WHERE id = ?", (row[0],))
        return {"ok": True, "message": f"请假单 {ticket_id} 已通过"}

    # ---------- 调试 / 评测 ----------

    def all_bookings(self) -> list[dict]:
        rows = self.conn.execute("SELECT b.id, v.name, b.date, b.slot, b.user FROM bookings b JOIN venues v ON v.id = b.venue_id WHERE b.status = '有效' ORDER BY b.id").fetchall()
        return [{"booking_id": f"VE-{r[0]:04d}", "venue": r[1], "date": r[2], "slot": r[3], "user": r[4]} for r in rows]

    def all_tickets(self) -> list[dict]:
        rows = self.conn.execute("SELECT id, user, leave_type, start_date, end_date, days, approver_level, status FROM leave_tickets ORDER BY id").fetchall()
        return [
            {"ticket": f"LV-{r[0]:04d}", "user": r[1], "leave_type": r[2], "start": r[3], "end": r[4],
             "days": r[5], "approver": r[6], "status": r[7]}
            for r in rows
        ]

    def reset(self) -> None:
        """清空运行数据（评测与演示用）。"""
        with self.conn:
            self.conn.execute("DELETE FROM bookings")
            self.conn.execute("DELETE FROM leave_tickets")
