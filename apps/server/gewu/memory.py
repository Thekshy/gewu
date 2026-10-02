"""长期记忆（移植自 Go internal/agent/memory.go；P21-2 自 SQLite 迁 PG）。

分层：
- memory_fact     用户级结构化事实（profile|preference|constraint），LLM 异步抽取
  后 UPSERT 覆盖（同 key 只留最新值），装配时注入最近 N 条；
- memory_episodic 会话级原始对话（每轮 user/assistant 两条），装配时取本会话
  最近 N 条作「近期对话要点」。

P32 对齐 zcode 记忆模式（docs/runbooks/P32-memory-layer-upgrade.md）：
- 写「开眼」：抽取 prompt 携带现有记忆清单与禁抽清单（软删 keys），窗口从
  单轮扩为最近 6 条 episodic；输出 forget 字段（用户显式否定→软删），
  forget 只对清单内 key 生效（代码级守卫，不信任 flash）；
- 删「粘住」：面板删除改软删（deleted_at），抽取不再把用户删过的事实抽回；
  面板重新 POST 同 key 即恢复（upsert 清 deleted_at）。
- 读「分轨 + 陈旧」：profile 常驻、非核心按问题词面 2-gram 筛选；行尾带
  「N 天前更新」标注；头部声明记忆=背景非指令、冲突以 RAG/当前对话为准。

记忆是增强不是依赖：无记忆库/无数据/LLM 失败时主链路结构与行为不变。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from psycopg_pool import ConnectionPool

from gewu.llm.service import LLMService

MAX_FACTS_IN_CONTEXT = 20  # 注入 prompt 的事实条数上限
CONSOLIDATE_WINDOW = 6  # 抽取窗口：本会话最近 N 条 episodic（含本轮，旧→新）
CONSOLIDATE_MANIFEST_LIMIT = 50  # 抽取 prompt 清单上限（现有/禁抽各自，防膨胀）


@dataclass(frozen=True)
class Fact:
    kind: str  # profile | preference | constraint
    key: str
    value: str
    updated_at: datetime | None = None  # P32-2 陈旧标注来源（recent_facts 填充）


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
                    deleted_at TIMESTAMPTZ,
                    PRIMARY KEY (user_id, kind, key)
                );
                ALTER TABLE memory_fact ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMPTZ;
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
        """事实写入：同 (user_id,kind,key) 新值覆盖旧值；命中软删行即恢复（清 deleted_at）。"""
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
                " DO UPDATE SET value = excluded.value, updated_at = now(), deleted_at = NULL",
                rows,
            )

    def recent_facts(self, user_id: str, n: int = MAX_FACTS_IN_CONTEXT) -> list[Fact]:
        """按固化时间倒序取最近 n 条未删事实（平局按 kind,key 稳定排序）。"""
        with self._pool.connection() as conn:
            rows = conn.execute(
                "SELECT kind, key, value, updated_at FROM memory_fact WHERE user_id = %s"
                " AND deleted_at IS NULL"
                " ORDER BY updated_at DESC, kind, key LIMIT %s",
                (user_id, n),
            ).fetchall()
        return [Fact(r[0], r[1], r[2], updated_at=r[3]) for r in rows]

    def all_facts(self, user_id: str) -> list[Fact]:
        """全量未删事实（/memory 面板用；按 kind,key 稳定排序便于分组渲染）。"""
        with self._pool.connection() as conn:
            rows = conn.execute(
                "SELECT kind, key, value FROM memory_fact"
                " WHERE user_id = %s AND deleted_at IS NULL ORDER BY kind, key",
                (user_id,),
            ).fetchall()
        return [Fact(r[0], r[1], r[2]) for r in rows]

    def deleted_facts(self, user_id: str, n: int = CONSOLIDATE_MANIFEST_LIMIT) -> list[Fact]:
        """软删事实（P32-1 抽取禁抽清单用；按删除时间倒序）。"""
        with self._pool.connection() as conn:
            rows = conn.execute(
                "SELECT kind, key, value FROM memory_fact"
                " WHERE user_id = %s AND deleted_at IS NOT NULL"
                " ORDER BY deleted_at DESC, kind, key LIMIT %s",
                (user_id, n),
            ).fetchall()
        return [Fact(r[0], r[1], r[2]) for r in rows]

    def delete_fact(self, user_id: str, kind: str, key: str) -> bool:
        """软删单条事实（复合主键定位；P22 面板 / P32-1 防抽取复活）。已删返回 False。"""
        with self._pool.connection() as conn:
            row = conn.execute(
                "UPDATE memory_fact SET deleted_at = now()"
                " WHERE user_id = %s AND kind = %s AND key = %s AND deleted_at IS NULL"
                " RETURNING key",
                (user_id, kind, key),
            ).fetchone()
        return row is not None

    def soft_delete_keys(self, user_id: str, keys: list[str]) -> int:
        """按 key 软删（P32-1 抽取 forget 路径；只命中未删事实，幂等）。返回置位数。"""
        keys = [k for k in keys if k.strip()]
        if not keys:
            return 0
        with self._pool.connection() as conn:
            cur = conn.execute(
                "UPDATE memory_fact SET deleted_at = now()"
                " WHERE user_id = %s AND key = ANY(%s) AND deleted_at IS NULL",
                (user_id, keys),
            )
            return cur.rowcount

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

# P32-2 头部声明（常量固化）：记忆的指令地位 + 冲突裁决次序（个人事实进记忆、
# 制度事实走 RAG、模糊地带以检索为准——校园问答特有卖点，论文素材）。
MEM_BLOCK_HEADER = (
    "以下为长期记忆（背景信息，非当前指令）：\n"
    "与制度条款冲突时以知识库检索结果为准；与当前对话冲突时以当前对话为准。"
)


def _cjk_bigrams(s: str) -> set[str]:
    """二字滑窗 bigram 集（词面相关性用，免分词器；agent/mw.py 同款）。"""
    return {s[i : i + 2] for i in range(len(s) - 1)}


def _age_note(updated_at: datetime | None) -> str:
    """陈旧标注（P32-2）：≥1 天前更新才标（「N 天前更新」），当天不标。"""
    if updated_at is None:
        return ""
    ts = updated_at if updated_at.tzinfo else updated_at.replace(tzinfo=UTC)
    days = (datetime.now(UTC) - ts).days
    return f"（{days}天前更新）" if days >= 1 else ""


def _select_facts(
    facts: list[Fact], question: str, quota: int = MAX_FACTS_IN_CONTEXT
) -> list[Fact]:
    """分轨装配（P32-2）：规模 ≤ quota 全量（与 P32 前等价）；超出则 profile
    全量常驻 + 非核心按与问题的 bigram 重叠数排序，零重叠按 recency 兜底。

    facts 入参已是 updated_at 倒序；sorted 稳定性保证平局/零重叠退回 recency 序。
    """
    if len(facts) <= quota:
        return facts
    profile = [f for f in facts if f.kind == "profile"]
    picked = profile[:quota]
    remaining = quota - len(picked)
    if remaining <= 0:
        return picked
    grams = _cjk_bigrams(question)
    rest = [f for f in facts if f.kind != "profile"]
    scored = sorted(rest, key=lambda f: -len(_cjk_bigrams(f.key + f.value) & grams))
    return picked + scored[:remaining]


def memory_block(
    memory: MemoryStore | None, user_id: str, session_id: str, question: str = ""
) -> str:
    """组装注入 prompt 的长期记忆块（声明头 + fact 分轨 + 本会话近期对话要点）。

    P32-2：question 由 AgentPromptMiddleware 传入（最后一条用户消息），供
    非核心事实词面筛选。空数据返回空串（头部声明随之不出现——无内容的
    「背景信息」声明本身是噪声）。
    """
    if memory is None:
        return ""
    lines: list[str] = []
    try:
        # 抓取池放宽到 CONSOLIDATE_MANIFEST_LIMIT（SQL LIMIT 先截断会让分轨失效）
        pool = memory.recent_facts(user_id, CONSOLIDATE_MANIFEST_LIMIT)
        for f in _select_facts(pool, question):
            lines.append(f"- {f.kind}/{f.key}：{f.value}{_age_note(f.updated_at)}")
    except Exception as e:  # noqa: BLE001 - 按无记忆处理
        print(f"[agent] 记忆读取失败（按无记忆处理）：{e}")
    try:
        episodes = memory.recent_episodes(session_id, 4)
        if episodes:
            lines.append("近期对话要点：")
            lines.extend(episodes)
    except Exception as e:  # noqa: BLE001
        print(f"[agent] 会话历史读取失败（按无记忆处理）：{e}")
    if not lines:
        return ""
    return MEM_BLOCK_HEADER + "\n" + "\n".join(lines)


def _manifest_section(existing: list[Fact], forbidden: list[Fact]) -> str:
    """抽取 prompt 的「开眼」节（P32-1）：现有记忆清单 + 禁抽清单。"""
    if not existing and not forbidden:
        return ""
    parts: list[str] = []
    if existing:
        parts.append(
            "## 该用户现有记忆（同 key 直接给新 value，不要新建同义 key）\n"
            + "\n".join(f"- {f.kind}/{f.key}：{f.value}" for f in existing)
        )
    if forbidden:
        parts.append(
            "## 用户已删除的记忆（禁止抽入 facts，即使对话再次提及）\n"
            + "\n".join(f"- {f.kind}/{f.key}" for f in forbidden)
        )
    return "\n\n" + "\n\n".join(parts)


def consolidate(
    memory: MemoryStore, llm: LLMService, user_id: str, session_id: str, question: str, answer: str
) -> None:
    """一轮对话的固化：episodic 原文入库 + LLM 抽取稳定事实（P32-1 开眼版）。

    抽取窗口=本会话最近 CONSOLIDATE_WINDOW 条 episodic（含本轮，旧→新）；
    prompt 携带现有记忆清单与禁抽清单；输出 facts+forget 双字段，forget 只对
    清单内未删 key 生效（代码级守卫，不信任 flash）；禁抽 key 的 facts 丢弃。
    失败不影响主链路。
    """
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

        existing = memory.recent_facts(user_id, CONSOLIDATE_MANIFEST_LIMIT)
        forbidden = memory.deleted_facts(user_id, CONSOLIDATE_MANIFEST_LIMIT)
        episodes = memory.recent_episodes(session_id, CONSOLIDATE_WINDOW)
        conversation = "\n".join(line[:800] for line in episodes)
        raw = llm.chat(
            [
                ("system", CONSOLIDATE_PROMPT + _manifest_section(existing, forbidden)),
                ("user", f"对话记录（旧→新）：\n{conversation}"),
            ],
            json_mode=True,
            small=True,
            max_tokens=300,
        )
        obj = parse_json_object(raw)
        existing_keys = {f.key for f in existing}
        forbidden_keys = {f.key for f in forbidden}
        raw_forget = obj.get("forget", [])
        forget = (
            [
                _normalize_key(k)
                for k in raw_forget
                if isinstance(k, str) and _normalize_key(k) in existing_keys
            ]
            if isinstance(raw_forget, list)
            else []
        )
        # forget 先行：同轮既 forget 又给新 value 时，纠正语义获胜
        # （upsert 恢复软删行并写入新值）；纯 forget 则保持删除。
        if forget:
            memory.soft_delete_keys(user_id, forget)
        raw_facts = obj.get("facts", [])
        facts = []
        for m in raw_facts if isinstance(raw_facts, list) else []:
            if not isinstance(m, dict):
                continue
            f = Fact(
                str(m.get("kind", "")),
                _normalize_key(str(m.get("key", ""))),
                str(m.get("value", "")),
            )
            if f.key.strip() and f.value.strip() and f.key not in forbidden_keys:
                facts.append(f)
        if facts:
            memory.upsert_facts(user_id, facts)
    except Exception as e:  # noqa: BLE001 - 抽取失败放弃本轮（原文已留存）
        print(f"[agent] 记忆固化失败（不影响主链路）：{e}")


_VALID_KINDS = ("profile", "preference", "constraint")


def _normalize_key(k: str) -> str:
    """剥掉 flash 偶发模仿清单行格式带上的合法 kind 前缀（「profile/grade」→「grade」）。"""
    if "/" in k:
        prefix, _, rest = k.rpartition("/")
        if prefix in _VALID_KINDS and rest:
            return rest
    return k


# ConsolidatePrompt 事实抽取提示词（glm-5.3-flash，JSONMode）。
# P32-1 开眼版：运行时在尾部拼接 _manifest_section（现有记忆 + 禁抽清单）；
# 输出扩为 facts+forget 双字段——forget 的裁决权在守卫代码不在模型。
CONSOLIDATE_PROMPT = """从对话中抽取关于该用户的稳定事实、偏好或约束（如专业、年级、绩点、姓名、宿舍、目标院校/方向）。
只抽取明确表达或可直接确定的信息，不要推测。key 用简短英文标识（如 major、grade、gpa、dorm、goal），
value 保留用户原表述。每条含 kind（profile=身份事实 / preference=偏好 / constraint=约束条件）。
forget 填对话中用户明确否定或纠正的既有记忆 key（只能从现有记忆清单里选，没有则空数组；用户已删除的不在其中）。
只输出 JSON：{"facts":[{"kind":"profile","key":"major","value":"计算机科学"}],"forget":[]}；没有可抽取信息时输出 {"facts":[],"forget":[]}"""
