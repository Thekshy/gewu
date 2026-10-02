"""P33-1 注册表一致性测试：三真相源归一后的注册表闸。

曾经的「约定」变测试闸——写工具漏登流程定义/触发词/角色任一处即红；
派生视图与旧常量形状等价断言锁死零行为变更。P33-2 补 resolve_flow/
flow_args 全分支（Q4 闸动态解析的机制闸）。
"""

from __future__ import annotations

from gewu.agent.tools import flow_args, flow_defs, resolve_flow, tools_for, write_tools

# 旧 txmeta.FLOW_DEFS 成员集（P33-1 零行为变更锚点）。
_LEGACY_FLOW_IDS = {"book_venue", "submit_leave", "cancel_booking", "approve_leave", "leave_status"}
# 旧 mw.WRITE_TOOLS 成员集。
_LEGACY_WRITE_TOOLS = {"book_venue", "cancel_booking", "submit_leave", "approve_leave"}


def _call(name: str, args: dict) -> dict:
    return {"name": name, "args": args, "id": "c1", "type": "function"}


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


# ---------- P33-2：resolve_flow / flow_args 全分支（Q4） ----------


def test_resolve_flow_dedicated_name():
    """专属工具名直查：写/读流程命中，非流程工具 None。"""
    assert resolve_flow(_call("book_venue", {})).name == "book_venue"
    assert resolve_flow(_call("leave_status", {})).name == "leave_status"
    assert resolve_flow(_call("search_knowledge", {"query": "x"})) is None
    assert resolve_flow(_call("nope", {})) is None


def test_resolve_flow_run_flow_unwrap():
    """run_flow 入口解开 flow_id：写/读流程命中；未知/缺失 id → None（闸放行）。"""
    spec = resolve_flow(_call("run_flow", {"flow_id": "submit_leave", "args": {}}))
    assert spec is not None and spec.name == "submit_leave"
    spec = resolve_flow(_call("run_flow", {"flow_id": "leave_status", "args": {}}))
    assert spec is not None and spec.name == "leave_status"
    assert resolve_flow(_call("run_flow", {"flow_id": "nope", "args": {}})) is None
    # 非 flow 工具 id 不可经 run_flow 收编（Q1 专属域不进 run_flow）
    assert resolve_flow(_call("run_flow", {"flow_id": "query_venues", "args": {}})) is None
    assert resolve_flow(_call("run_flow", {"args": {}})) is None
    assert resolve_flow(_call("run_flow", {})) is None


def test_flow_args_both_shapes():
    """专属=args 原样；run_flow=内层 slots（兼容 args 键）；畸形内层回 {}。"""
    assert flow_args(_call("book_venue", {"venue": "羽毛球馆"})) == {"venue": "羽毛球馆"}
    inner = {"flow_id": "book_venue", "slots": {"venue": "羽毛球馆", "date": "明天"}}
    assert flow_args(_call("run_flow", inner)) == {"venue": "羽毛球馆", "date": "明天"}
    legacy = {"flow_id": "book_venue", "args": {"venue": "羽毛球馆"}}
    assert flow_args(_call("run_flow", legacy)) == {"venue": "羽毛球馆"}
    assert flow_args(_call("run_flow", {"flow_id": "book_venue", "slots": "bad"})) == {}
    assert flow_args(_call("run_flow", {"flow_id": "book_venue"})) == {}
    assert flow_args(_call("book_venue", "bad")) == {}
