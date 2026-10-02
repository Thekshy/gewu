"""深度研究的子问题拆解（P31-2 起：plan 纯函数随 agent 侧 deep_research 工具保留；
classic 的 run_research 全链节点随退役删除，证据聚合/综合作答由主循环完成）。

拆解子问题（PARITY §7 的工具化残留）：LLM 拆 2~4 个可独立检索的子问题，
失败退化为原问题单路检索。
"""

from __future__ import annotations

from gewu.agent.prompts import PLANNER_SYSTEM
from gewu.jsonx import json_str_slice, parse_json_object

MAX_SUBQUESTIONS = 4  # 子问题上限


def plan(llm, question: str) -> list[str]:
    """LLM 拆解子问题；无 key/失败时退化为原问题单路检索。"""
    if not llm.has_key():
        return [question]
    try:
        raw = llm.chat(
            [("system", PLANNER_SYSTEM), ("user", question)],
            json_mode=True,
            small=True,
            max_tokens=400,
        )
        subs = json_str_slice(parse_json_object(raw), "subquestions")
        if subs:
            return subs[:MAX_SUBQUESTIONS]
    except Exception as e:  # noqa: BLE001
        print(f"[research] 子问题拆解失败，退化为单路检索：{e}")
    return [question]
