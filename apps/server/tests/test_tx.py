"""槽位元数据与 resume 桥纯逻辑测试：时段解析/槽位归一/确认摘要/续轮启发式。

P31-2：classic 流程用例（detect_tool/phrase_days/流程推进）随链路退役删除；
元数据迁 txmeta，classify_reply 迁 resume。
"""

from __future__ import annotations

from gewu.agent.resume import classify_reply
from gewu.agent.txmeta import normalize_slot, parse_slot, slot_meta
from gewu.config import Settings
from gewu.llm.service import LLMService


def test_parse_slot():
    assert parse_slot("晚上") == "19:00-21:00"
    assert parse_slot("下午") == ""  # 多选词不唯一时不命中，须指明具体时段
    assert parse_slot("14:00-16:00") == "14:00-16:00"
    assert parse_slot("14点") == "14:00-16:00"
    assert parse_slot("上午") == ""  # 多选词


def test_normalize_slot_venue_and_date(biz):
    meta = slot_meta(biz)
    norm, ok = normalize_slot(meta, "venue", "羽毛球馆")
    assert ok and norm == "venue-badminton"
    norm, ok = normalize_slot(meta, "date", "明天")
    assert ok and norm  # 相对今天换算成 ISO（确定性）
    norm, ok = normalize_slot(meta, "purpose", "院队训练")
    assert ok and norm == "院队训练"


def test_build_confirm_leave(biz):
    from gewu.agent.txmeta import build_confirm

    b = biz
    state = {
        "tx_tool": "submit_leave",
        "tx_slots": {
            "leave_type": "病假",
            "start_date": "2026-10-01",
            "end_date": "2026-10-05",
            "reason": "生病",
        },
    }
    pa, text, note = build_confirm(state, b)
    assert pa["type"] == "pending_action"
    assert pa["tool"] == "submit_leave"
    assert pa["args"]["共"] == "5 天"
    assert pa["args"]["审批"] == "学院（按学校规定）"
    assert note == "病假超过 3 天建议附医院证明。"
    assert "请确认请假申请信息" in text


def test_classify_reply_heuristic(biz):
    b = biz
    meta = slot_meta(b)
    state = {
        "tx_phase": "confirm",
        "tx_slots": {"venue": "venue-badminton", "date": "2026-10-01"},
        "tx_last_asked": "",
    }
    # 无 key 的 LLMService → 启发式
    llm = LLMService(Settings())
    assert classify_reply(llm, meta, "确认", state) == "continue"
    assert classify_reply(llm, meta, "算了不办了", state) == "cancel"
    assert classify_reply(llm, meta, "图书馆几点开门？", state) == "new_topic"
    assert classify_reply(llm, meta, "2026-10-02", state) == "continue"  # 值得当修改
