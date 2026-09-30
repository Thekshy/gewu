"""business 写路径测试：预约校验序/每日限额/冲突可选项/请假审批层级/权限。"""

from __future__ import annotations

from pathlib import Path

from gewu.business.db import Business, leave_days


def _biz(tmp_path: Path) -> Business:
    return Business(tmp_path / "b.db")


def test_book_venue_success_and_receipt(tmp_path: Path):
    b = _biz(tmp_path)
    r = b.book_venue("venue-badminton", "2099-01-01", "19:00-21:00", "训练", "demo-student")
    assert r.ok and r.receipt == "VE-0001"
    assert "已预约 羽毛球馆" in r.message
    bookings = b.my_bookings("demo-student")
    assert len(bookings) == 1 and bookings[0]["slot"] == "19:00-21:00"


def test_book_venue_validation_order(tmp_path: Path):
    b = _biz(tmp_path)
    assert b.book_venue("no-such", "2099-01-01", "19:00-21:00", "", "u").err == "invalid"
    assert b.book_venue("venue-badminton", "2099-01-01", "25:00", "", "u").message == "时段不合法"
    r = b.book_venue("venue-badminton", "2000-01-01", "19:00-21:00", "", "u")
    assert r.field == "date" and "过去" in r.message


def test_book_venue_daily_quota(tmp_path: Path):
    b = _biz(tmp_path)
    assert b.book_venue("venue-badminton", "2099-01-01", "08:00-10:00", "", "u").ok
    assert b.book_venue("venue-basketball", "2099-01-01", "10:00-12:00", "", "u").ok
    r = b.book_venue("venue-room301", "2099-01-01", "14:00-16:00", "", "u")
    assert r.err == "quota" and "2 个时段" in r.message


def test_book_venue_conflict_with_alternatives(tmp_path: Path):
    b = _biz(tmp_path)
    # 研讨间301 容量 1：占满 19:00-21:00 后再约同一时段 → conflict + 可选项
    assert b.book_venue("venue-room301", "2099-01-01", "19:00-21:00", "", "alice").ok
    r = b.book_venue("venue-room301", "2099-01-01", "19:00-21:00", "", "bob")
    assert r.err == "conflict" and r.field == "slot"
    assert "已约满" in r.message
    assert "19:00-21:00" not in r.alternatives
    assert "08:00-10:00" in r.alternatives  # 固定时段序输出稳定


def test_cancel_booking_owner_only(tmp_path: Path):
    b = _biz(tmp_path)
    b.book_venue("venue-badminton", "2099-01-01", "19:00-21:00", "", "alice")
    r = b.cancel_booking("VE-0001", "bob")
    assert r.err == "permission" and "本人" in r.message
    assert b.cancel_booking("VE-0001", "alice").ok
    assert b.cancel_booking("VE-0001", "alice").err == "not_found"  # 已取消不可再取消


def test_submit_leave_approver_levels(tmp_path: Path):
    b = _biz(tmp_path)
    r = b.submit_leave("demo-student", "事假", "2099-01-01", "2099-01-03", "私事")
    assert r.ok and r.days == 3 and r.approver == "辅导员" and r.receipt == "LV-0001"
    r = b.submit_leave("demo-student", "事假", "2099-02-01", "2099-02-10", "私事")
    assert r.days == 10 and r.approver == "教务处"
    r = b.submit_leave("demo-student", "事假", "2099-03-01", "2099-03-07", "私事")
    assert r.days == 7 and r.approver == "学院"


def test_submit_leave_validation(tmp_path: Path):
    b = _biz(tmp_path)
    r = b.submit_leave("u", "事假", "2099-01-05", "2099-01-01", "x")
    assert r.err == "invalid" and r.field == "end_date"
    r = b.submit_leave("u", "事假", "2000-01-01", "2000-01-02", "x")
    assert r.field == "start_date" and "过去" in r.message


def test_leave_status_and_approve(tmp_path: Path):
    b = _biz(tmp_path)
    b.submit_leave("alice", "事假", "2099-01-01", "2099-01-02", "私事")
    r = b.leave_status("LV-0001", "bob")
    assert r.err == "permission"
    r = b.leave_status("LV-0001", "alice")
    assert r.ok and "待审批" in r.message
    assert b.approve_leave("LV-0001").ok
    assert b.approve_leave("LV-0001").err == "invalid"
    assert [t.status for t in b.all_tickets()] == ["已通过"]


def test_leave_days():
    assert leave_days("2099-01-01", "2099-01-03") == 3  # 含首尾
    assert leave_days("2099-01-05", "2099-01-01") == -1
    assert leave_days("bad", "2099-01-01") == -1


def test_remaining_and_reset(tmp_path: Path):
    b = _biz(tmp_path)
    b.book_venue("venue-basketball", "2099-01-01", "08:00-10:00", "", "u")
    rem = b.remaining("venue-basketball", "2099-01-01")
    assert rem["08:00-10:00"] == 0  # 容量 1 已占满
    assert rem["19:00-21:00"] == 1
    b.reset()
    assert b.my_bookings("u") == []
