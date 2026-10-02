"""resume 桥翻译（P17-3）：用户文本 → HITL decisions（chat.py 调用）。

SSE 端点检测到 interrupted thread 时，HITL 确认门（payload 含
action_requests）的载荷需把用户文本映射为 approve/reject/respond decision：
修改绝不能走 edit——edit 会跳过二次确认直接执行；修改=respond 引导模型按
新参数重发，系统再次确认。前端零改动：照常 POST /api/chat。

P31-2：classic tx_gate 随退役删除，resume 桥收单形态；classify_reply（续轮
意图判定，HITL resume 唯一消费者）自 tx.py 迁入本模块。
"""

from __future__ import annotations

import json
import re

from gewu.agent.prompts import CLASSIFY_REPLY_SYSTEM
from gewu.agent.tools import flow_defs
from gewu.agent.txmeta import SLOT_ORDER, slot_meta
from gewu.jsonx import json_str, parse_json_object

# confirm 阶段的确认词与续轮意图启发式（classify_reply 用）。
_CONFIRM_MODIFY_RE = re.compile(r"确认|确定|好的|可以|提交|是的|对")
_REPLY_CANCEL_RE = re.compile(r"取消|算了|不办了|不要了")
_REPLY_CONFIRM_RE = re.compile(r"确认|确定|好的|可以|提交")
_REPLY_TOPIC_RE = re.compile(r"什么|怎么|为什么|几点|哪|谁|吗")


def _is_question_mark(s: str) -> bool:
    return "？" in s or "?" in s


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
    flow = flow_defs().get(tool) or {}
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


def classify_reply(llm, meta: dict, user_text: str, state: dict) -> str:
    """判断用户回复是继续流程、取消流程、还是切换新话题（LLM 优先，启发式兜底）。"""
    if llm is not None and llm.has_key():
        phase = "确认" if state.get("tx_phase") == "confirm" else "补充信息"
        prompt = CLASSIFY_REPLY_SYSTEM.format(
            phase=phase, slots=json.dumps(state.get("tx_slots") or {}, ensure_ascii=False)
        )
        try:
            raw = llm.chat(
                [("system", prompt), ("user", user_text)],
                json_mode=True,
                small=True,
                max_tokens=60,
            )
            obj = parse_json_object(raw)
            intent = json_str(obj, "intent")
            if intent in ("continue", "cancel", "new_topic"):
                return intent
        except Exception:  # noqa: BLE001 - 退化为启发式
            pass
    if _REPLY_CANCEL_RE.search(user_text):
        return "cancel"
    if _REPLY_CONFIRM_RE.search(user_text):
        return "continue"
    if _REPLY_TOPIC_RE.search(user_text):
        return "new_topic"
    if state.get("tx_last_asked"):
        m = meta.get(state["tx_last_asked"])
        if m and m["parse"](user_text):
            return "continue"
    for slot in SLOT_ORDER:
        if slot in ("purpose", "reason"):
            continue
        if slot not in (state.get("tx_slots") or {}):
            continue
        m = meta.get(slot)
        if m and m["parse"](user_text):
            return "continue"
    if len(user_text) <= 12 and not _is_question_mark(user_text):
        return "continue"  # 短句大概率是在回答追问
    return "new_topic"


def hitl_decisions(payload: dict, user_text: str, llm, business) -> dict:
    """用户回复 → {"decisions": [...]}（数量与 action_requests 对齐）。"""
    meta = slot_meta(business)
    reqs = payload.get("action_requests") or [{}]
    first = reqs[0] or {}
    tool = first.get("name", "")
    args = first.get("args") or {}
    if tool == "run_flow":
        # P33：run_flow 中断载荷解包——tool 还原为 flow_id、slots 取内层
        # 槽位（@tool 参数不可名 args，内层键主名 slots、兼容 args），槽位
        # 修改检测与专属路径共用一套解析（确认/修改/取消语义不变）。
        tool = str(args.get("flow_id", "") or "")
        inner = args.get("slots", args.get("args"))
        args = inner if isinstance(inner, dict) else {}
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
