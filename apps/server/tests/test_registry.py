"""P33-1 注册表一致性测试：三真相源归一后的注册表闸。

曾经的「约定」变测试闸——写工具漏登流程定义/触发词/角色任一处即红；
派生视图与旧常量形状等价断言锁死零行为变更。
"""

from __future__ import annotations

from gewu.agent.tools import flow_defs, tools_for, write_tools

# 旧 txmeta.FLOW_DEFS 成员集（P33-1 零行为变更锚点）。
_LEGACY_FLOW_IDS = {"book_venue", "submit_leave", "cancel_booking", "approve_leave", "leave_status"}
# 旧 mw.WRITE_TOOLS 成员集。
_LEGACY_WRITE_TOOLS = {"book_venue", "cancel_booking", "submit_leave", "approve_leave"}


def test_registry_consistency():
    """每个注册表行：roles/triggers/domain 非空；写工具必有必填槽位（流程定义）。"""
    for t in tools_for().values():
        assert t.roles, f"{t.name}: roles 不能为空"
        assert t.triggers, f"{t.name}: triggers 不能为空（检索台阶数据就位）"
        assert t.domain, f"{t.name}: domain 不能为空"
        if not t.read_only:
            assert t.slots_required, f"{t.name}: 写工具必有 slots_required（否则静默失去确认门）"


def test_flow_defs_view_matches_legacy_shape():
    """flow_defs() 派生视图与旧 FLOW_DEFS 常量成员集与形状逐字等价。"""
    fd = flow_defs()
    assert set(fd) == _LEGACY_FLOW_IDS
    assert fd["book_venue"] == {
        "label": "预约场馆",
        "required": ["venue", "date", "slot"],
        "optional": ["purpose"],
    }
    assert fd["submit_leave"] == {
        "label": "请假申请",
        "required": ["leave_type", "start_date", "end_date", "reason"],
        "optional": [],
    }
    assert fd["cancel_booking"] == {"label": "取消预约", "required": ["booking_id"], "optional": []}
    assert fd["approve_leave"] == {"label": "批准请假", "required": ["ticket_id"], "optional": []}
    assert fd["leave_status"] == {"label": "请假单查询", "required": ["ticket_id"], "optional": []}


def test_write_tools_view_matches_legacy_set():
    assert write_tools() == _LEGACY_WRITE_TOOLS


def test_high_freq_reads_are_not_flows():
    """Q1：query_venues/my_bookings/pending_leaves 保留专属工具、不入流程域。"""
    for name in ("query_venues", "my_bookings", "pending_leaves"):
        t = tools_for()[name]
        assert t.read_only and not t.slots_required
