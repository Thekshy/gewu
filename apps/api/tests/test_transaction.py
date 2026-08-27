"""知行执行层端到端（离线：无 LLM key，全走启发式路由 + 确定性槽位解析）。"""

import datetime as dt

from app.agent.pipeline import run_chat
from app.agent.session import sessions
from app.agent.tools import business
from app.dates import today


def setup_function():
    business.reset()
    sessions._data.clear()


def ask(sid, text, role="student"):
    return list(run_chat(text, "auto", session_id=sid, role=role))


def first(events, etype):
    return next((e for e in events if e["type"] == etype), None)


def any_event(events, etype):
    return any(e["type"] == etype for e in events)


TOMORROW = (today() + dt.timedelta(days=1)).isoformat()


def test_book_venue_happy_path_one_shot():
    events = ask("t1", "帮我预约明天晚上的羽毛球馆打班级比赛")
    assert first(events, "route")["route"] == "transaction"
    pending = first(events, "pending_action")
    assert pending and pending["tool"] == "book_venue"
    assert not any_event(events, "slot_question")  # 信息一次给全，直接进确认

    events2 = ask("t1", "确认")
    result = first(events2, "action_result")
    assert result["success"] and result["receipt"].startswith("VE-")
    bookings = [b for b in business.all_bookings() if b["venue"] == "羽毛球馆"]
    assert bookings and bookings[0]["date"] == TOMORROW and bookings[0]["slot"] == "19:00-21:00"


def test_book_venue_guided_clarification():
    sid = "t2"
    events = ask(sid, "帮我预约研讨间301")
    q = first(events, "slot_question")
    assert q and q["slot"] == "date"

    events = ask(sid, "明天下午")
    q = first(events, "slot_question")
    assert q and q["slot"] == "slot"  # 下午有两个时段，需指明

    events = ask(sid, "14:00-16:00")
    assert first(events, "pending_action")

    events = ask(sid, "确认")
    assert first(events, "action_result")["success"]


def test_conflict_recovery():
    sid = "t3"
    day_after = (today() + dt.timedelta(days=2)).isoformat()
    ask(sid, "帮我预约后天上午10点到12点的研讨间302自习")
    ask(sid, "确认")

    ask(sid, "再帮我预约后天上午10点到12点的研讨间302，和同学讨论")
    events = ask(sid, "确认")
    failed = first(events, "action_result")
    assert not failed["success"]
    q = first(events, "slot_question")
    assert q and q["slot"] == "slot" and "14:00-16:00" in q["question"]

    ask(sid, "那就 14:00 到 16:00 吧")
    events = ask(sid, "确认")
    assert first(events, "action_result")["success"]

    slots = {b["slot"] for b in business.all_bookings() if b["date"] == day_after}
    assert slots == {"10:00-12:00", "14:00-16:00"}


def test_leave_days_phrase_and_approver():
    sid = "t4"
    events = ask(sid, "帮我请下周一到下周二的事假")
    q = first(events, "slot_question")
    assert q and q["slot"] == "reason"

    events = ask(sid, "家中有事")
    pending = first(events, "pending_action")
    assert pending["args"].get("共") == "2 天"

    events = ask(sid, "确认")
    result = first(events, "action_result")
    assert result["success"]
    ticket = business.all_tickets()[-1]
    assert ticket["days"] == 2 and ticket["approver"] == "辅导员"


def test_leave_one_day_phrase():
    sid = "t5"
    events = ask(sid, "帮我提交明天一天的病假申请")
    # 明天 + 一天 → 起止都齐；缺的只有事由
    q = first(events, "slot_question")
    assert q and q["slot"] == "reason"
    events = ask(sid, "发烧需要休息")
    events = ask(sid, "确认")
    assert first(events, "action_result")["success"]
    assert business.all_tickets()[-1]["days"] == 1


def test_permission_denied_for_student():
    events = ask("t6", "帮我看下现在有哪些待审批的请假", role="student")
    result = first(events, "action_result")
    assert result and not result["success"] and "无权" in result["message"]


def test_topic_switch_abandons_flow():
    sid = "t7"
    ask(sid, "帮我预约研讨间301")  # 进入追问日期阶段
    events = ask(sid, "图书馆几点开门")
    assert first(events, "route")["route"] == "factual"
    assert not any_event(events, "action_result")
    assert sessions.get(sid) is None  # 流程已放弃


def test_cancel_during_confirm():
    sid = "t8"
    ask(sid, "帮我预约明天晚上的篮球场训练")
    events = ask(sid, "算了不约了")
    assert "已取消" in "".join(e.get("text", "") for e in events if e["type"] == "answer_delta")
    assert sessions.get(sid) is None


def test_read_tool_direct_answer():
    events = ask("t9", "现在有哪些场馆可以预约")
    result = first(events, "action_result")
    assert result["success"]
    assert "羽毛球馆" in result["message"]
