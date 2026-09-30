"""cascade 级联路由测试：L0 规则 / L1 双阈值+margin / L2 兜底 / refusal 安全网 / 启发式。"""

from __future__ import annotations

import pytest

from gewu.agent.routing import (
    CascadeRouter,
    fill_policy,
    heuristic_route,
    parse_route_scores,
    react_plan_signal,
)


class FakeRouterLLM:
    """可编程 L1/L2 输出的路由替身。"""

    def __init__(self, l1_raw: str = "", l2_raw: str = "", fail_l1: bool = False) -> None:
        self._l1_raw = l1_raw
        self._l2_raw = l2_raw
        self._fail_l1 = fail_l1
        self.calls: list[tuple[str, bool]] = []  # (prompt_head, small)

    def has_key(self) -> bool:
        return True

    def chat(self, messages, *, small=False, json_mode=False, temperature=0.0, max_tokens=2048):
        self.calls.append((messages[0][1][:12], small))
        if messages[0][1].startswith("你是校园问答系统「格物」的问题分类器"):
            if self._fail_l1:
                raise RuntimeError("L1 挂了")
            return self._l1_raw
        return self._l2_raw


L1_HIGH_FACTUAL = '{"scores":{"factual":0.9,"research":0.05,"transaction":0.03,"hybrid":0.01,"refusal":0.01},"reason":"事实查询"}'
L1_UNCERTAIN = '{"scores":{"factual":0.4,"research":0.35,"transaction":0.15,"hybrid":0.05,"refusal":0.05},"reason":"拿不准"}'
L1_REFUSAL_HIGH = '{"scores":{"factual":0.05,"research":0.05,"transaction":0.05,"hybrid":0.02,"refusal":0.83},"reason":"无关"}'
L2_FACTUAL = '{"route":"factual","reason":"校园政策问题"}'


def test_parse_route_scores_orders_and_ties():
    dec, top1, top2 = parse_route_scores(L1_HIGH_FACTUAL)
    assert dec["route"] == "factual" and top1 == 0.9 and top2 == 0.05
    assert dec["reason"] == "事实查询"


def test_parse_route_scores_string_scores_fall_back_to_heuristic():
    # 字符串数字不参与概率判定（宁可走兜底）
    dec, top1, _ = parse_route_scores('{"scores":{"factual":"0.9"},"reason":"x"}')
    assert dec["layer"] == "heuristic-fallback"
    assert top1 == 0.5


def test_parse_route_scores_tie_keeps_route_order():
    raw = '{"scores":{"refusal":0.5,"factual":0.5},"reason":"r"}'
    dec, _, _ = parse_route_scores(raw)
    assert dec["route"] == "factual"  # 平局按固定类别序取先


def test_heuristic_route_ordering():
    assert heuristic_route("帮我预约明天的羽毛球馆")["route"] == "transaction"
    assert heuristic_route("帮我预约明天羽毛球馆，顺便问下有什么要求")["route"] == "hybrid"
    assert heuristic_route("请假一周需要找谁审批")["route"] == "factual"  # 只咨询政策
    assert heuristic_route("转专业之后原课程绩点怎么算，同时影响保研吗")["route"] == "research"
    assert heuristic_route("图书馆几点开门")["route"] == "factual"


def test_fill_policy():
    dec = fill_policy(
        {
            "route": "transaction",
            "confidence": 1.0,
            "layer": "",
            "reason": "",
            "by_llm": False,
            "pre_rag": False,
            "toolset": [],
            "model_tier": "",
        }
    )
    assert dec["model_tier"] == "small"
    assert "book_venue" in dec["toolset"] and "approve_leave" in dec["toolset"]
    assert (
        fill_policy(
            {
                "route": "research",
                "confidence": 0,
                "layer": "",
                "reason": "",
                "by_llm": False,
                "pre_rag": False,
                "toolset": [],
                "model_tier": "",
            }
        )["model_tier"]
        == "flagship"
    )


def test_cascade_l0_exact_transaction_and_hybrid():
    llm = FakeRouterLLM()
    router = CascadeRouter(llm)
    dec = router.route("帮我预约明天晚上的羽毛球馆")
    assert dec["route"] == "transaction" and dec["layer"] == "L0-rule" and dec["confidence"] == 1.0
    assert dec["by_llm"] is False
    dec2 = router.route("帮我预约明天羽毛球馆，顺便问下预约有什么要求")
    assert dec2["route"] == "hybrid" and dec2["layer"] == "L0-rule"
    assert llm.calls == []  # L0 命中省一次 LLM


def test_cascade_l1_high_confidence_direct():
    llm = FakeRouterLLM(l1_raw=L1_HIGH_FACTUAL)
    dec = CascadeRouter(llm).route("图书馆几点开门")
    assert dec["route"] == "factual"
    assert dec["layer"] == "L1-llm" and dec["by_llm"] is True
    assert len(llm.calls) == 1 and llm.calls[0][1] is True  # 走小模型


def test_cascade_l1_uncertain_goes_l2_main():
    llm = FakeRouterLLM(l1_raw=L1_UNCERTAIN, l2_raw=L2_FACTUAL)
    dec = CascadeRouter(llm).route("这个问题有点复杂")
    assert dec["route"] == "factual"
    assert dec["layer"] == "L2-main" and dec["confidence"] == 0.9
    assert len(llm.calls) == 2 and llm.calls[1][1] is False  # L2 走主模型


def test_cascade_l2_uncertain_falls_to_flagship_factual():
    llm = FakeRouterLLM(l1_raw=L1_UNCERTAIN, l2_raw="not-json")
    dec = CascadeRouter(llm).route("这个问题有点复杂")
    assert dec["route"] == "factual" and dec["layer"] == "L2-uncertain"
    assert dec["model_tier"] == "flagship" and dec["pre_rag"] is True


def test_cascade_refusal_safety_net_escalates():
    """flash 高置信误判 refusal + 校园领域词 → 不采信，升级 L2 复核（真实误拒案例）。"""
    llm = FakeRouterLLM(l1_raw=L1_REFUSAL_HIGH, l2_raw=L2_FACTUAL)
    dec = CascadeRouter(llm).route("我的情况符合转专业条件吗")
    assert dec["route"] == "factual" and dec["layer"] == "L2-main"


def test_cascade_refusal_without_domain_word_accepted():
    llm = FakeRouterLLM(l1_raw=L1_REFUSAL_HIGH)
    dec = CascadeRouter(llm).route("今天A股行情怎么样")
    assert dec["route"] == "refusal" and dec["layer"] == "L1-llm"


def test_cascade_l1_failure_degrades_heuristic_then_l2():
    """L1 失败 → 启发式(hConf=0.5 落灰度区) → 仍走 L2；L2 也失败 → L2-uncertain（Go 同构）。"""
    llm = FakeRouterLLM(fail_l1=True, l2_raw="not-json")
    dec = CascadeRouter(llm).route("可以预约羽毛球馆吗")
    assert dec["layer"] == "L2-uncertain" and dec["route"] == "factual"
    llm2 = FakeRouterLLM(fail_l1=True, l2_raw='{"route":"transaction","reason":"办理"}')
    dec2 = CascadeRouter(llm2).route("可以预约羽毛球馆吗")
    assert dec2["layer"] == "L2-main" and dec2["route"] == "transaction"


def test_cascade_no_key_uses_heuristic():
    class NoKeyLLM:
        def has_key(self) -> bool:
            return False

    dec = CascadeRouter(NoKeyLLM()).route("图书馆几点开门")
    assert dec["layer"] == "heuristic-fallback"


def test_react_plan_signal():
    assert react_plan_signal("帮我安排下周的羽毛球场地")
    assert not react_plan_signal("帮我预约明天晚上的羽毛球馆")


@pytest.mark.parametrize(
    "conf,margin,expect_layer",
    [
        (0.85, 0.3, "L1-llm"),
        (0.6, 0.1, "L2-main"),  # 置信够但 margin 不足 → 纠缠升级
        (0.5, 0.4, "L2-main"),  # 置信不足 → 升级
    ],
)
def test_cascade_thresholds(conf, margin, expect_layer):
    raw = (
        '{"scores":{"factual":'
        + str(conf)
        + ',"research":'
        + str(conf - margin)
        + ',"transaction":0.0,"hybrid":0.0,"refusal":0.0},"reason":"r"}'
    )
    llm = FakeRouterLLM(l1_raw=raw, l2_raw=L2_FACTUAL)
    dec = CascadeRouter(llm).route("普通问题")
    assert dec["layer"] == expect_layer
