"""POST /api/chat 契约测试：校验序列、SSE 分帧、事件序、done.reason 单点语义。

P21 起：需登录（cookie）；role 服务端权威（请求体 role 废弃忽略）。
P22 起：session_id 必填且须为已登记属本人的会话（make_client 预建固定 id）。
P31-2 起：mode 枚举收窄 auto/react，direct 链路用例改写为 agent 脚本链路。
"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage

from gewu.api.app import create_app
from gewu.auth.store import AuthStore
from gewu.business.db import Business
from gewu.config import Settings
from gewu.memory import MemoryStore
from gewu.rag.store import DocInfo, Stats
from gewu.session.store import SessionStore
from tests.agent_fakes import FakeAgentLLM
from tests.conftest import make_logged_client
from tests.test_api import FakeRetriever, FakeStore


class FakeChatStream:
    def __init__(self, deltas: list[str], finish_reason: str = "stop") -> None:
        self._deltas = deltas
        self.finish_reason = finish_reason

    def __iter__(self):
        return iter(self._deltas)


class FakeChatLLM:
    """编排域 LLM 替身（admin 域共用）：主循环模型走空脚本（回答走兜底文案）。"""

    def __init__(self, deltas: list[str], finish_reason: str = "stop") -> None:
        self._deltas = deltas
        self._finish = finish_reason

    def has_key(self) -> bool:
        return True

    def chat(self, messages, *, small=False, json_mode=False, temperature=0.0, max_tokens=2048):
        return ""

    def chat_stream(self, messages, *, small=False, temperature=0.0, max_tokens=2048):
        return FakeChatStream(self._deltas, self._finish)

    def embed(self, texts):
        return [[0.0] * 2048]

    def agent_model(self, *, small: bool = False, max_tokens: int = 1200):
        from tests.agent_fakes import FakeToolChatModel  # noqa: PLC0415

        return FakeToolChatModel(responses=[])

    def record_usage(self, total_tokens: int) -> None:
        pass


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
    cp=None,
    llm=None,
    graph=None,
    logged: bool = True,
    trace_store=None,
) -> TestClient:
    (tmp_path / "usage.json").write_text("{}", encoding="utf-8")
    settings = Settings(llm_api_key="lk", embed_api_key="e", data_dir=tmp_path)
    kwargs = {} if cp is None else {"checkpointer": cp}
    if trace_store is not None:  # P27：e2e 断言 trace/spans 落库时注入测试库 store
        kwargs["trace"] = trace_store
    app = create_app(
        settings,
        store=FakeStore(Stats(1, 1, False), [DocInfo("d1", "t", "s", "u", 1)]),
        business=biz,
        memory=mem,
        auth=auth,
        sessions=sess,
        retriever=FakeRetriever([_hit()]),
        llm=llm or FakeAgentLLM(script=[AIMessage(content="开放时间是 7:30。")], has_key=True),
        **kwargs,
    )
    if graph is not None:
        app.state.graph = graph
    if not logged:
        return TestClient(app)
    client = make_logged_client(app, auth)
    # 预建固定 id 会话（用例直传；属主=登录用户 u1@example.com）
    for sid in ("s1", "t", "u", "e"):
        sess.create("u1@example.com", "chat", session_id=sid)
    return client


def _parse_sse(text: str) -> list[dict]:
    out = []
    for block in text.split("\n\n"):
        block = block.strip()
        if block.startswith("data: "):
            out.append(json.loads(block[len("data: ") :]))
    return out


def test_chat_requires_login(tmp_path: Path, biz, mem, auth, sess):
    c = make_client(tmp_path, biz, mem, auth, sess, logged=False)
    r = c.post("/api/chat", json={"question": "图书馆几点开门", "mode": "auto"})
    assert r.status_code == 401
    assert r.json()["detail"] == "未登录或会话已过期"


def test_chat_validation(tmp_path: Path, biz, mem, auth, sess):
    c = make_client(tmp_path, biz, mem, auth, sess)
    cases = [
        ({"question": ""}, "问题不能为空"),
        ({"question": "字" * 501}, "问题过长"),
        ({"question": "q", "mode": "bogus"}, "mode 必须为 auto/react"),
        ({"question": "q", "mode": "classic"}, "mode 必须为 auto/react"),
        ({"question": "q", "mode": "direct"}, "mode 必须为 auto/react"),
        ({"question": "q", "session_id": "s" * 65}, "session_id 过长（上限 64 字符）"),
        ({"question": 123}, "请求体不是合法 JSON"),
    ]
    for body, detail in cases:
        r = c.post("/api/chat", json=body)
        assert r.status_code == 422, body
        assert r.json()["detail"] == detail, body


def test_chat_session_id_required_and_registered(tmp_path: Path, biz, mem, auth, sess):
    """P22：default 缺省废弃（未传 422 给指引）；未登记/他人会话统一 404。"""
    c = make_client(tmp_path, biz, mem, auth, sess)
    r = c.post("/api/chat", json={"question": "图书馆几点开门", "mode": "auto"})
    assert r.status_code == 422
    assert r.json()["detail"] == "session_id 不能为空（请先 POST /api/sessions 创建会话）"

    r = c.post(
        "/api/chat",
        json={"question": "q", "mode": "auto", "session_id": "never-registered"},
    )
    assert r.status_code == 404
    assert r.json()["detail"] == "会话不存在"

    # 他人会话同样 404（不泄露存在性）：u2 建会话，u1 引用
    other = make_logged_client(c.app, auth, email="u2@example.com")
    sid = other.post("/api/sessions", json={}).json()["session_id"]
    r = c.post("/api/chat", json={"question": "q", "mode": "auto", "session_id": sid})
    assert r.status_code == 404


def test_chat_role_param_ignored_server_side_authority(tmp_path: Path, biz, mem, auth, sess):
    """role 废弃：自称 counselor 不再改变身份（服务端 users.role 权威）。"""
    c = make_client(tmp_path, biz, mem, auth, sess)  # 注册用户 role=student
    r = c.post(
        "/api/chat",
        json={
            "question": "图书馆几点开门",
            "mode": "auto",
            "session_id": "s1",
            "role": "counselor",
        },
    )
    assert r.status_code == 200  # 字段被忽略，不 422 不越权


def test_chat_sse_headers_and_frame(tmp_path: Path, biz, mem, auth, sess):
    c = make_client(tmp_path, biz, mem, auth, sess)
    r = c.post("/api/chat", json={"question": "图书馆几点开门", "mode": "auto", "session_id": "s1"})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/event-stream")
    assert r.headers["cache-control"] == "no-cache"
    assert r.headers["x-accel-buffering"] == "no"
    # 分帧格式：每个事件 data: {json}\n\n
    assert "\n\ndata: " in r.text
    events = _parse_sse(r.text)
    types = [e["type"] for e in events]
    # agent 单链事件序：status → answer_delta* → route(事后合成) → citations → done
    # （恰一个 done 收尾；citations 紧邻 done）
    assert types[0] == "status"
    assert "answer_delta" in types
    assert "route" in types
    assert types[-1] == "done"
    assert types.count("done") == 1
    assert types.index("citations") == len(types) - 2


def test_chat_done_reason_completed_and_citations(tmp_path: Path, biz, mem, auth, sess):
    c = make_client(tmp_path, biz, mem, auth, sess)
    r = c.post(
        "/api/chat",
        json={"question": "图书馆几点开门", "mode": "auto", "session_id": "t"},
    )
    events = _parse_sse(r.text)
    done = events[-1]
    assert done["type"] == "done"
    assert done["reason"] == "completed"
    assert isinstance(done["latency_ms"], int)
    answer = "".join(e["text"] for e in events if e["type"] == "answer_delta")
    assert answer == "开放时间是 7:30。"
    cites = [e for e in events if e["type"] == "citations"][0]
    assert cites["items"] == []  # 零工具轮：citations 通道空但事件照发（契约不缺帧）


def test_chat_truncated_marks_max_tokens(tmp_path: Path, biz, mem, auth, sess):
    long_text = "文" * 100
    llm = FakeAgentLLM(
        script=[AIMessage(content=long_text, response_metadata={"finish_reason": "length"})],
        has_key=True,
    )
    c = make_client(tmp_path, biz, mem, auth, sess, llm=llm)
    r = c.post("/api/chat", json={"question": "讲讲校历", "mode": "auto", "session_id": "t"})
    events = _parse_sse(r.text)
    assert events[-1]["reason"] == "max_tokens"
    assert any(e["type"] == "status" and "长度上限" in e["text"] for e in events)


def test_chat_utf8_raw_not_escaped(tmp_path: Path, biz, mem, auth, sess):
    c = make_client(tmp_path, biz, mem, auth, sess)
    r = c.post("/api/chat", json={"question": "图书馆几点开门", "mode": "auto", "session_id": "u"})
    assert "开放时间" in r.text  # UTF-8 原文（不转义为 \uXXXX）


def test_chat_error_path_emits_error_and_done(tmp_path: Path, biz, mem, auth, sess):
    class BoomGraph:
        def stream(self, *_a, **_k):
            raise RuntimeError("链路炸了")
            yield  # pragma: no cover

    c = make_client(tmp_path, biz, mem, auth, sess, graph=BoomGraph())
    r = c.post("/api/chat", json={"question": "q", "mode": "auto", "session_id": "e"})
    events = _parse_sse(r.text)
    assert events[-2]["type"] == "error"
    assert events[-2]["message"] == "服务内部错误，请稍后再试"  # P36：原文只进日志不外泄
    assert "链路炸了" not in r.text
    assert events[-1]["type"] == "done"
    assert events[-1]["reason"] == "error"


class FollowUpLLM(FakeAgentLLM):
    """检索一轮后作答的替身：route=factual 触发追问；chat() 吐合法追问 JSON。"""

    def __init__(self) -> None:
        super().__init__(
            script=[
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "search_knowledge",
                            "args": {"query": "图书馆 开放时间"},
                            "id": "c1",
                            "type": "function",
                        }
                    ],
                ),
                AIMessage(content="开放时间是 7:30。"),
            ],
            has_key=True,
        )
        self.follow_up_reply = (
            '["借的书过期了会罚款吗？", "一次最多能借几本书？", "可以在图书馆订自习室吗？"]'
        )

    def chat(self, messages, *, small=False, json_mode=False, temperature=0.0, max_tokens=2048):
        return self.follow_up_reply


def test_chat_follow_ups_after_done(tmp_path: Path, biz, mem, auth, sess):
    """P25：factual+completed → done 之后追发 follow_ups（事件序 done→follow_ups）。"""
    c = make_client(tmp_path, biz, mem, auth, sess, llm=FollowUpLLM())
    r = c.post(
        "/api/chat",
        json={"question": "图书馆几点开门", "mode": "auto", "session_id": "t"},
    )
    events = _parse_sse(r.text)
    types = [e["type"] for e in events]
    assert types.count("follow_ups") == 1
    assert types.index("done") == len(types) - 2
    assert types[-1] == "follow_ups"
    assert events[-1]["items"] == [
        "借的书过期了会罚款吗？",
        "一次最多能借几本书？",
        "可以在图书馆订自习室吗？",
    ]
    # 检索轮引用契约：citations 携带 FakeRetriever 命中
    cites = [e for e in events if e["type"] == "citations"][0]
    assert cites["items"][0]["doc_id"] == "doc-x"


def test_chat_no_follow_ups_when_route_refusal(tmp_path: Path, biz, mem, auth, sess):
    """refusal 轮不生成追问（Q5 门；替身 chat 有返回但路由不在白名单）。"""

    class BoomGraph:
        """首发 refusal 路由事件后正常收束的假图（不触发真实编排）。"""

        def __init__(self) -> None:
            self._n = 0

        def stream(self, *_a, **_k):
            from gewu.agent import events as ev  # noqa: PLC0415

            if self._n == 0:
                self._n += 1
                yield ((), ev.route_evt("refusal", "与知识库无关", False))
                yield ((), ev.answer_evt("抱歉，这不在校园制度范围内。"))
            else:
                return

    c = make_client(tmp_path, biz, mem, auth, sess, llm=FollowUpLLM(), graph=BoomGraph())
    r = c.post("/api/chat", json={"question": "今天股市行情", "mode": "auto", "session_id": "u"})
    events = _parse_sse(r.text)
    assert not any(e["type"] == "follow_ups" for e in events)


# ---------- P27：链路观测 e2e（chat 端点 → tracer → PG 两表） ----------


def test_chat_turn_writes_trace_and_tool_args_span(tmp_path, biz, mem, auth, sess, pg_dsn):
    """auto 轮走 web_search：trace 行 + llm/tool span 落库，工具 args 原样可见。

    覆盖 chat.py 的 tracer 生命周期与 contextvar re-set（P23 纪律）——
    span 丢失的症状是 trace 行有而 span 全无。
    """
    import psycopg
    from langchain_core.messages import AIMessage
    from langgraph.checkpoint.memory import MemorySaver

    from gewu.agent.agent import build_agent
    from gewu.agent.tools import tools_for
    from gewu.obs import TracerStore
    from tests.agent_fakes import FakeAgentLLM, FakeRetriever

    store = TracerStore(pg_dsn)
    store.wipe()
    settings = Settings(llm_api_key="lk", embed_api_key="ek", data_dir=tmp_path)
    llm = FakeAgentLLM(
        script=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "web_search",
                        "args": {"query": "10月1日 tyloo 比赛结果"},
                        "id": "c1",
                        "type": "function",
                    }
                ],
            ),
            AIMessage(content="根据 [1]，比赛结果如下。"),
        ]
    )
    web_hits = [{"title": "t", "url": "https://e.com/a", "snippet": "s", "site": "站", "date": ""}]
    graph = build_agent(
        settings,
        llm,
        FakeRetriever(),
        biz,
        tools_for(),
        web=lambda q, k=5, freshness="": (web_hits, "ok"),
        checkpointer=MemorySaver(),
    )
    client = make_client(tmp_path, biz, mem, auth, sess, graph=graph, trace_store=store)

    resp = client.post(
        "/api/chat",
        json={"question": "昨天tyloo的比赛结果如何", "mode": "auto", "session_id": "s1"},
    )
    assert resp.status_code == 200
    events = _parse_sse(resp.text)
    assert any(e.get("type") == "done" and e.get("reason") == "completed" for e in events)

    with psycopg.connect(pg_dsn, autocommit=True) as conn:
        tr = conn.execute(
            "SELECT id, question, route, reason FROM agent_trace ORDER BY id DESC LIMIT 1"
        ).fetchone()
        spans = conn.execute(
            "SELECT kind, name, input FROM agent_span WHERE trace_id = %s ORDER BY seq", (tr[0],)
        ).fetchall()
    assert tr[1] == "昨天tyloo的比赛结果如何" and tr[2] == "factual" and tr[3] == "completed"
    kinds = {(s[0], s[1]) for s in spans}
    assert ("tool", "web_search") in kinds  # 工具 args 原样落库（盲区根治点）
    assert ("llm", "agent") in kinds  # 主循环两次模型调用经 UsageRecord 接缝
    ws = next(s for s in spans if s[1] == "web_search")
    assert ws[2]["query"] == "10月1日 tyloo 比赛结果"  # psycopg 自动解 JSONB
    assert len(spans) >= 3  # 2×llm + 1×tool
    store.wipe()
