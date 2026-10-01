"""P25-1 追问生成单测：三层守卫四路 + Q5 条件矩阵 + 生成门面（替身/超时/异常）。

SSE 端点级的 follow_ups 事件序测试在 test_chat_api.py（follow_ups 在 done 之后）。
"""

from __future__ import annotations

import json
import time

from gewu.agent.followups import (
    generate_follow_ups,
    guard_follow_ups,
    should_generate,
)


class FakeLLM:
    def __init__(self, reply=None, delay: float = 0.0, raise_err: bool = False) -> None:
        self.reply = reply
        self._delay = delay
        self._raise = raise_err
        self.calls: list[tuple] = []

    def chat(self, messages, *, small=False, temperature=0.0, max_tokens=2048, **_kw):
        self.calls.append((messages, small))
        if self._delay:
            time.sleep(self._delay)
        if self._raise:
            raise RuntimeError("llm down")
        return self.reply


# ---------- guard_follow_ups（四路） ----------


def test_guard_valid_json_array():
    raw = '["借的书过期了会罚款吗？", "一次最多能借几本书？", "可以在图书馆订自习室吗？"]'
    out = guard_follow_ups(raw, "图书馆几点开门")
    assert out == ["借的书过期了会罚款吗？", "一次最多能借几本书？", "可以在图书馆订自习室吗？"]


def test_guard_dirty_json_and_code_fence():
    # 无数组结构 / 围栏包裹 / JSON 对象（非数组）→ 全弃
    assert guard_follow_ups("这不是数组", "q") == []
    assert guard_follow_ups('```json\n["问题一是什么呀？", "问题二是什么呀？"]\n```', "q") == [
        "问题一是什么呀？",
        "问题二是什么呀？",
    ]
    assert guard_follow_ups('{"items": []}', "q") == []


def test_guard_filters_length_dup_and_original():
    raw = json.dumps(
        [
            "短",
            "这条问题超过了三十个字符的长度上限所以应该会被过滤器直接给丢弃掉",
            "图书馆几点开门",
            "借的书过期了会罚款吗？",
            "借的书过期了会罚款吗？",
            "可以在图书馆订自习室吗？",
        ]
    )
    out = guard_follow_ups(raw, "图书馆几点开门")
    assert out == ["借的书过期了会罚款吗？", "可以在图书馆订自习室吗？"]


def test_guard_too_few_after_filter():
    # 过滤后只剩 1 条 → 弃（不做残缺单条）
    assert guard_follow_ups('["唯一一条合格的追问问题"]', "q") == []


# ---------- should_generate（Q5 条件矩阵） ----------


def test_should_generate_matrix():
    ok = ("factual", "completed", False)
    assert should_generate(*ok) is True
    assert should_generate("research", "completed", False) is True
    assert should_generate("hybrid", "completed", False) is True
    # 路由不在白名单
    assert should_generate("refusal", "completed", False) is False
    assert should_generate("transaction", "completed", False) is False
    assert should_generate("chitchat", "completed", False) is False
    assert should_generate("", "completed", False) is False
    # 非正常收尾
    assert should_generate("factual", "max_tokens", False) is False
    assert should_generate("factual", "error", False) is False
    assert should_generate("factual", "aborted", False) is False
    # HITL 悬停（tx 确认门在等用户）
    assert should_generate("factual", "completed", True) is False


# ---------- generate_follow_ups（门面：小模型通道/超时/异常） ----------


def test_generate_ok_via_small_channel():
    llm = FakeLLM(reply='["借的书过期了会罚款吗？", "一次最多能借几本书？"]')
    out = generate_follow_ups(
        llm, "u1@example.com", "图书馆几点开门", "7:30 开门。", ["图书馆管理办法"]
    )
    assert out == ["借的书过期了会罚款吗？", "一次最多能借几本书？"]
    assert llm.calls and llm.calls[0][1] is True  # 走 small 通道


def test_generate_timeout_silent():
    llm = FakeLLM(reply='["借的书过期了会罚款吗？", "一次最多能借几本书？"]', delay=0.4)
    out = generate_follow_ups(llm, "u1@example.com", "q", "a", [], timeout_s=0.05)
    assert out == []


def test_generate_error_silent():
    llm = FakeLLM(raise_err=True)
    assert generate_follow_ups(llm, "u1@example.com", "q", "a", []) == []


def test_generate_dirty_reply_guarded():
    llm = FakeLLM(reply="抱歉我不能回答")
    assert generate_follow_ups(llm, "u1@example.com", "q", "a", []) == []
