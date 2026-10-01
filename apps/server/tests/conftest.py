"""PG 依赖用例的公共夹具：gewu_test 真库 + 会话级咨询锁（对齐 Go OpenTest 惯例）。

PG 不可达时 Skip（门禁口径：make pg-up / CI service；裸跑 pytest 允许跳过）。
咨询锁进程持有到退出（连接断开 PG 自动释放），跨用例文件串行化共享测试库。
P21 起 business/memory/auth 三域入库（SQLite 退役），夹具统一 wipe 隔离。
"""

from __future__ import annotations

import os
import urllib.parse

import psycopg
import pytest
from fastapi.testclient import TestClient
from langgraph.checkpoint.postgres import PostgresSaver

from gewu.auth.store import AuthStore
from gewu.business.db import Business
from gewu.config import load_dotenv
from gewu.memory import MemoryStore
from gewu.rag.store import Store
from gewu.session.store import SessionStore
from gewu.usage import UsageStore

LOCK_KEY = 941012  # 与 Go testLockKey 同值（任意常量，全仓库唯一即可）
DEFAULT_TEST_DSN = "postgres://gewu:gewu@127.0.0.1:5433/gewu_test?sslmode=disable"

_lock_conn: psycopg.Connection | None = None


def test_dsn() -> str:
    """PG_TEST_DSN > PG_DSN（OS 环境变量或仓库 .env）推导 <db>_test > 缺省。"""
    load_dotenv()
    explicit = os.environ.get("PG_TEST_DSN")
    if explicit:
        return explicit
    base = os.environ.get("PG_DSN") or DEFAULT_TEST_DSN
    parts = urllib.parse.urlsplit(base)
    db = parts.path.lstrip("/")
    if not db:
        return DEFAULT_TEST_DSN
    return urllib.parse.urlunsplit(
        (parts.scheme, parts.netloc, f"/{db}_test", parts.query, parts.fragment)
    )


@pytest.fixture(scope="session")
def pg_dsn() -> str:
    dsn = test_dsn()
    try:
        conn = psycopg.connect(dsn, connect_timeout=3)
    except Exception as e:  # noqa: BLE001 - 不可达即跳过（门禁有 pg-up 前置）
        pytest.skip(f"PG 测试库不可达（{e}）——门禁请先 make pg-up")
    conn.close()
    return dsn


@pytest.fixture(scope="session")
def pg_lock(pg_dsn: str) -> str:
    """进程级咨询锁（首个使用者取，断开自动释放）——共享测试库跨文件串行化。"""
    global _lock_conn
    if _lock_conn is None:
        _lock_conn = psycopg.connect(pg_dsn)
        _lock_conn.autocommit = True
        _lock_conn.execute("SELECT pg_advisory_lock(%s)", (LOCK_KEY,))
    return pg_dsn


@pytest.fixture(scope="session")
def pg_store(pg_lock: str, pg_dsn: str) -> Store:
    store = Store(pg_dsn, ensure=True)
    store.wipe()
    yield store
    store.close()
    # _lock_conn 故意不关：进程退出 = 锁释放


@pytest.fixture(scope="session")
def _auth_store(pg_lock: str, pg_dsn: str) -> AuthStore:
    store = AuthStore(pg_dsn)
    yield store
    store.close()


@pytest.fixture
def auth(_auth_store: AuthStore) -> AuthStore:
    _auth_store.wipe()
    return _auth_store


@pytest.fixture(scope="session")
def _biz_store(pg_lock: str, pg_dsn: str) -> Business:
    biz = Business(pg_dsn)
    yield biz
    biz.close()


@pytest.fixture
def biz(_biz_store: Business) -> Business:
    _biz_store.wipe()
    return _biz_store


@pytest.fixture(scope="session")
def _mem_store(pg_lock: str, pg_dsn: str) -> MemoryStore:
    mem = MemoryStore(pg_dsn)
    yield mem
    mem.close()


@pytest.fixture
def mem(_mem_store: MemoryStore) -> MemoryStore:
    _mem_store.wipe()
    return _mem_store


@pytest.fixture(scope="session")
def _sess_store(pg_lock: str, pg_dsn: str) -> SessionStore:
    s = SessionStore(pg_dsn)
    yield s
    s.close()


@pytest.fixture
def sess(_sess_store: SessionStore) -> SessionStore:
    _sess_store.wipe()
    return _sess_store


@pytest.fixture(scope="session")
def _usage_store(pg_lock: str, pg_dsn: str) -> UsageStore:
    u = UsageStore(pg_dsn)
    yield u
    u.close()


@pytest.fixture
def usage(_usage_store: UsageStore) -> UsageStore:
    _usage_store.wipe()
    return _usage_store


@pytest.fixture(scope="session")
def _test_checkpointer(pg_lock: str, pg_dsn: str):
    """PostgresSaver（测试库）：连带删除/历史提取的行级断言用（P22）。

    autocommit 单连接（官方要求）；建表幂等；测试库专库不殃及真库。
    """
    conn = psycopg.connect(pg_dsn, autocommit=True)
    cp = PostgresSaver(conn)
    cp.setup()
    yield cp
    conn.close()


@pytest.fixture
def cp(_test_checkpointer, pg_dsn: str):
    """每用例清空 checkpoints 三表（行数断言基线归零）。"""
    with psycopg.connect(pg_dsn, autocommit=True) as conn:
        for t in ("checkpoints", "checkpoint_blobs", "checkpoint_writes"):
            conn.execute(f"DELETE FROM {t}")
    return _test_checkpointer


def make_logged_client(
    app,
    auth: AuthStore,
    *,
    email: str = "u1@example.com",
    password: str = "password123",
    admin: bool = False,
) -> TestClient:
    """注册即登录的 TestClient（cookie 会话随 client 持有）；admin=True 注册后提权。

    role 权威在 users 表：promote 后已发 cookie 立即生效（user_for_token 实时 join）。
    """
    client = TestClient(app)
    code = auth.create_invite(uses=9, note="test")
    r = client.post(
        "/api/auth/register",
        json={"email": email, "password": password, "invite_code": code},
    )
    assert r.status_code == 200, r.text
    if admin:
        assert auth.promote_admin(email)
    return client
