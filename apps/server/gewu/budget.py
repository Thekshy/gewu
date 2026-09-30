"""每日 token 预算（移植自 Go internal/budget：data/usage.json 同格式，跨天归零）。"""

from __future__ import annotations

import json
import threading
from datetime import date
from pathlib import Path


class BudgetExhausted(Exception):
    """预算耗尽（chat 入口 429 的依据）。"""


class TokenBudget:
    """进程内记账 + 文件持久化（毫秒级写，不含 LLM）。线程安全。"""

    def __init__(self, path: Path, daily_limit: int) -> None:
        self._path = path
        self._limit = daily_limit
        self._lock = threading.Lock()
        self._used = 0
        self._load()

    def _load(self) -> None:
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            if data.get("date") == date.today().isoformat():
                self._used = int(data.get("tokens", 0))
        except (OSError, ValueError, TypeError):
            self._used = 0

    def used(self) -> int:
        with self._lock:
            self._rollover_if_new_day()
            return self._used

    def limit(self) -> int:
        return self._limit

    def ensure(self) -> None:
        """预算闸：耗尽抛 BudgetExhausted（Go Ensure 的 429 等价）。"""
        if self.used() >= self._limit:
            raise BudgetExhausted(f"今日 token 预算已用尽（上限 {self._limit}），请明天再试")

    def add(self, tokens: int) -> None:
        with self._lock:
            self._rollover_if_new_day()
            self._used += max(0, int(tokens))
            try:
                self._path.write_text(
                    json.dumps({"date": date.today().isoformat(), "tokens": self._used}),
                    encoding="utf-8",
                )
            except OSError:
                pass  # 记账文件写失败不影响主链路（内存值仍准确到进程生命周期）

    def _rollover_if_new_day(self) -> None:
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            if data.get("date") != date.today().isoformat():
                self._used = 0
        except (OSError, ValueError, TypeError):
            pass
