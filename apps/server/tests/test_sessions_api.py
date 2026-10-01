"""P22 会话端点测试：CRUD/本人隔离/归属 404/messages 提取/三处连带删除。

硬点覆盖（任务书 §2.1）：messages 提取纯函数（工具轮过滤/system 跳过/
interrupt 态天然支持——get_state 不依赖 next）；checkpointer 删除走真
PostgresSaver 三表行数断言。
"""

from __future__ import annotations

from pathlib import Path

import psycopg
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from gewu.api.app import create_app
from gewu.api.sessions import extract_dialog_messages
from gewu.auth.store import AuthStore
from gewu.business.db import Business
from gewu.config import Settings
from gewu.memory import MemoryStore
from gewu.rag.store import DocInfo, Stats
from gewu.session.store import SessionStore
from tests.conftest import make_logged_client
from tests.test_api import FakeRetriever, FakeStore
from tests.test_chat_api import FakeChatLLM

_CP_TABLES = ("checkpoints", "checkpoint_blobs", "checkpoint_writes")


def _hit() -> dict:
    return {
        "chunk_id": 1,
        "doc_id": "doc-x",
        "seq": 0,
        "text": "图书馆周一至周五 7:30—22:30 开放。",
        "title": "图书馆管理办法",
        "source": "图书馆",
        "section_path": "开放时间",
    }


def make_client(
    tmp_path: Path,
    biz: Business,
    mem: MemoryStore,
    auth: AuthStore,
    sess: SessionStore,
    cp,
    graph=None,
) -> TestClient:
    (tmp_path / "usage.json").write_text("{}", encoding="utf-8")
    settings = Settings(llm_api_key="lk", embed_api_key="ek", data_dir=tmp_path)
    app = create_app(
        settings,
        store=FakeStore(Stats(1, 1, False), [DocInfo("d1", "t", "s", "u", 1)]),
        business=biz,
        memory=mem,
        auth=auth,
        sessions=sess,
        checkpointer=cp,
        retriever=FakeRetriever([_hit()]),
        llm=FakeChatLLM(["开放时间", "是 7:30。"]),
    )
    if graph is not None:
        app.state.graph = graph
    return make_logged_client(app, auth)


def _cp_rows(pg_dsn: str, thread_id: str) -> list[int]:
    out: list[int] = []
    with psycopg.connect(pg_dsn, autocommit=True) as conn:
        for t in _CP_TABLES:
            n = conn.execute(f"SELECT COUNT(*) FROM {t} WHERE thread_id = %s", (thread_id,))
            out.append(int(n.fetchone()[0]))
    return out


def _episodic_rows(pg_dsn: str, session_id: str) -> int:
    with psycopg.connect(pg_dsn, autocommit=True) as conn:
        return int(
            conn.execute(
                "SELECT COUNT(*) FROM memory_episodic WHERE session_id = %s", (session_id,)
            ).fetchone()[0]
        )


# ---------- 登录守卫 ----------


def test_sessions_require_login(tmp_path, biz, mem, auth, sess, cp):
    c = make_client(tmp_path, biz, mem, auth, sess, cp)
    c.cookies.clear()  # 裸 client：四端点全 401
    for method, path in [
        ("post", "/api/sessions"),
        ("get", "/api/sessions"),
        ("patch", "/api/sessions/x"),
        ("delete", "/api/sessions/x"),
        ("get", "/api/sessions/x/messages"),
    ]:
        if method in ("post", "patch"):
            r = getattr(c, method)(path, json={})
        else:
            r = getattr(c, method)(path)
        assert r.status_code == 401, (method, path)


# ---------- 创建 / 列表 ----------


def test_create_and_list_kind_filter_and_isolation(tmp_path, biz, mem, auth, sess, cp):
    c = make_client(tmp_path, biz, mem, auth, sess, cp)
    r = c.post("/api/sessions", json={})
    assert r.status_code == 200
    s1 = r.json()
    assert s1["kind"] == "chat" and s1["title"] == "" and s1["session_id"]

    r = c.post("/api/sessions", json={"kind": "compare"})
    assert r.json()["kind"] == "compare"

    r = c.get("/api/sessions")
    assert {s["kind"] for s in r.json()} == {"chat", "compare"}
    r = c.get("/api/sessions?kind=chat")
    assert [s["kind"] for s in r.json()] == ["chat"]
    r = c.get("/api/sessions?kind=bogus")
    assert r.status_code == 422
    r = c.post("/api/sessions", json={"kind": "bogus"})
    assert r.status_code == 422

    # 本人隔离：第二个用户看不到 u1 的会话
    other = make_logged_client(c.app, auth, email="u2@example.com")
    assert other.get("/api/sessions").json() == []
    assert other.get(f"/api/sessions/{s1['session_id']}/messages").status_code == 404


# ---------- 改名 ----------


def test_rename_and_cross_user_404(tmp_path, biz, mem, auth, sess, cp):
    c = make_client(tmp_path, biz, mem, auth, sess, cp)
    sid = c.post("/api/sessions", json={}).json()["session_id"]

    r = c.patch(f"/api/sessions/{sid}", json={"title": "场馆预约咨询"})
    assert r.status_code == 200 and r.json()["title"] == "场馆预约咨询"

    for bad in ("", "x" * 61, None):
        r = c.patch(f"/api/sessions/{sid}", json={"title": bad})
        assert r.status_code == 422, bad
    r = c.patch("/api/sessions/no-such", json={"title": "t"})
    assert r.status_code == 404

    other = make_logged_client(c.app, auth, email="u2@example.com")
    assert other.patch(f"/api/sessions/{sid}", json={"title": "越权改名"}).status_code == 404
    # 越权改名不生效（本人标题未被覆盖）
    assert c.get("/api/sessions").json()[0]["title"] == "场馆预约咨询"


# ---------- 三处连带删除（Q4 硬点二） ----------


def test_delete_cascades_three_stores(tmp_path, biz, mem, auth, sess, cp, pg_dsn, monkeypatch):
    monkeypatch.setenv("MEMORY_CONSOLIDATE", "off")  # 关异步固化，episodic 断言免竞态
    c = make_client(tmp_path, biz, mem, auth, sess, cp)
    sid = c.post("/api/sessions", json={}).json()["session_id"]

    # 真跑一轮 direct 链路 → checkpointer 落盘该 thread + title 首问回填
    r = c.post(
        "/api/chat", json={"question": "图书馆几点开门", "mode": "direct", "session_id": sid}
    )
    assert r.status_code == 200
    assert any(n > 0 for n in _cp_rows(pg_dsn, sid)), "checkpointer 应有该 thread 行"
    assert c.get("/api/sessions").json()[0]["title"] == "图书馆几点开门"

    # episodic 手动写两条（绕开异步固化竞态）
    mem.append_episode(sid, "u1@example.com", "user", "图书馆几点开门")
    mem.append_episode(sid, "u1@example.com", "assistant", "7:30 开放")
    assert _episodic_rows(pg_dsn, sid) == 2

    r = c.delete(f"/api/sessions/{sid}")
    assert r.status_code == 200
    assert _cp_rows(pg_dsn, sid) == [0, 0, 0], "三表该 thread 全清"
    assert _episodic_rows(pg_dsn, sid) == 0
    assert c.get("/api/sessions").json() == []
    assert c.get(f"/api/sessions/{sid}/messages").status_code == 404

    # 他人不可删：u2 建会话后 u1 删 → 404 且行还在
    sid2 = c.post("/api/sessions", json={}).json()["session_id"]
    other = make_logged_client(c.app, auth, email="u2@example.com")
    assert other.delete(f"/api/sessions/{sid2}").status_code == 404
    assert len(c.get("/api/sessions").json()) == 1


# ---------- messages 提取（Q2 硬点一） ----------


def test_extract_dialog_messages_filters_tool_rounds():
    vals = {
        "messages": [
            SystemMessage("作答准则"),
            HumanMessage("图书馆几点开门"),
            AIMessage(
                "", tool_calls=[{"name": "search_policy", "args": {"q": "图书馆"}, "id": "c1"}]
            ),
            ToolMessage("命中 3 条", tool_call_id="c1"),
            AIMessage("7:30 开放"),
            HumanMessage("谢谢"),
            AIMessage(""),
        ]
    }
    out = extract_dialog_messages(vals)
    assert out == [
        {"role": "user", "text": "图书馆几点开门"},
        {"role": "assistant", "text": "7:30 开放"},
        {"role": "user", "text": "谢谢"},
    ]


def test_extract_dialog_messages_multimodal_content():
    vals = {
        "messages": [
            HumanMessage(content=[{"type": "text", "text": "预约"}]),
            AIMessage(content=[{"type": "text", "text": "好的"}]),
        ]
    }
    assert extract_dialog_messages(vals) == [
        {"role": "user", "text": "预约"},
        {"role": "assistant", "text": "好的"},
    ]


def test_extract_dialog_messages_interrupt_state():
    """interrupt 悬停态：已有人类轮可见，AI tool_calls 轮不进对话级视图。"""
    vals = {
        "messages": [
            HumanMessage("帮我预约明晚羽毛球馆"),
            AIMessage("", tool_calls=[{"name": "book_venue", "args": {}, "id": "c1"}]),
        ]
    }
    assert extract_dialog_messages(vals) == [{"role": "user", "text": "帮我预约明晚羽毛球馆"}]


class _FakeGraph:
    """get_state 替身：返回预置 values（endpoint 只读 values，interrupt 态天然兼容）。"""

    def __init__(self, values: dict | None):
        self.values = values or {}

    def get_state(self, config):  # noqa: ANN001
        return self


def test_messages_endpoint_prefers_checkpointer_state(tmp_path, biz, mem, auth, sess, cp):
    c = make_client(tmp_path, biz, mem, auth, sess, cp)
    sid = c.post("/api/sessions", json={}).json()["session_id"]
    mem.append_episode(sid, "u1@example.com", "user", "episodic 兜底不该出现")
    c.app.state.graph = _FakeGraph(
        {"messages": [HumanMessage("state 里的问题"), AIMessage("state 里的回答")]}
    )
    r = c.get(f"/api/sessions/{sid}/messages")
    assert r.status_code == 200
    body = r.json()
    assert body["kind"] == "chat"
    assert body["messages"] == [
        {"role": "user", "text": "state 里的问题"},
        {"role": "assistant", "text": "state 里的回答"},
    ]


def test_messages_endpoint_falls_back_to_episodic(tmp_path, biz, mem, auth, sess, cp):
    """classic 链路不写 messages（提取为空）→ 退 memory_episodic（链路无关）。"""
    c = make_client(tmp_path, biz, mem, auth, sess, cp)
    sid = c.post("/api/sessions", json={}).json()["session_id"]
    c.app.state.graph = _FakeGraph({})  # classic 会话：state 无 messages
    mem.append_episode(sid, "u1@example.com", "user", "明天放假吗")
    mem.append_episode(sid, "u1@example.com", "assistant", "不放假")
    r = c.get(f"/api/sessions/{sid}/messages")
    assert r.json()["messages"] == [
        {"role": "user", "text": "明天放假吗"},
        {"role": "assistant", "text": "不放假"},
    ]
