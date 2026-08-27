"""确定性中文日期解析：把「明天 / 下周三 / 9月2日 / 2026-09-02」统一成 date。

LLM 做日期换算容易错（"下周三"到底是哪天），因此策略是：LLM 只负责从句子里
「找出」日期表述，统一交给本模块做确定性换算。
"""

from __future__ import annotations

import datetime as dt
import re

_FULL_RE = re.compile(r"(\d{4})[-/年.](\d{1,2})[-/月.](\d{1,2})[日号]?")
_MD_RE = re.compile(r"(\d{1,2})月(\d{1,2})[日号]?")
_WEEK_RE = re.compile(r"(下?)(?:周|星期)([一二三四五六日天])")
_DAYS_WORDS = ("今天", "今日", "明天", "明日", "后天")
_WEEKDAYS = "一二三四五六日"

# 中国标准时间（无夏令时，固定偏移即可，且免 tzdata 依赖）
CN_TZ = dt.timezone(dt.timedelta(hours=8), "UTC+8")


def today() -> dt.date:
    """业务与解析统一使用的「今天」（中国时区）。"""
    return dt.datetime.now(tz=CN_TZ).date()


_today_cn = today


def today_iso() -> str:
    return today().isoformat()


def now_cn() -> dt.datetime:
    return dt.datetime.now(tz=CN_TZ)


def _safe_date(year: int, month: int, day: int) -> dt.date | None:
    try:
        return dt.date(year, month, day)
    except ValueError:
        return None


def _matches(text: str, today: dt.date) -> list[tuple[int, dt.date]]:
    """按出现位置收集全部可识别的日期。"""
    found: list[tuple[int, dt.date]] = []
    for m in _FULL_RE.finditer(text):
        d = _safe_date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        if d:
            found.append((m.start(), d))
    for m in _MD_RE.finditer(text):
        d = _safe_date(today.year, int(m.group(1)), int(m.group(2)))
        if d and d < today:  # 已过去的"N月N日"顺延到明年
            d = _safe_date(today.year + 1, int(m.group(1)), int(m.group(2)))
        if d:
            found.append((m.start(), d))
    cur_weekday = today.weekday()
    for m in _WEEK_RE.finditer(text):
        target = _WEEKDAYS.index(m.group(2))
        if m.group(1) == "下":  # "下周X" = 下一周的周X（下周一为基准）
            delta = (7 - cur_weekday) % 7 + target
        else:  # "周X" = 最近将到来的周X（当天也算）
            delta = (target - cur_weekday) % 7
        found.append((m.start(), today + dt.timedelta(days=delta)))
    for word in _DAYS_WORDS:
        pos = text.find(word)
        while pos != -1:
            offset = 0 if word in ("今天", "今日") else (1 if word in ("明天", "明日") else 2)
            found.append((pos, today + dt.timedelta(days=offset)))
            pos = text.find(word, pos + len(word))
    found.sort(key=lambda x: x[0])
    return found


def parse_all(text: str, today: dt.date | None = None) -> list[dt.date]:
    """按出现顺序返回文本中的全部日期。"""
    today = today or _today_cn()
    return [d for _, d in _matches(text, today)]


def parse(text: str, today: dt.date | None = None) -> dt.date | None:
    """返回文本中第一个可识别的日期；识别不到返回 None。"""
    dates = parse_all(text, today)
    return dates[0] if dates else None
