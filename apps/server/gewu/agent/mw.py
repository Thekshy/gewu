"""agent-first 主循环的自定义中间件族与子图状态（P17-4/5）。

件清单（装配顺序见 agent.py；wrap_* 外层=列表在前者）：
- GewuAgentState：create_agent 的 state_schema 扩展（role/user/mem_block 经
  input schema 从外壳流入；citations 带去重 reducer 供并行检索合并）。
- AgentPromptMiddleware：system prompt 动态拼记忆块（wrap_model_call.override）。
- UsageRecordMiddleware：主循环 token 记账走 LLMService 预算闸（口径与
  classic 链路一致）。
- TruncationDefenseMiddleware：P10 截断防御铁律平移——finish_reason=length
  且带 tool_calls 时不执行工具，assistant 原样回填 + 合成错误 observation
  重调模型（Pi 式，不记指纹）。
- WriteSlotGateMiddleware：写工具缺必填参数时不执行，emit slot_question +
  引导模型向用户收集（classic advance 追问语义的事件级等价物）。
- PendingActionMiddleware：写工具参数齐 → emit pending_action + 确认摘要
  （classic tx_confirm 语义），在 HITL 中断之前发射。
- RouteEventMiddleware：after_agent 按本轮工具轨迹合成 effective route 补发
  （两段式第二段）。
- SearchQueryGuardMiddleware：search_knowledge 检索词与原问题零重合时拼回
  原话（P24-3；docstring 引导是软防线，本件是硬防线）。
- StreamingAnswerMiddleware：主循环答案 token 级流式（P30）——模型调用经
  流式代理逐 delta 发 answer_delta，中间轮 answer_reset 撤回。

P24-1：UsageRecordMiddleware 兼任 agent 主循环 [llm] per-call 观测（ms +
ctx_profile，补 create_agent 内部 model.invoke 不经 LLMService 封装的盲区）。
"""

from __future__ import annotations

import time
from typing import Annotated, Any, NotRequired

from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import AgentState, PrivateStateAttr
from langchain_core.language_models.chat_models import BaseChatModel, ChatGeneration, ChatResult
from langchain_core.messages import (
    AIMessage,
    AIMessageChunk,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)

from gewu.agent import events as ev
from gewu.agent.emitter import emit
from gewu.agent.prompts import agent_system_prompt
from gewu.agent.tx import FLOW_DEFS, build_confirm, normalize_slot, slot_meta
from gewu.dates import today_iso
from gewu.llm.service import ctx_profile
from gewu.obs import current_tracer

# 写工具四件（HITL 确认门对象，与 tools.py read_only=False 对齐）。
WRITE_TOOLS = {"book_venue", "cancel_booking", "submit_leave", "approve_leave"}


def _merge_citations(existing: list | None, new: list | None) -> list:
    """citations 通道 reducer：按 (doc_id,title) 去重合并，编号累计重排。"""
    out: list[dict] = list(existing or [])
    have = {(c["doc_id"], c["title"]) for c in out}
    for c in new or []:
        key = (c["doc_id"], c["title"])
        if key in have:
            continue
        have.add(key)
        out.append(ev.citation(len(out) + 1, c["doc_id"], c["title"], c["source"]))
    return out


class GewuAgentState(AgentState):
    """主循环子图状态扩展。role/user/mem_block 由外壳 state 流入（input schema）。"""

    role: NotRequired[str]
    user: NotRequired[str]
    mem_block: NotRequired[str]
    citations: NotRequired[Annotated[list, _merge_citations]]
    guard_action: NotRequired[Annotated[str, PrivateStateAttr]]  # allow|meta|block（本轮）
    # P30：最终轮流式已发文本（外壳 ChatState 同名字段接住，agent_done 防重读）
    answer_streamed: NotRequired[str]


# ---------- 消息行走辅助 ----------


def last_ai_message(messages: list) -> AIMessage | None:
    for m in reversed(messages or []):
        if isinstance(m, AIMessage):
            return m
    return None


def ai_content_text(message) -> str:
    """AIMessage/chunk 的 content 归一为纯文本（流式防重两端统一口径）。"""
    c = message.content
    if isinstance(c, str):
        return c
    return "".join(seg.get("text", "") if isinstance(seg, dict) else str(seg) for seg in c)


def messages_since_last_human(messages: list) -> list:
    """本轮的消息切片（最后一个 HumanMessage 之后，含其后全部）。"""
    idx = -1
    for i in range(len(messages or []) - 1, -1, -1):
        if isinstance(messages[i], HumanMessage):
            idx = i
            break
    return (messages or [])[idx + 1 :] if idx >= 0 else list(messages or [])


def normalize_tool_args(meta: dict, tool: str, args: dict) -> dict[str, str]:
    """模型给的原始参数过确定性解析器归一（日期换算、场馆名→ID；失败保留原值）。"""
    out: dict[str, str] = {}
    for k, v in (args or {}).items():
        raw = v if isinstance(v, str) else str(v)
        norm, ok = normalize_slot(meta, k, raw)
        out[k] = norm if ok else raw
    return out


def write_call_ready(business, tool_call: dict) -> bool:
    """写工具必填参数是否齐全（HITL when 谓词与槽位门共用；口径=字段在场）。"""
    name = tool_call.get("name", "")
    flow = FLOW_DEFS.get(name)
    if not flow:
        return True  # 非流程工具（防御：不在 FLOW_DEFS 的写工具不设门）
    args = tool_call.get("args") or {}
    return all(str(args.get(s, "") or "").strip() for s in flow["required"])


def effective_route(messages: list) -> tuple[str, str]:
    """本轮工具轨迹 → (effective route, reason)。意图分流的判断权在工具选择。"""
    tools: list[str] = []
    for m in messages_since_last_human(messages):
        if isinstance(m, ToolMessage) and m.name:
            tools.append(m.name)
    searched = "search_knowledge" in tools
    researched = "deep_research" in tools
    webbed = "web_search" in tools
    wrote = any(t in WRITE_TOOLS for t in tools)
    if wrote:
        return ("hybrid", "本轮检索后办理") if searched else ("transaction", "本轮办理业务")
    if researched:
        return "research", "本轮深研"
    if searched or tools:
        if webbed and not searched:
            return "factual", "本轮联网作答"
        return "factual", "本轮检索作答"
    return "chitchat", "本轮零工具直答"


def partial_answer(messages: list) -> str:
    """轮次耗尽的兜底：从本轮工具观察合成部分结论（react.py partial_answer 同语义）。"""
    lines = ["本轮未能完全办成，已查到的信息："]
    for m in messages_since_last_human(messages):
        if isinstance(m, ToolMessage) and m.content:
            text = m.content if isinstance(m.content, str) else str(m.content)
            lines.append("- " + text[:200])
        if len(lines) >= 6:
            break
    return "\n".join(lines) if len(lines) > 1 else ""


# ---------- 中间件 ----------


class AgentPromptMiddleware(AgentMiddleware):
    """system prompt 动态装配：主体 + 联网准则（能力注入）+ 长期记忆块尾部注入。"""

    def __init__(self, web_search: bool = False) -> None:
        super().__init__()
        self._web = web_search

    def wrap_model_call(self, request, handler):
        mem = request.state.get("mem_block", "")
        return handler(
            request.override(
                system_message=SystemMessage(content=agent_system_prompt(mem, web_search=self._web))
            )
        )


class UsageRecordMiddleware(AgentMiddleware):
    """主循环用量记账（LLMService 预算闸口径；classic 链路在 LLMService 内记）。

    P24-1 兼任观测：每次模型调用打 [llm] agent主循环一行（ms + ctx_profile），
    补 agent 主循环不经 chat_stream/chat_with_tools 封装的埋点盲区；格式含
    chars= 使 log-report.sh 的 ctx_chars 聚合自动吃到主循环数据。
    P27-2 兼任 llm span（tracer 关闭时零开销直通）。
    """

    def __init__(self, llm) -> None:
        super().__init__()
        self._llm = llm

    def wrap_model_call(self, request, handler):
        tracer = current_tracer()
        model_name = str(getattr(request.model, "model_name", "") or "agent")
        in_profile = {
            "msgs": len(request.messages),
            "chars": sum(len(str(m.content)) for m in request.messages),
        }
        if tracer is None:
            return self._run(request, handler, model_name, in_profile)
        with tracer.span("llm", model_name, input=in_profile) as sp:
            resp = self._run(request, handler, model_name, in_profile)
            tokens = 0
            for m in resp.result:
                usage = getattr(m, "usage_metadata", None)
                if usage:
                    tokens += int(usage.get("total_tokens", 0) or 0)
            sp.tokens = tokens or None
            sp.output = {"msgs_out": len(resp.result)}
            return resp

    def _run(self, request, handler, model_name: str, in_profile: dict):
        t0 = time.monotonic()
        resp = handler(request)
        print(
            f"[llm] agent主循环 ms={int((time.monotonic() - t0) * 1000)} "
            f"{ctx_profile(request.messages)}",
            flush=True,
        )
        for m in resp.result:
            usage = getattr(m, "usage_metadata", None)
            if usage:
                self._llm.record_usage(int(usage.get("total_tokens", 0) or 0))
        return resp


class ToolTraceMiddleware(AgentMiddleware):
    """所有工具调用的观测接缝（P27-2）：name/args/结果摘要/ms 自动落 span。

    放中间件栈列表首位=wrap 最外层：Budget/Gate/Limit 拦截件短路返回的
    调用同样留痕——以后加任何工具零观测成本（web_search 检索词盲区的
    根治）。tracer 关闭时零开销直通。
    """

    def wrap_tool_call(self, request, handler):
        tracer = current_tracer()
        if tracer is None:
            return handler(request)
        call = request.tool_call
        args = {k: v for k, v in (call.get("args") or {}).items()}
        with tracer.span("tool", str(call.get("name", "?")), input=args or None) as sp:
            out = handler(request)
            content = getattr(out, "content", out)
            sp.output = {"content": str(content)}
            return out


class TruncationDefenseMiddleware(AgentMiddleware):
    """P10 截断防御铁律（Pi 式）：length+tool_calls 不执行，回填重发。

    官方无现成件；规则平移自 react.py 的 _route_after_agent/_make_truncated_node，
    落点从「图条件边」改为「模型调用包裹层」（handler 重调即回到模型）。
    """

    MAX_REFIRE = 2  # 同一轮最多重发次数（防 length 死循环）

    def _is_truncated(self, resp) -> bool:
        if not resp.result:
            return False
        ai = resp.result[-1]
        if not isinstance(ai, AIMessage) or not ai.tool_calls:
            return False
        meta = getattr(ai, "response_metadata", None) or {}
        return str(meta.get("finish_reason", "") or "") == "length"

    def wrap_model_call(self, request, handler):
        resp = handler(request)
        refires = 0
        while self._is_truncated(resp) and refires < self.MAX_REFIRE:
            ai = resp.result[-1]
            synth = [
                ToolMessage(
                    content=(
                        "输出达到 token 上限被截断，参数可能不完整，本次未执行。"
                        "请重新发起完整调用。"
                    ),
                    tool_call_id=c["id"],
                )
                for c in ai.tool_calls
            ]
            print(f"[agent] 截断防御：length 带工具调用已拦截（第 {refires + 1} 次重发）")
            request = request.override(messages=[*request.messages, ai, *synth])
            resp = handler(request)
            refires += 1
        return resp


class WriteSlotGateMiddleware(AgentMiddleware):
    """写工具槽位门：缺必填参数不执行，emit slot_question + 引导模型收集。"""

    def __init__(self, business) -> None:
        super().__init__()
        self._business = business

    def wrap_tool_call(self, request, handler):
        call = request.tool_call
        name = call.get("name", "")
        flow = FLOW_DEFS.get(name)
        if name not in WRITE_TOOLS or not flow:
            return handler(request)
        args = call.get("args") or {}
        meta = slot_meta(self._business)
        missing = [s for s in flow["required"] if not str(args.get(s, "") or "").strip()]
        if not missing:
            return handler(request)
        slot = missing[0]
        ask = meta[slot]["ask"]
        emit(ev.slot_question_evt(slot, ask))
        return ToolMessage(
            content=(
                f"缺少必填参数 {slot}（{meta[slot]['label']}）。"
                f"请直接向用户提问：「{ask}」收集到答案后再重新发起调用，不要编造参数。"
            ),
            name=name,
            tool_call_id=call.get("id", ""),
        )


class ResearchLimitMiddleware(AgentMiddleware):
    """deep_research 单轮限 1 次代码闸（flash 无视否定指令必须代码兜底）。"""

    LIMIT = 1

    def wrap_tool_call(self, request, handler):
        if request.tool_call.get("name") != "deep_research":
            return handler(request)
        msgs = request.state.get("messages") or []
        used = sum(
            1
            for m in messages_since_last_human(msgs)
            if isinstance(m, ToolMessage) and m.name == "deep_research"
        )
        if used >= self.LIMIT:
            return ToolMessage(
                content="本轮 deep_research 调用次数已达上限（1 次）。请基于已有检索结果综合作答。",
                name="deep_research",
                tool_call_id=request.tool_call.get("id", ""),
            )
        return handler(request)


class WebSearchBudgetMiddleware(AgentMiddleware):
    """web_search 每日次数闸（IQS 按次计费，agent 循环失控即烧钱——代码闸兜底）。

    进程内 date 键计数：单进程部署语义足够（与 token 预算闸同场景）；多进程
    部署时升级走 usage 台账。超限回执引导模型基于已有信息作答，不崩主链路。
    """

    def __init__(self, daily_limit: int = 200) -> None:
        super().__init__()
        self._limit = max(0, daily_limit)
        self._day = ""
        self._used = 0

    def wrap_tool_call(self, request, handler):
        if request.tool_call.get("name") != "web_search":
            return handler(request)
        today = today_iso()
        if today != self._day:  # 跨日重置（首次调用同路初始化）
            self._day, self._used = today, 0
        if self._used >= self._limit:
            print(
                f"[websearch] 今日联网检索已达上限（{self._limit} 次），本次拦截",
                flush=True,
            )
            return ToolMessage(
                content=(
                    f"今日联网检索额度已用完（上限 {self._limit} 次/日）。"
                    "请基于已有信息回答，并告知用户今日无法联网核实。"
                ),
                name="web_search",
                tool_call_id=request.tool_call.get("id", ""),
            )
        self._used += 1
        return handler(request)


def _cjk_bigrams(s: str) -> set[str]:
    """二字滑窗 bigram 集（丢词判定用，免分词器）。"""
    return {s[i : i + 2] for i in range(len(s) - 1)}


class SearchQueryGuardMiddleware(AgentMiddleware):
    """search_knowledge / web_search 完全丢原词时代码兜底拼回原问题（P24-3）。

    GLM 无视否定指令是已知坑（docstring 引导是软防线，本件是硬防线）。
    只在原问题与检索词的 bigram 交集为空（完全丢词）时干预——拼接是增补
    不是替换，召回只增不减；有重合（保住核心实体）则放行，避免口语原话
    摊薄 BM25 关键词权重。deep_research 不拦：sub 是 plan 拆解产物本非原话。
    web_search 同守卫（P26）：联网检索词丢原词同样浪费一次计费调用。
    """

    def wrap_tool_call(self, request, handler):
        call = request.tool_call
        if call.get("name") not in ("search_knowledge", "web_search"):
            return handler(request)
        args = call.get("args") or {}
        query = str(args.get("query", "") or "")
        question = ""
        for m in reversed(request.state.get("messages") or []):
            if isinstance(m, HumanMessage):
                question = str(m.content)
                break
        if query and question and not (_cjk_bigrams(question) & _cjk_bigrams(query)):
            call = {**call, "args": {**args, "query": f"{question} {query}"}}
            print(f"[rag] 检索词与原问题零重合，已拼回原问题：q={query!r}", flush=True)
            return handler(request.override(tool_call=call))
        return handler(request)


class PendingActionMiddleware(AgentMiddleware):
    """写工具参数齐 → 确认摘要（pending_action 事件 + 摘要文案），先于 HITL 中断。"""

    def __init__(self, business) -> None:
        super().__init__()
        self._business = business

    def after_model(self, state, runtime) -> dict[str, Any] | None:
        ai = last_ai_message(state.get("messages") or [])
        if ai is None or not ai.tool_calls:
            return None
        meta = slot_meta(self._business)
        for call in ai.tool_calls:
            name = call.get("name", "")
            if name not in WRITE_TOOLS or not write_call_ready(self._business, call):
                continue
            norm = normalize_tool_args(meta, name, call.get("args") or {})
            pa, text, _note = build_confirm({"tx_tool": name, "tx_slots": norm}, self._business)
            emit(ev.status_evt("已整理办理信息，等待确认…"))
            emit(pa)
            emit(ev.answer_evt(text))
            break  # 一次模型响应只发一份摘要（多写调用罕见，首个为准）
        return None


class RouteEventMiddleware(AgentMiddleware):
    """after_agent 合成 effective route 事件（两段式第二段；guard block/meta 已发过）。"""

    def after_agent(self, state, runtime) -> dict[str, Any] | None:
        if state.get("guard_action") in ("block", "meta"):
            return None
        route, reason = effective_route(state.get("messages") or [])
        emit(
            ev.route_decision_evt(
                {
                    "route": route,
                    "reason": reason,
                    "layer": "effective",
                    "confidence": 0.9,
                    "by_llm": True,
                }
            )
        )
        return None


class _StreamingChatModel(BaseChatModel):
    """流式代理模型（P30）：对外保持 invoke 契约，内部跑 inner.stream。

    request.model 是未绑定 tools 的原始模型（tools/system message 由官方
    handler 拼装后落到本代理）——所以走「代理 + 官方 handler」而不是裸调
    request.model.stream：bind_tools / tool_choice / 模型设置零漂移。
    逐 chunk 回调 on_delta（emit answer_delta），AIMessageChunk 聚合返回，
    usage_metadata / finish_reason / tool_calls 天然保留在聚合消息上。
    """

    inner: Any = None
    on_delta: Any = None
    on_round_done: Any = None

    @property
    def _llm_type(self) -> str:
        return "streaming-proxy"

    def bind_tools(self, tools, **kwargs):
        # 工具绑定下沉 inner，返回包住已绑定模型的新代理（回调原样携带）
        return _StreamingChatModel(
            inner=self.inner.bind_tools(tools, **kwargs),
            on_delta=self.on_delta,
            on_round_done=self.on_round_done,
        )

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):  # noqa: ARG002
        # BaseChatModel.stream 产出 AIMessageChunk（消息本身，非 GenerationChunk）
        agg: AIMessageChunk | None = None
        emitted = False
        for chunk in self.inner.stream(messages, stop=stop, **kwargs):
            agg = chunk if agg is None else agg + chunk
            if self.on_delta is not None:
                text = ai_content_text(chunk)
                if text:
                    emitted = True
                    self.on_delta(text)
        result = agg if agg is not None else AIMessageChunk(content="")
        if self.on_round_done is not None:
            self.on_round_done(result, emitted)
        return ChatResult(generations=[ChatGeneration(message=result)])


class StreamingAnswerMiddleware(AgentMiddleware):
    """agent 主循环答案 token 级流式（P30）。

    位置=栈列表最末（SummarizationMiddleware 之后）＝wrap 最内层：压缩后的
    messages 才进流式，Summarization 内部的摘要小模型调用不经代理不误发。
    中间轮（聚合出 tool_calls）已发文本由 answer_reset 撤回（前端转存为
    step）；最终轮文本经 after_model 写 state.answer_streamed，agent_done
    等价校验防全文重发（轮次耗尽/错误轮文本不等价 → 照发，兜住漏发风险）。
    """

    def wrap_model_call(self, request, handler):
        proxy = _StreamingChatModel(
            inner=request.model,
            on_delta=lambda text: emit(ev.answer_evt(text)),
            on_round_done=self._on_round_done,
        )
        return handler(request.override(model=proxy))

    @staticmethod
    def _on_round_done(agg, emitted: bool) -> None:
        # 只撤回确实发过文本的轮（纯 tool_calls 轮零 delta，无需 reset）
        if emitted and getattr(agg, "tool_calls", None):
            emit(ev.answer_reset_evt())

    def after_model(self, state, runtime) -> dict[str, Any] | None:
        ai = last_ai_message(state.get("messages") or [])
        if ai is None or ai.tool_calls:
            return None
        # 最终轮：该文本已由 wrap_model_call 的代理逐 delta 发出
        return {"answer_streamed": ai_content_text(ai)}
