"""长期记忆（移植自 Go internal/agent/memory.go，SQLite 版）。

分层：
- memory_fact     用户级结构化事实（profile|preference|constraint），LLM 异步抽取
  后 UPSERT 覆盖（同 key 只留最新值），装配时注入最近 N 条；
- memory_episodic 会话级原始对话（每轮 user/assistant 两条），装配时取本会话
  最近 N 条作「近期对话要点」，也是上下文补全（ResolveQuery）的输入。

记忆是增强不是依赖：无记忆库/无数据/LLM 失败时主链路结构与行为不变。
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from pathlib import Path

from gewu.agent.prompts import CONSOLIDATE_PROMPT
from gewu.llm.service import LLMService

MAX_FACTS_IN_CONTEXT = 20  # 注入 prompt 的事实条数上限


@dataclass(frozen=True)
class Fact:
    kind: str  # profile | preference | constraint
    key: str
    value: str


class MemoryStore:
    """SQLite 长期记忆存储（单连接 + 锁）。"""

    def __init__(self, path: Path) -> None:
        import sqlite3  # noqa: PLC0415

        self._lock = threading.Lock()
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS memory_episodic (
                    id         INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    user_id    TEXT NOT NULL,
                    kind       TEXT NOT NULL,
                    text       TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT (datetime('now'))
                );
                CREATE INDEX IF NOT EXISTS idx_memory_episodic_session
                    ON memory_episodic(session_id, id);
                CREATE TABLE IF NOT EXISTS memory_fact (
                    user_id    TEXT NOT NULL,
                    kind       TEXT NOT NULL,
                    key        TEXT NOT NULL,
                    value      TEXT NOT NULL,
                    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
                    PRIMARY KEY (user_id, kind, key)
                );
                """
            )
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ---------- fact ----------

    def upsert_facts(self, user_id: str, facts: list[Fact]) -> None:
        """事实写入：同 (user_id,kind,key) 新值覆盖旧值。"""
        with self._lock:
            for f in facts:
                if not f.key.strip() or not f.value.strip():
                    continue
                self._conn.execute(
                    "INSERT INTO memory_fact (user_id, kind, key, value, updated_at) "
                    "VALUES (?, ?, ?, ?, datetime('now')) "
                    "ON CONFLICT(user_id, kind, key) "
                    "DO UPDATE SET value = excluded.value, updated_at = datetime('now')",
                    (user_id, f.kind, f.key, f.value),
                )
            self._conn.commit()

    def recent_facts(self, user_id: str, n: int = MAX_FACTS_IN_CONTEXT) -> list[Fact]:
        """按固化时间倒序取最近 n 条（平局按 kind,key 稳定排序）。"""
        with self._lock:
            rows = self._conn.execute(
                "SELECT kind, key, value FROM memory_fact WHERE user_id = ? "
                "ORDER BY updated_at DESC, kind, key LIMIT ?",
                (user_id, n),
            ).fetchall()
        return [Fact(r["kind"], r["key"], r["value"]) for r in rows]

    # ---------- episodic ----------

    def append_episode(self, session_id: str, user_id: str, kind: str, text: str) -> None:
        if not text.strip():
            return
        with self._lock:
            self._conn.execute(
                "INSERT INTO memory_episodic (session_id, user_id, kind, text) VALUES (?, ?, ?, ?)",
                (session_id, user_id, kind, text),
            )
            self._conn.commit()

    def recent_episodes(self, session_id: str, n: int) -> list[str]:
        """取本会话最近 n 条对话文本（时间正序：旧 → 新），带「用户/助手」前缀。"""
        with self._lock:
            rows = self._conn.execute(
                "SELECT kind, text FROM ("
                "  SELECT kind, text, id FROM memory_episodic"
                "  WHERE session_id = ? ORDER BY id DESC LIMIT ?"
                ") ORDER BY id ASC",
                (session_id, n),
            ).fetchall()
        return [("助手" if r["kind"] == "assistant" else "用户") + "：" + r["text"] for r in rows]


# ---------- 记忆块装配与固化（Go memory.go 的 Deps 方法等价物） ----------


def memory_block(memory: MemoryStore | None, user_id: str, session_id: str) -> str:
    """组装注入 prompt 的长期记忆块（fact + 本会话近期对话要点）。空数据返回空串。"""
    if memory is None:
        return ""
    lines: list[str] = []
    try:
        for f in memory.recent_facts(user_id):
            lines.append(f"- {f.kind}/{f.key}：{f.value}")
    except Exception as e:  # noqa: BLE001 - 按无记忆处理
        print(f"[agent] 记忆读取失败（按无记忆处理）：{e}")
    try:
        episodes = memory.recent_episodes(session_id, 4)
        if episodes:
            lines.append("近期对话要点：")
            lines.extend(episodes)
    except Exception as e:  # noqa: BLE001
        print(f"[agent] 会话历史读取失败（按无记忆处理）：{e}")
    return "\n".join(lines)


def consolidate(
    memory: MemoryStore, llm: LLMService, user_id: str, session_id: str, question: str, answer: str
) -> None:
    """一轮对话的固化：episodic 原文入库 + LLM 抽取稳定事实 UPSERT（失败不影响主链路）。"""
    try:
        memory.append_episode(session_id, user_id, "user", question)
        memory.append_episode(session_id, user_id, "assistant", answer)
    except Exception as e:  # noqa: BLE001
        print(f"[agent] episodic 写入失败（不影响主链路）：{e}")
        return
    if not llm.has_key():
        return
    try:
        from gewu.agent.jsonx import parse_json_object  # noqa: PLC0415

        raw = llm.chat(
            [
                ("system", CONSOLIDATE_PROMPT),
                ("user", f"用户：{question}\n助手：{answer[:800]}"),
            ],
            json_mode=True,
            small=True,
            max_tokens=300,
        )
        obj = parse_json_object(raw)
        facts = [
            Fact(str(m.get("kind", "")), str(m.get("key", "")), str(m.get("value", "")))
            for m in obj.get("facts", [])
            if isinstance(m, dict)
        ]
        if facts:
            memory.upsert_facts(user_id, facts)
    except Exception as e:  # noqa: BLE001 - 抽取失败放弃本轮（原文已留存）
        print(f"[agent] 记忆固化失败（不影响主链路）：{e}")
