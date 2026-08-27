import datetime as dt

from app.business.service import Business
from app.dates import today

TODAY = today()


def make(tmp_path):
    return Business(tmp_path / "biz.db")


def test_book_conflict_returns_alternatives(tmp_path):
    b = make(tmp_path)
    future = (TODAY + dt.timedelta(days=1)).isoformat()
    ok1 = b.book_venue("venue-room301", future, "10:00-12:00", "自习", "alice")
    assert ok1["ok"]
    clash = b.book_venue("venue-room301", future, "10:00-12:00", "讨论", "bob")
    assert not clash["ok"] and clash["error"] == "conflict"
    assert "14:00-16:00" in clash["alternatives"]


def test_capacity_allows_multiple_until_full(tmp_path):
    b = make(tmp_path)
    future = (TODAY + dt.timedelta(days=2)).isoformat()
    assert b.book_venue("venue-badminton", future, "08:00-10:00", "a", "u1")["ok"]
    assert b.book_venue("venue-badminton", future, "08:00-10:00", "b", "u2")["ok"]
    third = b.book_venue("venue-badminton", future, "08:00-10:00", "c", "u3")
    assert not third["ok"] and third["error"] == "conflict"


def test_daily_quota_per_user(tmp_path):
    b = make(tmp_path)
    future = (TODAY + dt.timedelta(days=3)).isoformat()
    assert b.book_venue("venue-badminton", future, "08:00-10:00", "", "u1")["ok"]
    assert b.book_venue("venue-badminton", future, "10:00-12:00", "", "u1")["ok"]
    third = b.book_venue("venue-badminton", future, "14:00-16:00", "", "u1")
    assert not third["ok"] and third["error"] == "quota"


def test_past_date_rejected(tmp_path):
    b = make(tmp_path)
    past = (TODAY - dt.timedelta(days=1)).isoformat()
    res = b.book_venue("venue-badminton", past, "08:00-10:00", "", "u1")
    assert not res["ok"] and res["error"] == "invalid"


def test_leave_approver_levels(tmp_path):
    b = make(tmp_path)
    future = (TODAY + dt.timedelta(days=10)).isoformat()
    end2 = (TODAY + dt.timedelta(days=11)).isoformat()
    assert b.submit_leave("u1", "事假", future, end2, "家事")["approver"] == "辅导员"
    end5 = (TODAY + dt.timedelta(days=14)).isoformat()
    assert b.submit_leave("u2", "事假", future, end5, "家事")["approver"] == "学院"
    end10 = (TODAY + dt.timedelta(days=19)).isoformat()
    assert b.submit_leave("u3", "事假", future, end10, "家事")["approver"] == "教务处"


def test_cancel_only_by_owner(tmp_path):
    b = make(tmp_path)
    future = (TODAY + dt.timedelta(days=1)).isoformat()
    booked = b.book_venue("venue-room301", future, "08:00-10:00", "", "alice")
    receipt = booked["receipt"]
    denied = b.cancel_booking(receipt, "bob")
    assert not denied["ok"] and denied["error"] == "permission"
    assert b.cancel_booking(receipt, "alice")["ok"]
    assert not b.cancel_booking(receipt, "alice")["ok"]  # 已取消
