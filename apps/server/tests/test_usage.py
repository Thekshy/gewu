"""P23 用量域测试：UsageStore CRUD + contextvar 记账贯通（LLMService 双写）。"""

from __future__ import annotations

from pathlib import Path

from gewu.config import Settings
from gewu.llm.service import LLMService
from gewu.usage import UsageStore, current_user


def test_usage_store_add_today_and_totals(usage: UsageStore):
    assert usage.today("u@x.com") == 0
    usage.add("u@x.com", 100)
    usage.add("u@x.com", 50)  # 同日累加
    usage.add("v@x.com", 10)
    usage.add("", 999)  # 空 user 跳过
    usage.add("u@x.com", 0)  # 零额跳过
    assert usage.today("u@x.com") == 150
    assert dict(usage.today_all()) == {"u@x.com": 150, "v@x.com": 10}
    daily = dict(usage.daily_totals(7))
    assert sum(daily.values()) == 160


def test_llm_service_records_per_user_via_contextvar(usage: UsageStore, tmp_path: Path):
    """双写贯通：record_usage 读 current_user 归属（contextvar 未设=只记全局）。"""
    (tmp_path / "usage.json").write_text("{}", encoding="utf-8")
    budget_log: list[int] = []

    class FakeBudget:
        def add(self, tokens: int) -> None:
            budget_log.append(tokens)

    llm = LLMService(Settings(llm_api_key="k", data_dir=tmp_path), budget=FakeBudget(), usage=usage)
    llm.record_usage(200)  # 未设归属：全局记，个人不记
    assert budget_log == [200]
    assert usage.today_all() == []

    token = current_user.set("someone@x.com")
    try:
        llm.record_usage(30)
        llm.record_usage(0)  # 零额跳过
    finally:
        current_user.reset(token)
    assert budget_log == [200, 30]
    assert dict(usage.today_all()) == {"someone@x.com": 30}


def test_llm_service_without_usage_store_untouched(tmp_path: Path):
    """usage=None（测试替身）：record_usage 只走全局，不炸。"""
    (tmp_path / "usage.json").write_text("{}", encoding="utf-8")
    llm = LLMService(Settings(llm_api_key="k", data_dir=tmp_path))
    token = current_user.set("u@x.com")
    try:
        llm.record_usage(5)
    finally:
        current_user.reset(token)
