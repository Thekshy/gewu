"""resume 桥翻译（P17-3）：用户文本 → HITL decisions（chat.py 调用）。

SSE 端点检测到 interrupted thread 时，需区分两类中断：
- HITL 确认门（payload 含 action_requests）→ 用 classify_reply 把用户文本
  映射为 approve/reject/respond decision（修改绝不能走 edit——edit 会跳过
  二次确认直接执行；修改=respond 引导模型按新参数重发，系统再次确认）；
- classic tx_gate（payload 含 tool/label/args）→ 维持原语义，resume 原文本。
前端零改动：照常 POST /api/chat。
"""

from __future__ import annotations

from gewu.agent.tx import (
    _CONFIRM_MODIFY_RE,
    FLOW_DEFS,
    SLOT_ORDER,
    classify_reply,
    slot_meta,
)


def find_hitl_payload(snap) -> dict | None:
    """从 state snapshot 的 pending 任务里找 HITL 中断载荷（找不到返回 None）。"""
    tasks = getattr(snap, "tasks", None) or ()
    if isinstance(tasks, dict):  # langgraph 版本差异：dict 或 tuple
        tasks = tasks.values()
    for task in tasks:
        for intr in getattr(task, "interrupts", None) or ():
            val = getattr(intr, "value", None)
            if isinstance(val, dict) and "action_requests" in val:
                return val
    return None


def _slot_updates(meta: dict, tool: str, slots: dict, text: str) -> tuple[dict, dict]:
    """确认阶段用户文本里的槽位修改 → (结构化变更, 自由文本补充)。

    purpose/reason 这类自由文本字段任何文本都能 parse 出值，单独分组——
    是否算「补充修改」由调用方结合确认词与文本长度决定（防「确认」被吞）。
    """
    flow = FLOW_DEFS.get(tool) or {}
    updates: dict[str, str] = {}
    soft: dict[str, str] = {}
    for slot in SLOT_ORDER:
        if slot not in flow.get("required", []) and slot not in (slots or {}):
            continue
        m = meta.get(slot)
        if not m:
            continue
        v = m["parse"](text)
        if v and v != (slots or {}).get(slot):
            (soft if slot in ("purpose", "reason") else updates)[slot] = v
    return updates, soft


def hitl_decisions(payload: dict, user_text: str, llm, business) -> dict:
    """用户回复 → {"decisions": [...]}（数量与 action_requests 对齐）。"""
    meta = slot_meta(business)
    reqs = payload.get("action_requests") or [{}]
    first = reqs[0] or {}
    tool = first.get("name", "")
    args = first.get("args") or {}
    pseudo = {"tx_phase": "confirm", "tx_slots": args, "tx_last_asked": ""}

    intent = classify_reply(llm, meta, user_text, pseudo)
    if intent == "cancel":
        dec = {
            "type": "reject",
            "message": "用户取消本次办理。请确认已取消并简短告知用户，不要再重试本次调用。",
        }
    elif intent == "new_topic":
        dec = {
            "type": "respond",
            "message": (
                f"用户没有确认，而是说了别的事：{user_text}。"
                "请放弃本次办理（不执行），直接回应用户的新内容。"
            ),
        }
    else:  # continue：结构化槽位变更=修改；确认词优先于自由文本补充
        updates, soft = _slot_updates(meta, tool, args, user_text)
        if updates:
            detail = "；".join(f"{k} 改为 {v}" for k, v in updates.items())
            dec = {
                "type": "respond",
                "message": (
                    f"用户要求修改：{detail}。请按修改后的参数重新发起调用"
                    "（系统会再次向用户确认），不要编造执行结果。"
                ),
            }
        elif soft and not _CONFIRM_MODIFY_RE.search(user_text) and len(user_text) > 4:
            # 长文本且无确认词：视为补充事由/用途（修改而非确认）
            detail = "；".join(f"{k} 改为 {v}" for k, v in soft.items())
            dec = {
                "type": "respond",
                "message": (
                    f"用户补充了信息：{detail}。请按补充后的参数重新发起调用"
                    "（系统会再次向用户确认），不要编造执行结果。"
                ),
            }
        elif _CONFIRM_MODIFY_RE.search(user_text):
            dec = {"type": "approve"}
        else:
            dec = {
                "type": "respond",
                "message": (
                    "用户回复不明确。请向用户重申办理摘要，请其回复「确认」提交或「取消」放弃。"
                ),
            }
    return {"decisions": [dec] * len(reqs)}
