"""IQS 联网搜索适配层：内部 search() 返回干净结果，外部形状不穿透。

防腐哲学与 business 对齐：工具层只见 title/url/snippet/site/date 五个干净
字段，IQS 的响应形状、错误码、毫秒耗时在此翻译。任何失败一律返回 []
（调用方负责降级文案，不当崩溃——WeKnora「失败保留证据」同款语义）。

实测口径（2026-10-01 真调钉死，与官方文档两处出入以实测为准）：
- POST /search/unified，body 只需 query/engineType/advancedParams.numResults，
  文档示例的 contents 数组会 400（服务端要 RequestContents 对象）；
- publishedTime 是 ISO 字符串（"2026-09-11T00:00:00+08:00"）而非文档所说毫秒；
- hostname 为中文站点名，优于裸域名做来源展示；rerankScore 已预排，
  结果按返回序取用。mainText 有意不请求：整页正文不进上下文（token 成本），
  全文抓取留二期（届时须带 SSRF 校验：link 是外站 URL，禁止打内网）。
"""

from __future__ import annotations

from urllib.parse import urlsplit

import httpx

IQS_URL = "https://cloud-iqs.aliyuncs.com/search/unified"
IQS_TIMEOUT = 8.0  # 对齐 followups 线程超时风格；超时降级不崩主链路
SNIPPET_LIMIT = 600  # 与 search_knowledge 的 600 字口径一致
MAX_RESULTS = 10  # 服务端单次上限，超出截断


def _site_of(item: dict, url: str) -> str:
    """来源展示名：中文站点名优先，缺失退 URL 域名。"""
    host = str(item.get("hostname") or "").strip()
    if host:
        return host
    try:
        return urlsplit(url).netloc or url
    except ValueError:
        return url


def _date_of(raw) -> str:
    """publishedTime → 日期串（ISO 字符串取日期段；毫秒时间戳兼容兜底）。"""
    if isinstance(raw, (int, float)):
        from datetime import UTC, datetime  # noqa: PLC0415

        try:
            return datetime.fromtimestamp(raw / 1000, tz=UTC).strftime("%Y-%m-%d")
        except (ValueError, OSError, OverflowError):
            return ""
    s = str(raw or "").strip()
    return s.split("T", 1)[0] if len(s) >= 10 and s[:4].isdigit() else ""


def _clean(item: dict) -> dict | None:
    url = str(item.get("link") or "").strip()
    if not url:
        return None
    return {
        "title": str(item.get("title") or "").strip() or "(无标题)",
        "url": url,
        "snippet": str(item.get("snippet") or "").strip()[:SNIPPET_LIMIT],
        "site": _site_of(item, url),
        "date": _date_of(item.get("publishedTime")),
    }


def search(api_key: str, query: str, k: int = 5, *, client=None) -> list[dict]:
    """IQS 智能搜索 → 干净结果列表；参数非法或调用失败返回 []。

    client 注入缝：缺省 httpx 模块（每次短连接），测试传 MockTransport
    做契约测试；生产经 functools.partial 绑定 api_key 后作为工具闭包。
    """
    q = (query or "").strip()
    if not api_key or not 2 <= len(q) <= 100:  # 服务端长度约束前置
        return []
    body = {
        "query": q,
        "engineType": "CNAuto",
        "advancedParams": {"numResults": max(1, min(int(k), MAX_RESULTS))},
    }
    hc = client or httpx
    try:
        resp = hc.post(
            IQS_URL,
            headers={"Authorization": f"Bearer {api_key}"},
            json=body,
            timeout=IQS_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:  # noqa: BLE001 - 联网失败不崩主链路，交调用方降级
        print(f"[websearch] IQS 调用失败（返回空，主链路降级）：{e}", flush=True)
        return []
    out: list[dict] = []
    for item in data.get("pageItems") or []:
        if isinstance(item, dict):
            c = _clean(item)
            if c:
                out.append(c)
    return out
