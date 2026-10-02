"""IQS 联网搜索适配层契约测试（P26/P29）：MockTransport 回放，不打真网。

覆盖：成功映射（hostname/日期/snippet 截断）/ 状态三分支（ok/empty/error）/
freshness 顶层 timeRange 映射 / 非法 freshness 不传 / URL 去重与 scheme 过滤 /
link 缺失丢弃 / 长度约束前置（不出网）/ numResults 封顶。
实测口径（2026-10-02 真调钉死）：timeRange 是顶层字段，四档枚举全生效；
advancedParams/queryContext 里的 timeRange 无效。
"""

from __future__ import annotations

import httpx
import pytest

from gewu.websearch import IQS_URL, MAX_RESULTS, SNIPPET_LIMIT, search


def _item(**kw) -> dict:
    base = {
        "title": "标题",
        "link": "https://example.com/a",
        "snippet": "摘要",
        "publishedTime": "2026-09-11T00:00:00+08:00",
        "hostname": "示例站",
        "rerankScore": 0.9,
    }
    base.update(kw)
    return base


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def _body_of(request: httpx.Request) -> dict:
    import json

    return json.loads(request.content.decode("utf-8"))


def test_success_maps_clean_fields_and_ok_status():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == IQS_URL
        assert request.headers["Authorization"] == "Bearer k1"
        assert _body_of(request)["query"] == "四六级报名时间"
        return httpx.Response(
            200,
            json={"pageItems": [_item(), _item(link="")]},  # link 空的应被丢弃
        )

    hits, status = search("k1", " 四六级报名时间 ", 5, client=_client(handler))
    assert status == "ok" and len(hits) == 1
    assert hits[0] == {
        "title": "标题",
        "url": "https://example.com/a",
        "snippet": "摘要",
        "site": "示例站",
        "date": "2026-09-11",
    }


def test_freshness_maps_top_level_time_range():
    bodies: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(_body_of(request))
        return httpx.Response(200, json={"pageItems": [_item()]})

    hits, status = search("k1", "tyloo 比赛结果", 5, "day", client=_client(handler))
    assert status == "ok"
    assert bodies[0]["timeRange"] == "OneDay"  # 顶层字段（非 advancedParams）
    search("k1", "tyloo 比赛结果", 5, "not-a-window", client=_client(handler))
    assert "timeRange" not in bodies[1]  # 非法档位静默不过滤


def test_dedup_by_canonical_url_and_scheme_filter():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "pageItems": [
                    _item(link="https://example.com/a?x=1#frag"),  # 带锚点变体
                    _item(link="https://example.com/a?x=1"),  # 同页去重
                    _item(link="ftp://example.com/bad"),  # 非 http(s) 丢弃
                    _item(link="javascript:alert(1)"),  # 危险 scheme 丢弃
                    _item(link="https://example.com/real"),
                ]
            },
        )

    hits, status = search("k1", "四六级报名时间", 5, client=_client(handler))
    assert status == "ok"
    assert [h["url"] for h in hits] == ["https://example.com/a?x=1", "https://example.com/real"]


def test_site_falls_back_to_domain_and_date_tolerant():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "pageItems": [
                    _item(hostname="", publishedTime=1757900000000),  # 毫秒兜底
                    _item(hostname="", publishedTime="garbage", link="https://example.com/b"),
                ]
            },
        )

    hits, _ = search("k1", "四六级报名时间", 5, client=_client(handler))
    assert hits[0]["site"] == "example.com"
    assert hits[0]["date"]  # 毫秒时间戳也能换算出日期
    assert hits[1]["date"] == ""  # 非法 publishedTime 静默为空串


def test_snippet_truncated_and_title_fallback():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"pageItems": [_item(snippet="长" * 2000, title="")]},
        )

    hits, _ = search("k1", "四六级报名时间", 5, client=_client(handler))
    assert len(hits[0]["snippet"]) == SNIPPET_LIMIT
    assert hits[0]["title"] == "(无标题)"


def test_status_branches_empty_and_error():
    def ok_empty(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"pageItems": []})

    assert search("k1", "四六级报名时间", 5, client=_client(ok_empty)) == ([], "empty")

    def bad(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"code": "Throttling"})

    assert search("k1", "四六级报名时间", 5, client=_client(bad)) == ([], "error")

    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("8s")

    assert search("k1", "四六级报名时间", 5, client=_client(boom)) == ([], "error")


@pytest.mark.parametrize("q", ["", "一", "a" * 101])
def test_query_length_guard_short_circuits_without_network(q):
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("长度非法不应出网")

    assert search("k1", q, 5, client=_client(handler)) == ([], "empty")
    assert search("", "正常长度的查询词", 5, client=_client(handler)) == ([], "empty")


def test_num_results_capped():
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(_body_of(request))
        return httpx.Response(200, json={"pageItems": []})

    search("k1", "四六级报名时间", 99, client=_client(handler))
    assert seen[0]["advancedParams"]["numResults"] == MAX_RESULTS
