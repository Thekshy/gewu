"""GuardMiddleware 单测（P17-1）：三 decision 全路径 + 快路径 + fail-open。"""

from __future__ import annotations

from gewu.agent.guardrails import GREETING_RE, classify_guard, guard_update
from gewu.agent.prompts import REFUSAL_ANSWER
from tests.agent_fakes import FakeAgentLLM


def test_greeting_regex_fast_paths():
    for q in ["你好", "你好呀", "在吗？", "你是谁", "你能做什么", "谢谢！", "再见", "hello", "hi~"]:
        assert GREETING_RE.match(q), q
    for q in ["你好，帮我预约羽毛球场", "帮我写代码", "图书馆几点开门", "转专业条件"]:
        assert not GREETING_RE.match(q), q


def test_classify_allow_meta_block():
    llm = FakeAgentLLM(
        chat_replies=[
            '{"decision":"allow","intent":"factual","reply":""}',
            '{"decision":"meta","intent":"chitchat","reply":"你好呀同学！"}',
            '{"decision":"block","intent":"refusal","reply":""}',
        ],
        has_key=True,
    )
    v = classify_guard(llm, "转专业要什么条件")
    assert v["decision"] == "allow" and v["intent"] == "factual"
    v = classify_guard(llm, "早上好呀")
    assert v["decision"] == "meta" and v["reply"] == "你好呀同学！"
    v = classify_guard(llm, "今天A股怎么样")
    assert v["decision"] == "block"


def test_classify_fail_open():
    # 无 key / 异常 / 非法输出 / meta 缺 reply → 全部 fail-open 放行
    assert classify_guard(FakeAgentLLM(has_key=False), "随便什么")["decision"] == "allow"
    boom = FakeAgentLLM(has_key=True)
    boom.chat = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("网络炸了"))
    assert classify_guard(boom, "随便什么")["decision"] == "allow"
    bad = FakeAgentLLM(chat_replies=["不是 JSON"], has_key=True)
    assert classify_guard(bad, "随便什么")["decision"] == "allow"
    no_reply = FakeAgentLLM(
        chat_replies=['{"decision":"meta","intent":"chitchat","reply":""}'], has_key=True
    )
    assert classify_guard(no_reply, "嗨")["decision"] == "allow"


def test_middleware_greeting_zero_llm_allow():
    llm = FakeAgentLLM(has_key=True)  # 有 key 也不该被调用（快路径）
    out = guard_update(llm, "你好")
    assert out == {"guard_action": "allow"}


def test_middleware_block_short_circuits_to_refusal():
    llm = FakeAgentLLM(
        chat_replies=['{"decision":"block","intent":"refusal","reply":""}'], has_key=True
    )
    out = guard_update(llm, "今天A股大盘怎么样")
    assert out["jump_to"] == "end"
    assert out["guard_action"] == "block"
    assert out["messages"][0].content == REFUSAL_ANSWER


def test_middleware_meta_injects_reply():
    llm = FakeAgentLLM(
        chat_replies=[
            '{"decision":"meta","intent":"chitchat","reply":"你好！我可以帮你查政策、约场馆。"}'
        ],
        has_key=True,
    )
    out = guard_update(llm, "早上好呀同学")
    assert out["jump_to"] == "end"
    assert "约场馆" in out["messages"][0].content


def test_middleware_allow_passes_through():
    llm = FakeAgentLLM(
        chat_replies=['{"decision":"allow","intent":"transaction","reply":""}'], has_key=True
    )
    assert guard_update(llm, "帮我预约明天的羽毛球场") == {"guard_action": "allow"}


def test_guard_skips_classification_in_conversation():
    # 会话进行中（如办理槽位收集的短回复轮）：只放行，不交给安检分类
    llm = FakeAgentLLM(
        chat_replies=['{"decision":"meta","intent":"chitchat","reply":"被吃掉"}'], has_key=True
    )
    assert guard_update(llm, "研讨间301", in_conversation=True) == {"guard_action": "allow"}
    assert llm._replies  # LLM 未被调用
