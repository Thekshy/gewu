"""POST /api/search 契约测试（PARITY §2.3）：校验语义逐条对照 Go 实现。

P21 起需登录：hit client 一律注册登录（401 路径在 test_api/test_auth 覆盖）。
"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from gewu.api.app import create_app
from gewu.config import Settings
from gewu.rag.store import DocInfo, Hit, Stats
from tests.conftest import make_logged_client
from tests.test_api import FakeRetriever, FakeStore


def _hit(chunk_id: int, doc_id: str, text: str) -> Hit:
    return Hit(
        chunk_id=chunk_id,
        doc_id=doc_id,
        seq=chunk_id,
        text=text,
        title=f"标题{doc_id}",
        source="教务处",
        section_path="第一章 > 第一节",
    )


def _hit_client(tmp_path, biz, mem, auth, hits: list[Hit]):
    (tmp_path / "usage.json").write_text("{}", encoding="utf-8")
    rr = FakeRetriever(hits)
    settings = Settings(llm_api_key="lk", embed_api_key="ek", data_dir=tmp_path)
    app = create_app(
        settings,
        store=FakeStore(Stats(1, 1, False), [DocInfo("d1", "t", "s", "u", 1)]),
        business=biz,
        memory=mem,
        auth=auth,
        retriever=rr,
    )
    return make_logged_client(app, auth), rr


def test_search_ok_maps_fields_and_truncates_300(tmp_path: Path, biz, mem, auth):
    long_text = "甲" * 350
    c, rr = _hit_client(tmp_path, biz, mem, auth, [_hit(7, "doc-x", long_text)])
    r = c.post("/api/search", json={"query": "图书馆几点开门", "k": 3})
    assert r.status_code == 200
    body = r.json()
    assert len(body) == 1
    assert body[0]["doc_id"] == "doc-x"
    assert body[0]["seq"] == 7
    assert body[0]["text"] == "甲" * 300  # 截断到 300 字符
    assert body[0]["title"] == "标题doc-x"
    assert body[0]["source"] == "教务处"
    assert rr.calls == [("图书馆几点开门", 3)]


def test_search_k_defaults_5(tmp_path: Path, biz, mem, auth):
    c, rr = _hit_client(tmp_path, biz, mem, auth, [])
    assert c.post("/api/search", json={"query": "奖学金"}).status_code == 200
    assert rr.calls == [("奖学金", 5)]


def test_search_query_length_422(tmp_path: Path, biz, mem, auth):
    c, _ = _hit_client(tmp_path, biz, mem, auth, [])
    r = c.post("/api/search", json={"query": ""})
    assert r.status_code == 422
    assert r.json()["detail"] == "query 长度需在 1~200 字之间"
    r = c.post("/api/search", json={"query": "字" * 201})
    assert r.status_code == 422
    assert r.json()["detail"] == "query 长度需在 1~200 字之间"


def test_search_k_range_422(tmp_path: Path, biz, mem, auth):
    c, _ = _hit_client(tmp_path, biz, mem, auth, [])
    for k in (0, 21):
        r = c.post("/api/search", json={"query": "q", "k": k})
        assert r.status_code == 422
        assert r.json()["detail"] == "k 需在 1~20 之间"


def test_search_binding_errors_are_uniform_422(tmp_path: Path, biz, mem, auth):
    """类型不符/非对象体/非法 JSON → 统一「请求体不是合法 JSON」（对齐 Go ShouldBindJSON）。"""
    c, _ = _hit_client(tmp_path, biz, mem, auth, [])
    cases = [
        {"query": 123},
        {"query": "q", "k": "5"},
        {"query": "q", "k": True},
        [1, 2],  # 非 JSON 对象
    ]
    for body in cases:
        r = c.post("/api/search", json=body)
        assert r.status_code == 422, body
        assert r.json()["detail"] == "请求体不是合法 JSON"
    r = c.post("/api/search", content=b"not-json", headers={"Content-Type": "application/json"})
    assert r.status_code == 422
    assert r.json()["detail"] == "请求体不是合法 JSON"


def test_search_missing_body_is_binding_error(tmp_path: Path, biz, mem, auth):
    c, _ = _hit_client(tmp_path, biz, mem, auth, [])
    r = c.post("/api/search")
    assert r.status_code == 422
    assert r.json()["detail"] == "请求体不是合法 JSON"


def test_usage_json_write_roundtrip(tmp_path: Path, biz, mem, auth):
    """防回归占位：确认 usage.json 读取路径稳定（空文件→used=0）。"""
    (tmp_path / "usage.json").write_text(json.dumps({}), encoding="utf-8")
    rr = FakeRetriever()
    settings = Settings(llm_api_key="", embed_api_key="", data_dir=tmp_path)
    app = create_app(
        settings,
        store=FakeStore(Stats(0, 0, False), []),
        business=biz,
        memory=mem,
        auth=auth,
        retriever=rr,
    )
    assert TestClient(app).get("/api/health").json()["budget"]["used"] == 0
