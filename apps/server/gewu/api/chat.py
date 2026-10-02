"""POST /api/chat：SSE 流式问答（PARITY §2.4/§3 契约，移植 Go internal/api/chat.go）。

校验序列逐条对照；SSE 分帧 `data: {json}\n\n`（UTF-8 原文不转义）；done 事件
在流末单点发射（reason: completed/max_tokens/error/aborted——RunChat 单点语义）。
interrupt/resume 桥：thread 停在确认门（tx_gate interrupt）时，用户本轮消息作为
resume 值续跑——前端照常发 /api/chat，零改动。流结束后异步固化长期记忆。

P21-3：需登录（cookie）；role 改服务端权威（users.role，请求体 role 字段废弃
忽略——权限矩阵从君子协定变强制）；user_id=登录 email（记忆/台账真实归属）。
P22：session_id 必填且必须为已登记属本人的会话（POST /api/sessions 下发；
未传 422 给指引、不属本人/不存在 404 防枚举；"default" 缺省值废弃）；每轮
刷 updated_at + 首见空 title 回填首问前 20 字（SessionStore.note_turn）。
P23：per-user token 限额闸（users.daily_token_limit ?? DAILY_USER_BUDGET，
超限 429 文案区分全局闸）；contextvar set 供 LLMService 记账归属。
P25：done 之后按 Q5 门追发 follow_ups 事件（知识型路由+正常收尾+无 HITL
悬停才生成；flash 小模型 8s 超时静默降级——主路径零延迟增量）。
"""

from __future__ import annotations

import json
import os
import threading
import time
from typing import Annotated

from fastapi import APIRouter, Body, HTTPException, Request
from fastapi.responses import StreamingResponse
from langchain_core.messages import HumanMessage

from gewu.agent import events as ev
from gewu.agent.followups import generate_follow_ups, should_generate
from gewu.api.auth import require_user
from gewu.obs import Tracer, set_current_tracer
from gewu.usage import current_user

router = APIRouter()


def _parse_chat_body(payload: dict) -> dict:
    """请求校验（顺序对照 Go chat.go；role 自 P21 起服务端权威，不再解析）。"""
    question = payload.get("question")
    if question is None:
        question = ""
    if not isinstance(question, str):
        raise HTTPException(status_code=422, detail="请求体不是合法 JSON")
    if question == "":
        raise HTTPException(status_code=422, detail="问题不能为空")
    if len(question) > 500:
        raise HTTPException(status_code=422, detail="问题过长")
    mode = payload.get("mode") or "auto"
    if mode not in ("auto", "react"):
        raise HTTPException(status_code=422, detail="mode 必须为 auto/react")
    session_id = payload.get("session_id")
    if session_id is None or session_id == "":
        raise HTTPException(
            status_code=422, detail="session_id 不能为空（请先 POST /api/sessions 创建会话）"
        )
    if not isinstance(session_id, str):
        raise HTTPException(status_code=422, detail="请求体不是合法 JSON")
    if len(session_id) > 64:
        raise HTTPException(status_code=422, detail="session_id 过长（上限 64 字符）")
    return {"question": question, "mode": mode, "session_id": session_id}


def _sse(ev_: dict) -> str:
    return f"data: {json.dumps(ev_, ensure_ascii=False, separators=(',', ':'))}\n\n"


def _consolidate_async(request: Request, req: dict, question: str, answer: str) -> None:
    """会话结束后的记忆固化（Go consolidateAsync 等价）：线程内跑，不阻塞响应。"""
    memory = getattr(request.app.state, "memory", None)
    if memory is None:
        return

    def work():
        from gewu.memory import consolidate

        user_token = current_user.set(req["user"])  # 异步线程不继承，显式带归属
        try:
            consolidate(
                memory,
                request.app.state.llm,
                req["user"],
                req["session_id"],
                question,
                answer,
            )
        except Exception as e:  # noqa: BLE001 - 固化失败不影响主链路
            print(f"[agent] 记忆固化失败（不影响主链路）：{e}")
        finally:
            current_user.reset(user_token)

    threading.Thread(target=work, daemon=True).start()


@router.post("/api/chat")
def chat(request: Request, payload: Annotated[dict, Body(...)]):
    user = require_user(request)  # 401 未登录；role 服务端权威、user=email
    req = _parse_chat_body(payload)
    req["role"] = user.role
    req["user"] = user.email
    # P22 归属校验：会话必须已登记且属本人（否则 404，不泄露他人会话存在性）。
    if request.app.state.sessions.get(user.email, req["session_id"]) is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    request.app.state.sessions.note_turn(user.email, req["session_id"], req["question"])
    try:
        request.app.state.budget.ensure()
    except Exception as e:  # noqa: BLE001 - 预算耗尽 → 429（PARITY §2.4）
        raise HTTPException(status_code=429, detail=str(e)) from e
    # P23 per-user 限额闸：个性化限额 ?? 全局缺省；超限 429（与全局闸文案区分）。
    # 查询失败放行（软防护：公网兜底还有全局闸 usage.json；打印日志留痕）。
    usage_store = getattr(request.app.state, "usage", None)
    if usage_store is not None:
        try:
            limit = request.app.state.auth.daily_limit(user.email)
            if limit is None:
                limit = request.app.state.settings.daily_user_budget
            if usage_store.today(user.email) >= limit:
                raise HTTPException(
                    status_code=429,
                    detail=f"今日个人 token 预算已用尽（上限 {limit}），请明天再试",
                )
        except HTTPException:
            raise
        except Exception as e:  # noqa: BLE001 - 用量查询失败不拦主链路
            print(f"[chat] per-user 用量查询失败（放行，全局闸兜底）：{e}", flush=True)
    # 记账归属（contextvar）：StreamingResponse 的 sync 迭代由线程池分派（每次
    # next 可能换线程），不能在端点体 set——在 generate() 迭代体开头 set，保证
    # 与本轮 LLM 调用/记账同线程；consolidate 异步线程在 work() 首行显式 set。
    graph = request.app.state.graph
    config = {"configurable": {"thread_id": req["session_id"]}}
    # P31-3：输入构造直接对齐 GewuAgentState（外壳 new_state/agent_in 退役）。
    # citations 显式 [] 经 reducer 承担轮起清零（P26 跨轮污染修复的等价承载，
    # 回归测试 test_citations_cleared_between_turns）；answer_streamed 同款。
    state_input = {
        "messages": [HumanMessage(content=req["question"])],
        "question": req["question"],
        "mode": req["mode"],
        "session_id": req["session_id"],
        "role": req["role"],
        "user": req["user"],
        "citations": [],
        "answer_streamed": "",
        "truncated": False,
        "answer": "",
    }

    # interrupt/resume 桥：thread 停在确认门时以 HITL decisions resume
    # （P31-3 起单形态：classic tx_gate 原文 resume 分支随链路退役）。
    from langgraph.types import Command

    from gewu.agent.resume import find_hitl_payload, hitl_decisions

    run_input = state_input
    try:
        snap = graph.get_state(config)
        if snap.next:  # 停在确认门（agent HITL）
            payload = find_hitl_payload(snap)
            if payload is not None:
                run_input = Command(
                    resume=hitl_decisions(
                        payload, req["question"], request.app.state.llm, request.app.state.business
                    )
                )
    except Exception:  # noqa: BLE001 - 状态读取失败按新会话处理
        pass

    def generate():
        t0 = time.monotonic()
        truncated = False
        answer = ""
        citations: list[dict] = []
        hitl_paused = False
        trace = {"route": "", "route_layer": "", "steps": 0, "tool": 0}
        # P27：本轮观测聚合器（store 软降级时全程 no-op；print [chat] 行与
        # trace 行在此同源产出，双写不漂移）
        tracer = Tracer(
            getattr(request.app.state, "trace_store", None),
            {
                "session_id": req["session_id"],
                "user": req["user"],
                "role": req["role"],
                "mode": req["mode"],
                "question": req["question"],
            },
        )

        def turn_log(reason: str, err: str | None = None) -> None:
            """整轮汇总一行 JSON（线上排障回溯：问题/路由/步数/耗时/结局单点可见）。"""
            print(
                "[chat] "
                + json.dumps(
                    {
                        "session": req["session_id"],
                        "user": req["user"],
                        "role": req["role"],
                        "mode": req["mode"],
                        "q": req["question"][:60],
                        **trace,
                        "answer_chars": len(answer),
                        "answer_head": answer[:80],
                        "ms": int((time.monotonic() - t0) * 1000),
                        "reason": reason,
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
            tracer.finish(
                route=trace["route"],
                route_layer=trace["route_layer"],
                reason=reason,
                latency_ms=int((time.monotonic() - t0) * 1000),
                steps=trace["steps"],
                answer_head=answer[:80],
                error=err,
            )

        try:
            # P31-3 外壳塌缩：create_agent 编译产物为顶层图，无嵌套——
            # subgraphs 摘除，custom 事件不再包 (namespace, event) 元组。
            # 记账归属：SSE sync 迭代每次 next 可能换 Context（anyio 线程池），
            # contextvar 须在每次取事件前 re-set——graph 节点的 LLM 调用/记账
            # 都发生在 next() 的调用栈里，随 Context 副本传播。
            stream = graph.stream(run_input, config, stream_mode="custom")
            while True:
                current_user.set(req["user"])
                set_current_tracer(tracer)  # P23 同款纪律：每次 next 前 re-set
                try:
                    evt = next(stream)
                except StopIteration:
                    break
                t = evt.get("type")
                if t == "route":
                    trace["route"] = evt.get("route", "")
                    trace["route_layer"] = evt.get("layer", "")
                elif t == "citations":
                    citations = evt.get("items") or []
                elif t in ("status", "step"):
                    trace["steps"] += 1
                elif t in ("pending_action", "action_result"):
                    trace["tool"] += 1
                yield _sse(evt)
            # 终态读取（interrupt 悬停时为当前值）：answer/truncated 单点真相；
            # snap.next 非空 = HITL 确认门悬停（follow_ups 的 Q5 门输入之一）。
            try:
                snap = graph.get_state(config)
                vals = snap.values or {}
                truncated = bool(vals.get("truncated"))
                answer = vals.get("answer") or ""
                hitl_paused = bool(snap.next)
            except Exception:  # noqa: BLE001
                pass
        except GeneratorExit:
            turn_log("aborted")
            raise  # 客户端断开：done 已无法送达（语义上记 aborted）
        except Exception as e:  # noqa: BLE001 - 链路错误 → error 事件 + done(error)
            turn_log("error", err=str(e))
            yield _sse(ev.error_evt(str(e)))
            yield _sse(ev.done_evt(int((time.monotonic() - t0) * 1000), "error"))
            return
        reason = "max_tokens" if truncated else "completed"
        turn_log(reason)
        yield _sse(ev.done_evt(int((time.monotonic() - t0) * 1000), reason))
        # P25：追问在 done 之后追发（前端 done 即解锁输入，晚到不阻塞下一问）；
        # Q5 门 + 三层守卫 + 8s 超时全在 followups 模块内，失败静默无事件。
        if should_generate(trace["route"], reason, hitl_paused):
            titles = [str(c.get("title") or "") for c in citations]
            items = generate_follow_ups(
                request.app.state.llm, req["user"], req["question"], answer, titles
            )
            if items:
                yield _sse(ev.follow_ups_evt(items))
        if os.environ.get("MEMORY_CONSOLIDATE", "on") == "on":
            _consolidate_async(request, req, req["question"], answer)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
