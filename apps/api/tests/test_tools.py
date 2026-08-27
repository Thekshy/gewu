from app.agent import tools
from app.agent.tools import business


def setup_function():
    business.reset()


def test_student_cannot_list_pending_leaves():
    res = tools.call("pending_leaves", {}, role="student", user="demo-student")
    assert not res["ok"] and res["error"] == "permission"


def test_counselor_can_list_pending_leaves():
    business.submit_leave("demo-student", "事假", "2099-01-01", "2099-01-02", "家事")
    res = tools.call("pending_leaves", {}, role="counselor", user="demo-counselor")
    assert res["ok"] and "LV-" in res["message"]


def test_unknown_tool():
    res = tools.call("drop_tables", {}, role="student", user="u")
    assert not res["ok"] and res["error"] == "unknown_tool"


def test_tool_descriptions_filtered_by_role():
    student = tools.tool_descriptions("student")
    counselor = tools.tool_descriptions("counselor")
    assert "pending_leaves" not in student
    assert "pending_leaves" in counselor
