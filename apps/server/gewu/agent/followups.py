"""P25-1 追问生成（done 之后 SSE 追发 follow_ups 事件，主路径零延迟增量）。

america.gov/chat 同款模式：回答完成后给 3 条可点续问。生成走小模型通道
（llm_small_model），8s 超时静默降级（无事件=无 pills），不占主路径延迟
（P24 结论：串行模型调用是 agent-first 的结构性成本，追问不重蹈）。

GLM flash 有无视否定指令的前科（路由误判/工具乱调两度实证），输出必须过
三层代码守卫：①JSON 解析失败即弃 ②逐条长度 6~30/不等于原问/保序去重
③剩余 <2 条即弃（不做残缺单条）。
"""

from __future__ import annotations

import json
import threading

from gewu.usage import current_user

FOLLOWUP_ROUTES = frozenset({"factual", "research", "hybrid"})
_TIMEOUT_S = 8.0

_SYSTEM = (
    "你是校园制度问答助手的追问生成器。根据本轮问答生成恰好 3 条用户可能想"
    "继续问的问题。要求：每条 6~30 个字；口语化中文疑问句；不重复原问题；"
    "只输出一个 JSON 字符串数组，不要任何解释、前缀或代码块。"
)


def should_generate(route: str, reason: str, hitl_paused: bool) -> bool:
    """Q5 门：知识型路由 + 正常收尾 + 无 HITL 悬停（确认门在等用户时不生成）。"""
    return route in FOLLOWUP_ROUTES and reason == "completed" and not hitl_paused


def guard_follow_ups(raw: str, question: str, *, limit: int = 3) -> list[str]:
    """三层守卫：dirty in → 干净 out 或 []（解析/长度/去重/数量四路单测覆盖）。"""
    text = (raw or "").strip()
    i, j = text.find("["), text.rfind("]")
    if i == -1 or j <= i:
        return []
    try:
        arr = json.loads(text[i : j + 1])
    except (ValueError, TypeError):
        return []
    if not isinstance(arr, list):
        return []
    seen: set[str] = set()
    out: list[str] = []
    q0 = question.strip()
    for item in arr:
        if not isinstance(item, str):
            continue
        q = item.strip().strip('“”"「」')
        if not 6 <= len(q) <= 30:
            continue
        if q == q0 or q in seen:
            continue
        seen.add(q)
        out.append(q)
        if len(out) == limit:
            break
    return out if len(out) >= 2 else []


def generate_follow_ups(
    llm, user: str, question: str, answer: str, titles: list[str], *, timeout_s: float = _TIMEOUT_S
) -> list[str]:
    """小模型生成 + 守卫。独立线程跑、join 超时即弃（SSE 收尾最多再挂 timeout_s）。

    记账归属：线程不继承 contextvar，work() 首行显式 set（_consolidate_async 同款）。
    """
    excerpt = (answer or "")[:1200]
    docs = "、".join(t for t in titles if t)[:120] or "（无）"
    user_content = f"问题：{question}\n\n回答节选：{excerpt}\n\n引用文档：{docs}"
    box: dict = {}

    def work() -> None:
        token = current_user.set(user)
        try:
            box["raw"] = llm.chat(
                [("system", _SYSTEM), ("user", user_content)],
                small=True,
                temperature=0.0,
                max_tokens=300,
            )
        except Exception as e:  # noqa: BLE001 - 生成失败不影响主链路
            box["err"] = e
        finally:
            current_user.reset(token)

    t = threading.Thread(target=work, daemon=True)
    t.start()
    t.join(timeout_s)
    if t.is_alive():
        print("[chat] follow_ups 生成超时（不影响主链路）", flush=True)
        return []
    if "err" in box or "raw" not in box:
        print(f"[chat] follow_ups 生成失败（不影响主链路）：{box.get('err')}", flush=True)
        return []
    return guard_follow_ups(str(box["raw"]), question)
