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

# freshness 相对窗口 → IQS 顶层 timeRange（四档均实测生效，2026-10-02 真调；
# 注意 advancedParams.timeRange / queryContext.timeRange 均无效，参数在顶层）。
_FRESHNESS_MAP = {"day": "OneDay", "week": "OneWeek", "month": "OneMonth", "year": "OneYear"}


def _canonical(url: str) -> str | None:
    """URL 合法性与规范化键：仅 http/https、须有主机、去 fragment——
    同一页面的带锚点变体只留首条（WeKnora canonical 去重同款）。"""
    from urllib.parse import urlsplit

    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return None
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return None
    return parts._replace(fragment="").geturl()


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


def search(
    api_key: str, query: str, k: int = 5, freshness: str = "", *, client=None
) -> tuple[list[dict], str]:
    """IQS 智能搜索 → (干净结果列表, 状态)。

    状态三分支（调用方据此分流回执）：ok=有结果；empty=服务正常但无命中
    （含参数非法短路）；error=网络/HTTP/超时（不崩主链路，按不可用降级）。
    freshness：day/week/month/year → 请求体顶层 timeRange（时效题过滤旧闻；
    非法值静默视为不过滤）。client 注入缝：缺省 httpx，测试传 MockTransport。
    """
    q = (query or "").strip()
    if not api_key or not 2 <= len(q) <= 100:  # 服务端长度约束前置
        return [], "empty"
    body: dict = {
        "query": q,
        "engineType": "CNAuto",
        "advancedParams": {"numResults": max(1, min(int(k), MAX_RESULTS))},
    }
    if freshness in _FRESHNESS_MAP:
        body["timeRange"] = _FRESHNESS_MAP[freshness]
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
        return [], "error"
    out: list[dict] = []
    seen: set[str] = set()
    for item in data.get("pageItems") or []:
        if not isinstance(item, dict):
            continue
        c = _clean(item)
        if c is None:
            continue
        key = _canonical(c["url"])
        if key is None or key in seen:  # 非法 scheme 或重复页丢弃
            continue
        seen.add(key)
        c["url"] = key  # 存规范化 URL（去 fragment），键值同源
        out.append(c)
    return out, ("ok" if out else "empty")
