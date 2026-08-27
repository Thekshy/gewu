import datetime as dt

from app.dates import parse, parse_all

# 2026-08-27 是周四
TODAY = dt.date(2026, 8, 27)


def test_full_date():
    assert parse("2026-09-02", TODAY) == dt.date(2026, 9, 2)
    assert parse("2026/9/2", TODAY) == dt.date(2026, 9, 2)


def test_month_day_rolls_to_next_year():
    assert parse("1月5日", TODAY) == dt.date(2027, 1, 5)
    assert parse("9月2日", TODAY) == dt.date(2026, 9, 2)


def test_relative_words():
    assert parse("今天上课吗", TODAY) == TODAY
    assert parse("明天见", TODAY) == dt.date(2026, 8, 28)
    assert parse("后天交作业", TODAY) == dt.date(2026, 8, 29)


def test_weekday():
    assert parse("周五", TODAY) == dt.date(2026, 8, 28)
    assert parse("周四", TODAY) == TODAY  # 当天
    assert parse("下周三", TODAY) == dt.date(2026, 9, 2)  # 下一周的周三，不是下下周
    assert parse("下周一", TODAY) == dt.date(2026, 8, 31)


def test_parse_all_keeps_order():
    assert parse_all("下周一到下周二", TODAY) == [dt.date(2026, 8, 31), dt.date(2026, 9, 1)]


def test_no_date_returns_none():
    assert parse("这段话没有日期", TODAY) is None
