"""P42 内容审查拒绝分类测试：形态识别、误判防线、LLMService 200 形态拦截。

全程无网络、无任何真实敏感词——样例一律用审查类通用表述与占位文案，
「拒绝动作 × 审查话题」正反例覆盖小模型族四类合法输出（改写串/精排分数
JSON/追问列表/政策术语）。
"""

from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage

from gewu.config import Settings
from gewu.llm.safety import (
    ContentFilterError,
    is_blocked_completion,
    is_content_filter_error,
    is_filter_finish,
    looks_like_error_content,
)
from gewu.llm.service import LLMService


class FakeAPIStatusError(Exception):
    """仿 openai.APIStatusError 形状（status_code/body），不引真 SDK。"""

    def __init__(self, status_code: int, body=None, message: str = "") -> None:
        super().__init__(message or "provider error")
        self.status_code = status_code
        self.body = body


class FakeBoundModel:
    """chat() 用假模型：bind 吞参、invoke 返回预置 AIMessage。"""

    def __init__(self, resp: AIMessage) -> None:
        self._resp = resp

    def bind(self, **_kwargs):  # noqa: ARG002
        return self

    def invoke(self, _msgs) -> AIMessage:
        return self._resp


def _svc_with(resp: AIMessage, **kw) -> LLMService:
    svc = LLMService(Settings(**kw))
    svc._models[False] = FakeBoundModel(resp)  # noqa: SLF001 - 测试注入双模型缓存
    return svc


# ---------- is_content_filter_error：异常形态 ----------


def test_unified_exception_instance_is_positive():
    assert is_content_filter_error(ContentFilterError("统一异常"))


def test_zhipu_1301_body_code_matches():
    e = FakeAPIStatusError(400, body={"error": {"code": "1301", "message": "（审查类提示语）"}})
    assert is_content_filter_error(e)


def test_contentfilter_field_shape_matches():
    e = FakeAPIStatusError(
        400,
        body={
            "error": {"code": "", "message": "x"},
            "contentFilter": [{"role": "user", "level": 1}],
        },
    )
    assert is_content_filter_error(e)


def test_word_face_on_4xx_matches():
    assert is_content_filter_error(FakeAPIStatusError(400, message="输入包含敏感内容"))
    assert is_content_filter_error(FakeAPIStatusError(403, message="内容审核未通过"))


def test_non_filter_codes_and_errors_do_not_match():
    # 智谱 13xx 其余为限流/并发、1000 段为鉴权——不得误判为内容审查
    assert not is_content_filter_error(
        FakeAPIStatusError(400, body={"error": {"code": "1302", "message": "并发上限"}})
    )
    assert not is_content_filter_error(
        FakeAPIStatusError(401, body={"error": {"code": "1000", "message": "API key 无效"}})
    )
    assert not is_content_filter_error(FakeAPIStatusError(429, message="rate limited"))
    assert not is_content_filter_error(FakeAPIStatusError(400, message="模型名称不存在"))
    assert not is_content_filter_error(ValueError("普通错误"))
    assert not is_content_filter_error(RuntimeError("连接超时"))


# ---------- is_filter_finish：finish 值形态 ----------


def test_is_filter_finish_two_canonical_values():
    for r in ("content_filter", "sensitive"):
        assert is_filter_finish(r)
    for r in ("stop", "length", "tool_calls", "", "error"):
        assert not is_filter_finish(r)


# ---------- looks_like_error_content：200 文案形态 ----------


def test_error_content_shapes_match():
    assert looks_like_error_content('{"error": {"code": "1301"}}')  # JSON error 形状
    assert looks_like_error_content("系统检测到输入或生成内容可能包含不安全或敏感内容")
    assert looks_like_error_content("您的请求无法处理：包含违规内容")


def test_error_content_never_hits_legit_small_model_output():
    assert not looks_like_error_content('{"scores": [8, 5, 0]}')  # 精排合法输出
    assert not looks_like_error_content("图书馆 开放时间 外借 上限 元")  # 改写合法输出
    assert not looks_like_error_content("学生违规用电处分规定")  # 政策术语：话题词单项命中放行
    # 追问合法输出：动作词单项命中放行
    assert not looks_like_error_content("无法按时注册选课怎么办")
    assert not looks_like_error_content("")
    assert not looks_like_error_content('{"subquestions": ["转专业条件"]}')  # 深研拆解合法输出


def test_blocked_completion_or_logic():
    assert is_blocked_completion("sensitive", "")
    assert is_blocked_completion("stop", '{"error": 1}')
    assert not is_blocked_completion("stop", "正常回答文本")


# ---------- LLMService.chat：200 形态拦截（②，开关门禁） ----------


def test_chat_raises_on_sensitive_finish():
    resp = AIMessage(content="", response_metadata={"finish_reason": "sensitive"})
    with pytest.raises(ContentFilterError):
        _svc_with(resp).chat([("user", "q")])


def test_chat_raises_on_content_filter_finish():
    resp = AIMessage(content="半截", response_metadata={"finish_reason": "content_filter"})
    with pytest.raises(ContentFilterError):
        _svc_with(resp).chat([("user", "q")])


def test_chat_raises_on_error_text_content():
    resp = AIMessage(content='{"error": {"code": "1301"}}')
    with pytest.raises(ContentFilterError):
        _svc_with(resp).chat([("user", "q")])


def test_chat_switch_off_returns_raw_text():
    """开关=0 回退改前行为：可疑文本原样返回（P40 污染场景重现）。"""
    resp = AIMessage(content='{"error": {"code": "1301"}}')
    svc = _svc_with(resp, content_filter_fallback=False)
    assert svc.chat([("user", "q")]) == '{"error": {"code": "1301"}}'


def test_chat_normal_output_passes_through():
    resp = AIMessage(content="开放时间是 7:30。", response_metadata={"finish_reason": "stop"})
    assert _svc_with(resp).chat([("user", "q")]) == "开放时间是 7:30。"
