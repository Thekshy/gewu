"""agent-first 链路测试的公共假件：脚本化 chat 模型 / LLM 替身 / 检索假件。"""

from __future__ import annotations

from langchain_core.language_models.chat_models import BaseChatModel, ChatGeneration, ChatResult
from langchain_core.messages import AIMessage, AIMessageChunk

from gewu.rag.store import Hit, Scored


class FakeToolChatModel(BaseChatModel):
    """按序吐出预置 AIMessage（含 tool_calls / finish_reason）的假 chat 模型。

    bind_tools 原样吞下（create_agent 装配需要）；脚本耗尽后返回空 content。
    """

    responses: list = []

    @property
    def _llm_type(self) -> str:
        return "fake-tool-chat"

    def bind_tools(self, tools, **kwargs):  # noqa: ARG002
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):  # noqa: ARG002
        msg = self.responses.pop(0) if self.responses else AIMessage(content="")
        return ChatResult(generations=[ChatGeneration(message=msg)])


class FakeAgentLLM:
    """LLMService 替身：guard/解析类调用返回固定文本；主循环模型走脚本。"""

    def __init__(
        self,
        chat_replies: list[str] | None = None,
        has_key: bool = False,
        script: list[AIMessage] | None = None,
    ) -> None:
        self._replies = list(chat_replies or [])
        self._script = list(script or [])
        self._key = has_key
        self.recorded: list[int] = []

    def has_key(self) -> bool:
        return self._key

    def chat(self, messages, *, small=False, json_mode=False, temperature=0.0, max_tokens=2048):
        if self._replies:
            return self._replies.pop(0)
        return ""

    def chat_stream(self, messages, *, small=False, temperature=0.0, max_tokens=2048):
        return FakeStream(["答"])

    def embed(self, texts):
        return [[0.0] * 2048]

    def agent_model(self, *, small: bool = False, max_tokens: int = 1200):
        # 主模型共享脚本列表（pop 消费，跨轮线性推进）；摘要小模型恒空
        return FakeToolChatModel(responses=[] if small else self._script)

    def record_usage(self, total_tokens: int) -> None:
        self.recorded.append(total_tokens)


class FakeStreamChunksModel(BaseChatModel):
    """按轮吐 chunk 流的假模型（P30 流式测试）：脚本元素 = list[AIMessageChunk]。

    stream() 直接 yield AIMessageChunk（对齐 BaseChatModel.stream 的真实契约：
    产出消息本身而非 GenerationChunk）；_generate 聚合返回（STREAM_ANSWER
    关闭时的 invoke 路径）。脚本耗尽返回空 content 单 chunk。
    """

    rounds: list = []

    @property
    def _llm_type(self) -> str:
        return "fake-stream-chunks"

    def bind_tools(self, tools, **kwargs):  # noqa: ARG002
        return self

    def _round(self) -> list[AIMessageChunk]:
        return self.rounds.pop(0) if self.rounds else [AIMessageChunk(content="")]

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):  # noqa: ARG002
        agg: AIMessageChunk | None = None
        for c in self._round():
            agg = c if agg is None else agg + c
        return ChatResult(generations=[ChatGeneration(message=agg)])

    def stream(self, messages, stop=None, run_manager=None, **kwargs):  # noqa: ARG002
        yield from self._round()


class FakeStreamAgentLLM(FakeAgentLLM):
    """FakeAgentLLM 的流式版：主循环模型逐 chunk 吐（rounds 元素=chunk 列表）。"""

    def __init__(self, rounds: list | None = None, **kw) -> None:
        super().__init__(**kw)
        self._rounds = list(rounds or [])

    def agent_model(self, *, small: bool = False, max_tokens: int = 1200):
        return FakeStreamChunksModel(rounds=[] if small else self._rounds)


class FakeStream:
    def __init__(self, deltas: list[str]) -> None:
        self._deltas = deltas
        self.finish_reason = "stop"

    def __iter__(self):
        return iter(self._deltas)


class FakeRetriever:
    def __init__(self, hits: list[Hit] | None = None) -> None:
        self.hits = hits or []
        self.calls: list[str] = []
        self.k = 5

    def search(self, query: str, k: int = 0, *, expand: bool = True) -> list[Hit]:
        self.calls.append(query)
        return list(self.hits)

    def bm25_search(self, query: str, k: int) -> list[Scored]:
        return []

    def vector_search(self, query_vec, k: int) -> list[Scored]:
        return []

    def chunk_rows(self, ids):
        return {}

    def parent_rows(self, ids):
        return {}

    def doc_meta_map(self, ids):
        return {}

    def has_embeddings(self) -> bool:
        return True


def make_hit(
    chunk_id: int = 1, doc_id: str = "0001-transfer", title: str = "转专业管理办法"
) -> Hit:
    return Hit(
        chunk_id=chunk_id,
        doc_id=doc_id,
        seq=0,
        text="申请转专业的条件包括：在校期间无未通过课程。",
        title=title,
        source="教务处",
        section_path="申请条件",
    )
