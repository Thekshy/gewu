"""会话域存储（P22）：chat_sessions（PG + psycopg pool，auth/business 同款惯例）。

会话从「客户端自报的裸 uuid」升格为服务端资源：session_id 由本表登记发放，
/api/chat 只认已登记且属本人的会话（404 早退，不泄露他人会话存在性）。
对外用户键=email（台账/记忆同口径；"user" 是 PG 保留字，SQL 全程双引号——P21 首坑）。
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass

from psycopg_pool import ConnectionPool

VALID_KINDS = ("chat", "compare")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS chat_sessions (
    session_id TEXT PRIMARY KEY,
    "user"     TEXT NOT NULL,
    title      TEXT NOT NULL DEFAULT '',
    kind       TEXT NOT NULL DEFAULT 'chat' CHECK (kind IN ('chat', 'compare')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_chat_sessions_user ON chat_sessions("user", updated_at DESC);
"""


@dataclass(frozen=True)
class ChatSession:
    """会话行（API 响应与归属校验的取值面）。"""

    session_id: str
    user: str
    title: str
    kind: str
    created_at: str
    updated_at: str


_COLS = 'session_id, "user", title, kind, created_at, updated_at'


def _row(row) -> ChatSession:
    return ChatSession(
        session_id=row[0],
        user=row[1],
        title=row[2],
        kind=row[3],
        created_at=row[4].isoformat(),
        updated_at=row[5].isoformat(),
    )


class SessionStore:
    """PG 连接池封装：会话 CRUD 与 chat 装配侧的归属校验/title 回填。"""

    def __init__(self, dsn: str) -> None:
        self._pool = ConnectionPool(dsn, min_size=1, max_size=4, open=True, name="gewu-sess")
        with self._pool.connection() as conn:
            conn.execute(_SCHEMA)

    def close(self) -> None:
        self._pool.close()

    def wipe(self) -> None:
        """测试专用：清全表。"""
        with self._pool.connection() as conn:
            conn.execute("DELETE FROM chat_sessions")

    def create(
        self, user: str, kind: str = "chat", *, session_id: str | None = None
    ) -> ChatSession:
        """登记会话；session_id 缺省服务端生成（显式传参仅供测试夹具）。"""
        if kind not in VALID_KINDS:
            raise ValueError(f"kind 必须为 {'/'.join(VALID_KINDS)}")
        sid = session_id or secrets.token_urlsafe(16)
        with self._pool.connection() as conn:
            row = conn.execute(
                f'INSERT INTO chat_sessions (session_id, "user", kind) VALUES (%s, %s, %s)'
                f" RETURNING {_COLS}",
                (sid, user, kind),
            ).fetchone()
        return _row(row)

    def get(self, user: str, session_id: str) -> ChatSession | None:
        """本人视角取会话（他人/不存在统一 None → 404，防枚举）。"""
        with self._pool.connection() as conn:
            row = conn.execute(
                f'SELECT {_COLS} FROM chat_sessions WHERE session_id = %s AND "user" = %s',
                (session_id, user),
            ).fetchone()
        return _row(row) if row else None

    def list_sessions(self, user: str, kind: str | None = None) -> list[ChatSession]:
        """本人会话列表（updated_at 倒序；kind 过滤）。"""
        sql = f'SELECT {_COLS} FROM chat_sessions WHERE "user" = %s'
        args: tuple = (user,)
        if kind is not None:
            if kind not in VALID_KINDS:
                raise ValueError(f"kind 必须为 {'/'.join(VALID_KINDS)}")
            sql += " AND kind = %s"
            args = (user, kind)
        sql += " ORDER BY updated_at DESC, session_id"
        with self._pool.connection() as conn:
            rows = conn.execute(sql, args).fetchall()
        return [_row(r) for r in rows]

    def rename(self, user: str, session_id: str, title: str) -> ChatSession | None:
        """改名（1~60 字由 API 层校验；本人不存在返回 None）。"""
        with self._pool.connection() as conn:
            row = conn.execute(
                f"UPDATE chat_sessions SET title = %s, updated_at = now()"
                f' WHERE session_id = %s AND "user" = %s RETURNING {_COLS}',
                (title, session_id, user),
            ).fetchone()
        return _row(row) if row else None

    def delete(self, user: str, session_id: str) -> bool:
        """删业务行（checkpointer/episodic 连带由 API 层按序处理，见任务书 Q4）。"""
        with self._pool.connection() as conn:
            row = conn.execute(
                'DELETE FROM chat_sessions WHERE session_id = %s AND "user" = %s'
                " RETURNING session_id",
                (session_id, user),
            ).fetchone()
        return row is not None

    def note_turn(
        self, user: str, session_id: str, first_question: str, title_limit: int = 20
    ) -> None:
        """chat 装配侧副作用：title 空→首问截断回填（CASE 条件，改名后不再覆盖）+ 刷 updated_at。"""
        with self._pool.connection() as conn:
            conn.execute(
                "UPDATE chat_sessions SET"
                " title = CASE WHEN title = '' THEN %s ELSE title END,"
                " updated_at = now()"
                ' WHERE session_id = %s AND "user" = %s',
                (first_question[:title_limit], session_id, user),
            )
