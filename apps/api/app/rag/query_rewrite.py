"""查询改写：口语问题 → 政策术语检索串。

解决无向量检索时的词法失配：「最多能借几本书」里没有「上限」「外借」这两个
语料里的实际用词，BM25 就会召回错误文档。改写只做词面归一，不改变语义；
无 key 时原样返回，不影响离线模式。
"""

from __future__ import annotations

import logging

from .. import llm
from ..llm import has_key

logger = logging.getLogger(__name__)

_SYSTEM = """你是校园政策检索的查询改写器。把用户的口语化问题改写为适合关键词检索的查询串：
1. 保留核心实体（图书馆、转专业、奖学金、体测……）与数字；
2. 把口语说法换成政策文件用语（如：最多→上限，借书→外借 借阅，钱→元，挂科→不及格，发学位证→授予学位）；
3. 输出 10~25 个字的查询词串，不解释、不使用引号。
只输出改写后的查询串本身。"""

_cache: dict[str, str] = {}


def expand(query: str) -> str:
    """返回 原查询 + 改写词串（保召回）；失败或无 key 时返回原查询。"""
    if not has_key():
        return query
    if query in _cache:
        return _cache[query]
    try:
        rewritten = (
            llm.chat(
                [
                    {"role": "system", "content": _SYSTEM},
                    {"role": "user", "content": query},
                ],
                temperature=0.0,
                max_tokens=80,
            )
            .strip()
            .strip('"“”')
        )
        result = f"{query} {rewritten}" if rewritten else query
        _cache[query] = result
        return result
    except Exception as exc:  # noqa: BLE001
        logger.warning("查询改写失败，使用原查询：%s", exc)
        return query
