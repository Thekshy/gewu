"""确定性中文日期解析（移植自 Go internal/dates/dates.go，语义逐条对照）。

设计动机：LLM 做日期换算容易错（"下周三"到底是哪天），策略是 LLM 只负责
「找出」日期表述，换算统一走本模块。时区固定中国标准时间（UTC+8）。
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone

CN_TZ = timezone(timedelta(hours=8), name="UTC+8")

_FULL_RE = re.compile(r"(\d{4})[-/年.](\d{1,2})[-/月.](\d{1,2})[日号]?")
_MD_RE = re.compile(r"(\d{1,2})月(\d{1,2})[日号]?")
_WEEK_RE = re.compile(r"(下?)(?:周|星期)([一二三四五六日天])")

_DAYS_WORDS = [("今天", 0), ("今日", 0), ("明天", 1), ("明日", 1), ("后天", 2)]
_WEEKDAY_INDEX = {"一": 0, "二": 1, "三": 2, "四": 3, "五": 4, "六": 5, "日": 6, "天": 6}


def today_cn() -> date:
    """业务与解析统一使用的「今天」（中国时区）。"""
    return datetime.now(CN_TZ).date()


def today_iso() -> str:
    return today_cn().isoformat()


def _safe_date(y: int, m: int, d: int) -> date | None:
    """非法日期（如 2 月 30 日）返回 None。"""
    try:
        return date(y, m, d)
    except ValueError:
        return None


def _matches(text: str, today: date) -> list[tuple[int, date]]:
    """按出现位置收集全部可识别日期：(pos, date)。"""
    found: list[tuple[int, date]] = []
    for m in _FULL_RE.finditer(text):
        dt = _safe_date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        if dt is not None:
            found.append((m.start(), dt))
    for m in _MD_RE.finditer(text):
        mm, dd = int(m.group(1)), int(m.group(2))
        dt = _safe_date(today.year, mm, dd)
        if dt is not None and dt < today:  # 已过去的「N月N日」顺延到明年
            dt = _safe_date(today.year + 1, mm, dd)
        if dt is not None:
            found.append((m.start(), dt))
    # Go Weekday 周日=0；Python date.weekday() 周一=0 —— 直接用 Python 基准
    py_weekday = today.weekday()  # Monday=0 … Sunday=6
    for m in _WEEK_RE.finditer(text):
        target = _WEEKDAY_INDEX[m.group(2)]
        if m.group(1) == "下":  # 「下周X」= 下一周的周X（下周一为基准）
            delta = (7 - py_weekday) % 7 + target
        else:  # 「周X」= 最近将来的周X（当天也算）
            delta = (target - py_weekday + 7) % 7
        found.append((m.start(), today + timedelta(days=delta)))
    for word, offset in _DAYS_WORDS:
        start = 0
        while (pos := text.find(word, start)) != -1:
            found.append((pos, today + timedelta(days=offset)))
            start = pos + len(word)
    found.sort(key=lambda x: x[0])  # 按出现位置稳定排序（同位置保持收集顺序）
    return found


def parse_all(text: str, today: date | None = None) -> list[date]:
    """按出现顺序返回文本中的全部日期（中国时区当天为基准）。"""
    base = today or today_cn()
    return [dt for _, dt in _matches(text, base)]


def parse(text: str, today: date | None = None) -> date | None:
    """返回文本中第一个可识别日期；识别不到返回 None。"""
    all_dates = parse_all(text, today)
    return all_dates[0] if all_dates else None


def parse_iso(text: str, today: date | None = None) -> str:
    """解析并返回 YYYY-MM-DD，失败返回空串。"""
    dt = parse(text, today)
    return dt.isoformat() if dt else ""
