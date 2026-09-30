"""端点契约测试（PARITY §2）：存储以 Fake 协议替身脱 PG，业务库用临时 SQLite 真跑。"""

from __future__ import annotations

import json
import sqlite3
from datetime import date, timedelta
from pathlib import Path

from fastapi.testclient import TestClient

from gewu.api.app import create_app
from gewu.business.db import Business
from gewu.config import Settings
from gewu.rag.store import DocInfo, Stats


class FakeStore:
    """DocStore 协议替身（PG 接线另有真库测试，随 P14-1 加）。"""

    def __init__(self, stats: Stats, docs: list[DocInfo]) -> None:
        self._stats = stats
        self._docs = docs

    def get_stats(self) -> Stats:
        return self._stats

    def list_docs(self) -> list[DocInfo]:
        return list(self._docs)


def make_client(tmp_path: Path, usage: dict | None = None) -> TestClient:
    (tmp_path / "usage.json").write_text(json.dumps(usage or {}), encoding="utf-8")
    settings = Settings(
        llm_api_key="lk",
        embed_api_key="ek",
        data_dir=tmp_path,
    )
    store = FakeStore(
        stats=Stats(docs=15, chunks=57, embedded=True),
        docs=[
            DocInfo("doc-001", "学生手册", "corpus", "2026-09-01", 4),
            DocInfo("doc-002", "请假制度", "corpus", "2026-09-02", 3),
        ],
    )
    app = create_app(settings, store=store, business=Business(tmp_path / "business.db"))
    return TestClient(app)


def test_health_contract(tmp_path: Path):
    today = date.today().isoformat()
    c = make_client(tmp_path, usage={"date": today, "tokens": 123})
    r = c.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["version"] == "0.1.0"
    assert body["llm"] is True
    assert body["embeddings"] is True  # 有 key 且库里有向量
    assert body["docs"] == 15
    assert body["chunks"] == 57
    assert body["budget"] == {"used": 123, "limit": 2_000_000}


def test_health_budget_resets_cross_day(tmp_path: Path):
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    c = make_client(tmp_path, usage={"date": yesterday, "tokens": 999})
    assert c.get("/api/health").json()["budget"]["used"] == 0


def test_health_llm_false_without_key(tmp_path: Path):
    (tmp_path / "usage.json").write_text("{}", encoding="utf-8")
    settings = Settings(llm_api_key="", embed_api_key="", data_dir=tmp_path)
    store = FakeStore(Stats(docs=0, chunks=0, embedded=False), [])
    app = create_app(settings, store=store, business=Business(tmp_path / "business.db"))
    body = TestClient(app).get("/api/health").json()
    assert body["llm"] is False
    assert body["embeddings"] is False


def test_docs_contract(tmp_path: Path):
    c = make_client(tmp_path)
    r = c.get("/api/docs")
    assert r.status_code == 200
    docs = r.json()
    assert [d["doc_id"] for d in docs] == ["doc-001", "doc-002"]  # 按 doc_id 升序
    assert docs[0] == {
        "doc_id": "doc-001",
        "title": "学生手册",
        "source": "corpus",
        "updated": "2026-09-01",
        "chunks": 4,
    }


def test_business_overview_empty(tmp_path: Path):
    c = make_client(tmp_path)
    r = c.get("/api/business/overview")
    assert r.status_code == 200
    body = r.json()
    assert body == {"bookings": [], "tickets": []}


def test_business_overview_with_data(tmp_path: Path):
    c = make_client(tmp_path)
    # 直插运行数据（写路径 P14-6 才移植）：一条有效预约、一条已取消（应被过滤）、一张请假单
    conn = sqlite3.connect(tmp_path / "business.db")
    conn.execute(
        "INSERT INTO bookings (venue_id, date, slot, purpose, user, status, created_at) "
        "VALUES ('venue-badminton', '2026-10-01', '18:00-19:00', '院队训练', "
        "'demo-student', '有效', '2026-09-30 10:00:00')"
    )
    conn.execute(
        "INSERT INTO bookings (venue_id, date, slot, purpose, user, status, created_at) "
        "VALUES ('venue-room301', '2026-10-02', '10:00-11:00', '', "
        "'demo-student', '已取消', '2026-09-30 11:00:00')"
    )
    conn.execute(
        "INSERT INTO leave_tickets (user, leave_type, start_date, end_date, days, "
        "reason, approver_level, status, created_at) "
        "VALUES ('demo-student', '事假', '2026-10-08', '2026-10-09', 2, "
        "'家中事务', 'counselor', '待审批', '2026-09-30 12:00:00')"
    )
    conn.commit()
    conn.close()

    body = c.get("/api/business/overview").json()
    assert body["bookings"] == [
        {
            "booking_id": "VE-0001",
            "venue": "羽毛球馆",
            "date": "2026-10-01",
            "slot": "18:00-19:00",
            "user": "demo-student",
        }
    ]
    assert body["tickets"] == [
        {
            "ticket": "LV-0001",
            "user": "demo-student",
            "leave_type": "事假",
            "start": "2026-10-08",
            "end": "2026-10-09",
            "days": 2,
            "approver": "counselor",
            "status": "待审批",
        }
    ]
