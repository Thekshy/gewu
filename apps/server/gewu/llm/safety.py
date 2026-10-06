"""Provider 内容审查拒绝的统一分类（P42，WeKnora 式归一化）。

立场：内容审查由 provider 侧安全策略执行，本模块只做「识别 + 优雅降级」，
不做也不需要做任何绕过。识别全部基于官方契约形态（智谱 1301 错误码 /
contentFilter 字段 / 流式非标准 finish_reason=sensitive，见 docs.bigmodel.cn
cn/faq/api-code 与 cn/guide/platform/securityaudit）与审查类通用词面——
不包含、也不会输出任何具体敏感词条；真实词表永远只存在于 provider 侧。

四种拒绝形态 → 两条收敛路径：
- 异常形态（HTTP 400/403 + 审查错误码/消息词面）：is_content_filter_error →
  主循环由 SSE 端点转优雅拒答（api/chat.py），小模型族由调用点既有 except
  降级接住（改写回原查询、精排退 RRF、追问弃用……）；
- 200 形态（finish_reason=sensitive|content_filter，或 content 本身是错误
  文案）：is_filter_finish / looks_like_error_content → LLMService.chat
  拦截转 ContentFilterError（P40 rewriter 被错误文案污染挂账的根治点），
  AgentDoneMiddleware 按 finish 收口主循环（P10 契约补 content_filter 位）。

参照：WeKnora internal/agent/observe.go（各家审查信号归一 canonical 值 +
终止循环 + 兜底文案 + 按正常完成流关闭）、retry.go（审查类失败不重试——
本模块同样不新增任何重试）。
"""

from __future__ import annotations

import json
import re


class ContentFilterError(RuntimeError):
    """provider 内容审查拒绝（本系统归一后的统一异常形态）。"""


# 智谱官方 13xx 段中唯一内容安全码（1302-1306 是限流/并发、1000-1005 是鉴权，
# 均不得误判）。新 provider 形态在此追加码值，不改识别逻辑。
CONTENT_FILTER_CODES = frozenset({"1301"})

# 审查类通用词面（异常消息判定用，仅 400/403 上启用防误判；只收「审查动作/
# 结论」类表述，不收任何具体敏感词条）。
_FILTERED_MSG_RE = re.compile(
    r"敏感|违规|不合规|不安全|安全审核|审核未通过|安全策略|内容安全"
    r"|content[_ ]?filter|sensitive|moderation|inappropriate",
    re.IGNORECASE,
)

# 200 形态错误文案防御：须同时命中「拒绝/告知动作」与「审查话题」两类词才判
# 拦截——单项命中放行（「违规用电处分规定」这类合法政策术语不得误伤）。
_REFUSAL_ACTION_RE = re.compile(r"检测到|无法|抱歉|对不起|已拦截|被拦截")
_FILTERED_TOPIC_RE = re.compile(
    r"敏感|违规|不合规|不安全|安全风险|安全审核|安全策略|内容安全"
    r"|content[_ ]?filter|sensitive|moderation",
    re.IGNORECASE,
)


def _status_code_of(exc: BaseException) -> int | None:
    """duck-typing 取 HTTP 状态码（openai.APIStatusError 有 .status_code；
    httpx.HTTPStatusError 挂在 .response.status_code）；都没有返回 None。"""
    code = getattr(exc, "status_code", None)
    if isinstance(code, int):
        return code
    code = getattr(getattr(exc, "response", None), "status_code", None)
    return code if isinstance(code, int) else None


def _body_of(exc: BaseException) -> object:
    """错误响应体（openai SDK 解析好的 dict；缺失返回 None）。"""
    return getattr(exc, "body", None)


def _error_message_of(exc: BaseException) -> str:
    """异常的完整可判文本：str(exc) + 响应体序列化（词面判定输入）。"""
    parts = [str(exc)]
    body = _body_of(exc)
    if isinstance(body, dict):
        parts.append(json.dumps(body, ensure_ascii=False))
    elif isinstance(body, str):
        parts.append(body)
    return " | ".join(parts)


def is_content_filter_error(exc: BaseException) -> bool:
    """provider 异常是否为内容审查拒绝（形态识别，其他错误类永不误伤）。

    优先级：本模块统一异常 → 官方错误码（1301 / contentFilter 字段形状）→
    400/403 上的审查词面。无状态码的普通异常（ValueError 等）一律 False。
    """
    if isinstance(exc, ContentFilterError):
        return True
    status = _status_code_of(exc)
    if status is None:
        return False
    body = _body_of(exc)
    if isinstance(body, dict):
        err = body.get("error")
        if isinstance(err, dict) and str(err.get("code", "")) in CONTENT_FILTER_CODES:
            return True
        if "contentFilter" in body:  # 智谱契约顶层字段（role/level 数组）
            return True
    if status in (400, 403) and _FILTERED_MSG_RE.search(_error_message_of(exc)):
        return True
    return False


def is_filter_finish(reason: str) -> bool:
    """finish_reason 是否为审查中断：OpenAI 系标准值 content_filter + 智谱
    流式非标准值 sensitive（分批检测命中在末条 chunk 体现，官方文档明示）。
    """
    return reason in ("content_filter", "sensitive")


def looks_like_error_content(text: str) -> bool:
    """补全文本是否为「错误文案而非模型产出」（200 形态防御，P40 rewriter
    被错误文案当改写结果消费的根因类）。从严两条：JSON error 对象形状；
    拒绝动作词 × 审查话题词同时命中。小模型族合法输出（改写串 / 精排分数
    JSON / 追问列表 / 记忆摘要）不含「拒绝动作 × 审查话题」组合，不误伤。
    """
    s = text.strip()
    if not s:
        return False
    if s.startswith("{") and s.endswith("}"):
        try:
            obj = json.loads(s)
        except ValueError:
            obj = None
        if isinstance(obj, dict) and "error" in obj:
            return True
    return _REFUSAL_ACTION_RE.search(s) is not None and _FILTERED_TOPIC_RE.search(s) is not None


def is_blocked_completion(finish_reason: str, text: str) -> bool:
    """200 形态总判定：finish 值或 文案 两路任一命中即拦截。"""
    return is_filter_finish(finish_reason) or looks_like_error_content(text)
