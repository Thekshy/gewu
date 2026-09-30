"""tx 域纯逻辑测试：工具识别/时段解析/天数/确认摘要/续轮启发式/槽位归一。"""

from __future__ import annotations

from gewu.agent.tx import (
    classify_reply,
    detect_tool,
    normalize_slot,
    parse_slot,
    phrase_days,
    slot_meta,
)
from gewu.business.db import Business
from gewu.config import Settings
from gewu.llm.service import LLMService


def _meta(tmp_path) -> dict:
    return slot_meta(Business(tmp_path / "b.db"))


def test_detect_tool_order_and_negative_guard():
    assert detect_tool("取消预约 VE-0001") == "cancel_booking"
    assert detect_tool("我的预约有哪些") == "my_bookings"
    assert detect_tool("待审批的请假有哪些") == "pending_leaves"
    assert detect_tool("帮我提交请假申请") == "submit_leave"
    assert detect_tool("帮我预约明天晚上的羽毛球馆") == "book_venue"
    # 负向双保险：预约心理咨询不选场馆工具（宁可落知识库也不误入办理流）
    assert detect_tool("我想预约心理咨询") == ""
    # 顺序契约：请假单号查询优先于 submit_leave 的宽匹配
    assert detect_tool("LV-0001 这个请假单批了没") == "leave_status"


def test_parse_slot():
    assert parse_slot("晚上") == "19:00-21:00"
    assert parse_slot("下午") == ""  # 多选词不唯一时不命中，须指明具体时段
    assert parse_slot("14:00-16:00") == "14:00-16:00"
    assert parse_slot("14点") == "14:00-16:00"
    assert parse_slot("上午") == ""  # 多选词


def test_phrase_days():
    assert phrase_days("请一天假") == 1
    assert phrase_days("请三天假") == 3
    assert phrase_days("请 5 天假") == 5
    assert phrase_days("请假") is None


def test_normalize_slot_venue_and_date(tmp_path):
    meta = _meta(tmp_path)
    norm, ok = normalize_slot(meta, "venue", "羽毛球馆")
    assert ok and norm == "venue-badminton"
    norm, ok = normalize_slot(meta, "date", "明天")
    assert ok and norm  # 相对今天换算成 ISO（确定性）
    norm, ok = normalize_slot(meta, "purpose", "院队训练")
    assert ok and norm == "院队训练"


def test_build_confirm_leave(tmp_path):
    from gewu.agent.tx import build_confirm

    b = Business(tmp_path / "b.db")
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


def test_classify_reply_heuristic(tmp_path):
    b = Business(tmp_path / "b.db")
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
