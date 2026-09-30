"""mock 校内业务库（SQLite）：P14-0 仅 overview 只读路径。

表结构与种子场馆对齐 Go internal/business；venues 为演示种子（空表自动补种），
bookings/leave_tickets 为运行数据。写路径（预约/请假/确认流）在 P14-6 移植。
"""

from __future__ import annotations

import sqlite3
import threading
from dataclasses import dataclass
from pathlib import Path

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
