"""长期记忆（移植自 Go internal/agent/memory.go；P21-2 自 SQLite 迁 PG）。

分层：
- memory_fact     用户级结构化事实（profile|preference|constraint），LLM 异步抽取
  后 UPSERT 覆盖（同 key 只留最新值），装配时注入最近 N 条；
- memory_episodic 会话级原始对话（每轮 user/assistant 两条），装配时取本会话
  最近 N 条作「近期对话要点」，也是上下文补全（ResolveQuery）的输入。

记忆是增强不是依赖：无记忆库/无数据/LLM 失败时主链路结构与行为不变。
"""

from __future__ import annotations

from dataclasses import dataclass

from psycopg_pool import ConnectionPool

from gewu.llm.service import LLMService

MAX_FACTS_IN_CONTEXT = 20  # 注入 prompt 的事实条数上限


@dataclass(frozen=True)
class Fact:
    kind: str  # profile | preference | constraint
    key: str
    value: str


class MemoryStore:
    """PG 长期记忆存储（psycopg 连接池；user_id 自 P21 起为真实 email）。"""

    def __init__(self, dsn: str) -> None:
        self._pool = ConnectionPool(dsn, min_size=1, max_size=4, open=True, name="gewu-mem")
        with self._pool.connection() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS memory_episodic (
                    id         BIGSERIAL PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    user_id    TEXT NOT NULL,
                    kind       TEXT NOT NULL,
                    text       TEXT NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
                );
                CREATE INDEX IF NOT EXISTS idx_memory_episodic_session
                    ON memory_episodic(session_id, id);
                CREATE TABLE IF NOT EXISTS memory_fact (
                    user_id    TEXT NOT NULL,
                    kind       TEXT NOT NULL,
                    key        TEXT NOT NULL,
                    value      TEXT NOT NULL,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    PRIMARY KEY (user_id, kind, key)
                );
                """
            )

    def close(self) -> None:
        self._pool.close()

    def wipe(self) -> None:
        """测试专用：清双表 + episodic 序列归零。"""
        with self._pool.connection() as conn:
            conn.execute("DELETE FROM memory_episodic")
            conn.execute("DELETE FROM memory_fact")
            conn.execute("SELECT setval('memory_episodic_id_seq', 1, false)")

    # ---------- fact ----------

    def upsert_facts(self, user_id: str, facts: list[Fact]) -> None:
        """事实写入：同 (user_id,kind,key) 新值覆盖旧值。"""
        rows = [
            (user_id, f.kind, f.key, f.value) for f in facts if f.key.strip() and f.value.strip()
        ]
        if not rows:
            return
        with self._pool.connection() as conn, conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO memory_fact (user_id, kind, key, value, updated_at)"
                " VALUES (%s, %s, %s, %s, now())"
                " ON CONFLICT (user_id, kind, key)"
                " DO UPDATE SET value = excluded.value, updated_at = now()",
                rows,
            )

    def recent_facts(self, user_id: str, n: int = MAX_FACTS_IN_CONTEXT) -> list[Fact]:
        """按固化时间倒序取最近 n 条（平局按 kind,key 稳定排序）。"""
        with self._pool.connection() as conn:
            rows = conn.execute(
                "SELECT kind, key, value FROM memory_fact WHERE user_id = %s"
                " ORDER BY updated_at DESC, kind, key LIMIT %s",
                (user_id, n),
            ).fetchall()
        return [Fact(r[0], r[1], r[2]) for r in rows]

    def all_facts(self, user_id: str) -> list[Fact]:
        """全量事实（/memory 面板用；按 kind,key 稳定排序便于分组渲染）。"""
        with self._pool.connection() as conn:
            rows = conn.execute(
                "SELECT kind, key, value FROM memory_fact WHERE user_id = %s ORDER BY kind, key",
                (user_id,),
            ).fetchall()
        return [Fact(r[0], r[1], r[2]) for r in rows]

    def delete_fact(self, user_id: str, kind: str, key: str) -> bool:
        """删除单条事实（复合主键定位；P22 记忆面板）。"""
        with self._pool.connection() as conn:
            row = conn.execute(
                "DELETE FROM memory_fact WHERE user_id = %s AND kind = %s AND key = %s"
                " RETURNING key",
                (user_id, kind, key),
            ).fetchone()
        return row is not None

    # ---------- episodic ----------

    def append_episode(self, session_id: str, user_id: str, kind: str, text: str) -> None:
        if not text.strip():
            return
        with self._pool.connection() as conn:
            conn.execute(
                "INSERT INTO memory_episodic (session_id, user_id, kind, text)"
                " VALUES (%s, %s, %s, %s)",
                (session_id, user_id, kind, text),
            )

    def recent_episodes(self, session_id: str, n: int) -> list[str]:
        """取本会话最近 n 条对话文本（时间正序：旧 → 新），带「用户/助手」前缀。"""
        with self._pool.connection() as conn:
            rows = conn.execute(
                "SELECT kind, text FROM ("
                "  SELECT kind, text, id FROM memory_episodic"
                "  WHERE session_id = %s ORDER BY id DESC LIMIT %s"
                ") t ORDER BY id ASC",
                (session_id, n),
            ).fetchall()
        return [("助手" if r[0] == "assistant" else "用户") + "：" + r[1] for r in rows]

    def episodes_for_session(self, session_id: str) -> list[dict]:
        """本会话全部对话轮（时间正序 [{role,text}]）——历史恢复的 classic 兜底（P22）。"""
        with self._pool.connection() as conn:
            rows = conn.execute(
                "SELECT kind, text FROM memory_episodic WHERE session_id = %s ORDER BY id ASC",
                (session_id,),
            ).fetchall()
        return [{"role": r[0], "text": r[1]} for r in rows]

    def delete_episodes(self, session_id: str) -> None:
        """删会话连带清理（P22 Q4：用户删会话=清干净）。"""
        with self._pool.connection() as conn:
            conn.execute("DELETE FROM memory_episodic WHERE session_id = %s", (session_id,))


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
        from gewu.jsonx import parse_json_object  # noqa: PLC0415

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


# ConsolidatePrompt 事实抽取提示词（glm-5.3-flash，JSONMode）。
CONSOLIDATE_PROMPT = """从对话中抽取关于该用户的稳定事实、偏好或约束（如专业、年级、绩点、姓名、宿舍、目标院校/方向）。
只抽取明确表达或可直接确定的信息，不要推测。key 用简短英文标识（如 major、grade、gpa、dorm、goal），
value 保留用户原表述。每条含 kind（profile=身份事实 / preference=偏好 / constraint=约束条件）。
只输出 JSON：{"facts":[{"kind":"profile","key":"major","value":"计算机科学"}]}；没有可抽取信息时输出 {"facts":[]}"""
