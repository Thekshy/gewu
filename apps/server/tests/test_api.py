"""端点契约测试（PARITY §2）：存储以 Fake 协议替身脱 PG，业务/认证用测试 PG 真跑。

P21 起 business/auth/memory 均 PG 化：make_client 注入 conftest 的 biz/auth/mem
夹具（函数级 wipe 隔离）；受保护端点用 make_logged_client 拿 cookie 会话。
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

from fastapi.testclient import TestClient

from gewu.api.app import create_app
from gewu.auth.store import AuthStore
from gewu.business.db import Business
from gewu.config import Settings
from gewu.memory import MemoryStore
from gewu.rag.store import DocInfo, Stats
from tests.conftest import make_logged_client


class FakeStore:
    """DocStore 协议替身（PG 接线另有真库测试，见 test_store_pg.py）。"""

    def __init__(self, stats: Stats, docs: list[DocInfo]) -> None:
        self._stats = stats
        self._docs = docs

    def get_stats(self) -> Stats:
        return self._stats

    def list_docs(self) -> list[DocInfo]:
        return list(self._docs)


class FakeRetriever:
    """检索替身：只读端点测试不需要真检索；search 契约测试单独注入行为。"""

    def __init__(self, hits: list | None = None, k: int = 5) -> None:
        self.hits = hits or []
        self.k = k
        self.calls: list[tuple[str, int]] = []

    def search(self, query: str, k: int) -> list:
        self.calls.append((query, k))
        return list(self.hits)


def make_client(
    tmp_path: Path,
    biz: Business,
    mem: MemoryStore,
    auth: AuthStore,
    usage: dict | None = None,
    retriever: FakeRetriever | None = None,
) -> TestClient:
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
    app = create_app(
        settings,
        store=store,
        business=biz,
        memory=mem,
        auth=auth,
        retriever=retriever or FakeRetriever(),
    )
    return TestClient(app)


def test_health_contract(tmp_path: Path, biz, mem, auth):
    today = date.today().isoformat()
    c = make_client(tmp_path, biz, mem, auth, usage={"date": today, "tokens": 123})
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


def test_health_budget_resets_cross_day(tmp_path: Path, biz, mem, auth):
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    c = make_client(tmp_path, biz, mem, auth, usage={"date": yesterday, "tokens": 999})
    assert c.get("/api/health").json()["budget"]["used"] == 0


def test_health_llm_false_without_key(tmp_path: Path, biz, mem, auth):
    (tmp_path / "usage.json").write_text("{}", encoding="utf-8")
    settings = Settings(llm_api_key="", embed_api_key="", data_dir=tmp_path)
    store = FakeStore(Stats(docs=0, chunks=0, embedded=False), [])
    app = create_app(
        settings,
        store=store,
        business=biz,
        memory=mem,
        auth=auth,
        retriever=FakeRetriever(),
    )
    body = TestClient(app).get("/api/health").json()
    assert body["llm"] is False
    assert body["embeddings"] is False


def test_docs_contract(tmp_path: Path, biz, mem, auth):
    c = make_client(tmp_path, biz, mem, auth)
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


def test_business_overview_requires_login(tmp_path: Path, biz, mem, auth):
    c = make_client(tmp_path, biz, mem, auth)
    assert c.get("/api/business/overview").status_code == 401


def test_business_overview_mine_view_filters_by_email(tmp_path: Path, biz, mem, auth):
    app = make_client(tmp_path, biz, mem, auth).app
    c = make_logged_client(app, auth, email="u1@example.com")
    # 本人一条有效预约；陌生人一条预约 + 一张请假单（本人视图不可见）
    assert biz.book_venue(
        "venue-basketball", "2099-10-01", "10:00-12:00", "训练", "u1@example.com"
    ).ok
    assert biz.book_venue(
        "venue-room301", "2099-10-02", "14:00-16:00", "自习", "stranger@example.com"
    ).ok
    assert biz.submit_leave("stranger@example.com", "事假", "2099-11-01", "2099-11-02", "私事").ok

    body = c.get("/api/business/overview").json()
    assert body["scope"] == "mine"
    assert [b["booking_id"] for b in body["bookings"]] == ["VE-0001"]
    assert body["bookings"][0]["user"] == "u1@example.com"
    assert body["tickets"] == []


def test_business_overview_admin_all(tmp_path: Path, biz, mem, auth):
    app = make_client(tmp_path, biz, mem, auth).app
    admin = make_logged_client(app, auth, email="boss@example.com", admin=True)
    student = make_logged_client(app, auth, email="s1@example.com")
    assert biz.book_venue("venue-basketball", "2099-10-01", "10:00-12:00", "", "s1@example.com").ok

    # 普通用户 ?all=1 不生效（仍本人视图）
    assert student.get("/api/business/overview?all=1").json()["scope"] == "mine"
    body = admin.get("/api/business/overview?all=1").json()
    assert body["scope"] == "all"
    assert len(body["bookings"]) == 1


def test_business_reset_admin_only(tmp_path: Path, biz, mem, auth):
    app = make_client(tmp_path, biz, mem, auth).app
    student = make_logged_client(app, auth, email="s1@example.com")
    admin = make_logged_client(app, auth, email="boss@example.com", admin=True)
    assert biz.book_venue("venue-basketball", "2099-10-01", "10:00-12:00", "", "s1@example.com").ok

    assert student.post("/api/business/reset").status_code == 403
    assert len(biz.all_bookings()) == 1  # 未被清掉
    assert admin.post("/api/business/reset").status_code == 200
    assert biz.all_bookings() == []


def test_search_requires_login(tmp_path: Path, biz, mem, auth):
    c = make_client(tmp_path, biz, mem, auth)
    assert c.post("/api/search", json={"query": "图书馆", "k": 3}).status_code == 401
