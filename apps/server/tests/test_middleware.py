"""P14-7 支撑域测试：budget 记账与 429 闸 / trace-id / 限流。"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from gewu.api.app import create_app
from gewu.budget import TokenBudget
from gewu.business.db import Business
from gewu.config import Settings
from tests.test_api import DocInfo, FakeRetriever, FakeStore, Stats
from tests.test_chat_api import FakeChatLLM


def test_token_budget_roll_and_persist(tmp_path: Path):
    p = tmp_path / "usage.json"
    b = TokenBudget(p, 100)
    assert b.used() == 0
    b.add(40)
    assert b.used() == 40
    assert json.loads(p.read_text())["tokens"] == 40
    # 新实例从文件恢复
    assert TokenBudget(p, 100).used() == 40
    # 跨天：文件日期是昨天 → 归零
    p.write_text(json.dumps({"date": "2000-01-01", "tokens": 99}))
    assert TokenBudget(p, 100).used() == 0


def test_token_budget_ensure_raises_when_exhausted(tmp_path: Path):
    import pytest

    from gewu.budget import BudgetExhausted

    b = TokenBudget(tmp_path / "usage.json", 10)
    b.add(10)
    with pytest.raises(BudgetExhausted, match="2000000|10"):
        b.ensure()


def _client(tmp_path: Path, limit: int = 600) -> TestClient:
    (tmp_path / "usage.json").write_text("{}", encoding="utf-8")
    settings = Settings(llm_api_key="lk", data_dir=tmp_path, rate_limit_per_minute=limit)
    return TestClient(
        create_app(
            settings,
            store=FakeStore(Stats(1, 1, False), [DocInfo("d", "t", "s", "u", 1)]),
            business=Business(tmp_path / "b.db"),
            retriever=FakeRetriever(),
            llm=FakeChatLLM(["答"]),
        )
    )


def test_trace_id_header_present(tmp_path: Path):
    c = _client(tmp_path)
    r = c.get("/api/health")
    assert r.headers.get("x-trace-id")  # TestClient 头名小写化
    r2 = c.get("/api/health", headers={"X-Trace-Id": "fixed-id"})
    assert r2.headers["x-trace-id"] == "fixed-id"  # 入站头沿用


def test_rate_limit_429_after_threshold(tmp_path: Path):
    c = _client(tmp_path, limit=3)
    for _ in range(3):
        assert c.get("/api/docs").status_code == 200
    r = c.get("/api/docs")
    assert r.status_code == 429
    assert r.json()["detail"] == "请求过于频繁，请稍后再试"
    # 健康检查不受限流影响
    assert c.get("/api/health").status_code == 200


def _client_nousage(tmp_path: Path) -> TestClient:
    """不预写 usage.json 的 client（budget 用例自行控制 usage 文件）。"""
    settings = Settings(llm_api_key="lk", data_dir=tmp_path, rate_limit_per_minute=600)
    return TestClient(
        create_app(
            settings,
            store=FakeStore(Stats(1, 1, False), [DocInfo("d", "t", "s", "u", 1)]),
            business=Business(tmp_path / "b.db"),
            retriever=FakeRetriever(),
            llm=FakeChatLLM(["答"]),
        )
    )


def test_chat_budget_429(tmp_path: Path):
    (tmp_path / "usage.json").write_text(
        json.dumps({"date": __import__("datetime").date.today().isoformat(), "tokens": 2_000_000}),
        encoding="utf-8",
    )
    c = _client_nousage(tmp_path)
    r = c.post("/api/chat", json={"question": "q"})
    assert r.status_code == 429
    assert "预算" in r.json()["detail"]
