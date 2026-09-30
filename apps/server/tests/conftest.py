"""PG 依赖用例的公共夹具：gewu_test 真库 + 会话级咨询锁（对齐 Go OpenTest 惯例）。

PG 不可达时 Skip（门禁口径：make pg-up / CI service；裸跑 pytest 允许跳过）。
咨询锁进程持有到退出（连接断开 PG 自动释放），跨用例文件串行化共享测试库。
"""

from __future__ import annotations

import os
import urllib.parse

import psycopg
import pytest

from gewu.config import load_dotenv
from gewu.rag.store import Store

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
def pg_store(pg_dsn: str) -> Store:
    global _lock_conn
    if _lock_conn is None:
        _lock_conn = psycopg.connect(pg_dsn)
        _lock_conn.autocommit = True
        _lock_conn.execute("SELECT pg_advisory_lock(%s)", (LOCK_KEY,))
    store = Store(pg_dsn, ensure=True)
    store.wipe()
    yield store
    store.close()
    # _lock_conn 故意不关：进程退出 = 锁释放
