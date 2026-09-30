"""dates / tx 域 / business 写路径 / ReAct 子图测试（P14-4）。"""

from __future__ import annotations

from datetime import date

from gewu.dates import parse, parse_all, parse_iso

BASE = date(2026, 8, 27)  # 周四（与 Go dates_test 同基准日）


def _p(text: str) -> str:
    return parse_iso(text, BASE)


def test_full_date_formats():
    assert _p("2026-09-02") == "2026-09-02"
    assert _p("2026/9/2") == "2026-09-02"
    assert _p("2026年9月2日") == "2026-09-02"


def test_month_day_rolls_to_next_year():
    assert _p("1月5日") == "2027-01-05"  # 已过去 → 顺延明年
    assert _p("9月2日") == "2026-09-02"


def test_invalid_calendar_date_rejected():
    assert _p("2月30日") == ""


def test_relative_words():
    assert _p("今天") == "2026-08-27"
    assert _p("明天") == "2026-08-28"
    assert _p("后天") == "2026-08-29"
    assert _p("周一") == "2026-08-31"  # 周四的最近将来周一
    assert _p("周四") == "2026-08-27"  # 当天也算
    assert _p("下周三") == "2026-09-02"  # 下周一(08-31)基准 + 2
    assert _p("下周一") == "2026-08-31"


def test_parse_all_ordered():
    dates = parse_all("明天和下周三之间，后天复查", BASE)
    # 按出现位置排序（明天 pos0 → 下周三 pos3 → 后天 pos9）
    assert [d.isoformat() for d in dates] == ["2026-08-28", "2026-09-02", "2026-08-29"]


def test_parse_none():
    assert parse("没有日期表述的内容", BASE) is None
