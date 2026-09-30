"""容错解析 LLM 输出的 JSON（移植自 Go internal/agent/routing/jsonx.go 同族）。

剥离 ```json 围栏、截取首个 { 到末个 } 之间的内容再解析。
"""

from __future__ import annotations

import json
from typing import Any


class EmptyJSONError(ValueError):
    """LLM 返回为空（调用方各自降级）。"""


def parse_json_object(raw: str) -> dict[str, Any]:
    s = raw.strip()
    if not s:
        raise EmptyJSONError("LLM 返回为空")
    start = s.find("{")
    if start >= 0:
        end = s.rfind("}")
        if end > start:
            s = s[start : end + 1]
    obj = json.loads(s)
    if not isinstance(obj, dict):
        raise ValueError("JSON 不是对象")
    return obj


def json_str(obj: dict, key: str) -> str:
    """取对象字符串字段（缺失/类型不符返回空串）。"""
    v = obj.get(key)
    return v if isinstance(v, str) else ""


def json_str_slice(obj: dict, key: str) -> list[str]:
    """取对象字符串数组字段（缺失/类型不符返回空列表）。"""
    v = obj.get(key)
    if not isinstance(v, list):
        return []
    return [x for x in v if isinstance(x, str)]


def json_str_map(obj: dict, key: str) -> dict[str, Any]:
    """取对象嵌套对象字段（缺失/类型不符返回空 dict）。"""
    v = obj.get(key)
    return v if isinstance(v, dict) else {}
