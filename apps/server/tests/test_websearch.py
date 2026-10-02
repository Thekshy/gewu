"""IQS 联网搜索适配层契约测试（P26）：MockTransport 回放，不打真网。

覆盖：成功映射（hostname/日期/snippet 截断）/ 空 pageItems / 非 2xx /
超时 / link 缺失丢弃 / 长度约束前置（不出网）/ numResults 封顶。
实测口径（2026-10-01 真调钉死）：publishedTime 为 ISO 字符串。
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


def test_success_maps_clean_fields():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == IQS_URL
        assert request.headers["Authorization"] == "Bearer k1"
        assert _body_of(request)["query"] == "四六级报名时间"
        return httpx.Response(
            200,
            json={"pageItems": [_item(), _item(link="")]},  # link 空的应被丢弃
        )

    hits = search("k1", " 四六级报名时间 ", 5, client=_client(handler))
    assert len(hits) == 1
    h = hits[0]
    assert h == {
        "title": "标题",
        "url": "https://example.com/a",
        "snippet": "摘要",
        "site": "示例站",
        "date": "2026-09-11",
    }


def test_site_falls_back_to_domain_and_date_tolerant():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "pageItems": [
                    _item(hostname="", publishedTime=1757900000000),  # 毫秒兜底
                    _item(hostname="", publishedTime="garbage"),
                ]
            },
        )

    hits = search("k1", "四六级报名时间", 5, client=_client(handler))
    assert hits[0]["site"] == "example.com"
    assert hits[0]["date"]  # 毫秒时间戳也能换算出日期
    assert hits[1]["date"] == ""  # 非法 publishedTime 静默为空串


def test_snippet_truncated_and_title_fallback():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"pageItems": [_item(snippet="长" * 2000, title="")]},
        )

    hits = search("k1", "四六级报名时间", 5, client=_client(handler))
    assert len(hits[0]["snippet"]) == SNIPPET_LIMIT
    assert hits[0]["title"] == "(无标题)"


def test_empty_and_error_shapes_return_empty_list():
    def ok_empty(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"pageItems": []})

    assert search("k1", "四六级报名时间", 5, client=_client(ok_empty)) == []

    def bad(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"code": "Throttling"})

    assert search("k1", "四六级报名时间", 5, client=_client(bad)) == []

    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("8s")

    assert search("k1", "四六级报名时间", 5, client=_client(boom)) == []


@pytest.mark.parametrize("q", ["", "一", "a" * 101])
def test_query_length_guard_short_circuits_without_network(q):
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("长度非法不应出网")

    assert search("k1", q, 5, client=_client(handler)) == []
    assert search("", "正常长度的查询词", 5, client=_client(handler)) == []


def test_num_results_capped():
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(_body_of(request))
        return httpx.Response(200, json={"pageItems": []})

    search("k1", "四六级报名时间", 99, client=_client(handler))
    assert seen[0]["advancedParams"]["numResults"] == MAX_RESULTS
