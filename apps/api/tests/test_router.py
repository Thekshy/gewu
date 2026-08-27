from app.agent.router import heuristic_route


def test_compound_question_routes_to_research():
    r = heuristic_route("我挂过一门课，还能申请转专业吗，转完学分怎么算")
    assert r.route == "research"


def test_long_question_routes_to_research():
    r = heuristic_route("请问学校对于本科生申请国际交换项目的绩点要求和语言成绩要求分别是什么？")
    assert r.route == "research"


def test_simple_fact_routes_to_factual():
    assert heuristic_route("图书馆几点开门").route == "factual"
    assert heuristic_route("校园卡丢了怎么补办").route == "factual"
