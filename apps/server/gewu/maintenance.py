"""跨域维护例程（P39）：游客影子用户及其全部下游数据的过期清理。

游客=免登一次性身份（users 行 + 会话/记忆/台账/反馈/用量各域 email 锚点行）。
auth store 只管 users/auth_sessions，本模块按 sessions.py DELETE 端点的连带顺序
（checkpointer → episodic → 业务行 → 会话行）做批量版，供 CLI 与测试共用。
"""

from __future__ import annotations

import psycopg

from gewu.auth.store import GUEST_ROLE

_CHECKPOINT_TABLES = ("checkpoints", "checkpoint_blobs", "checkpoint_writes")


def prune_guests(dsn: str, *, days: int) -> dict[str, int]:
    """删除创建早于 days 天的游客全部数据（单事务原子）；返回各表删除行数。

    days 应 ≥ 游客会话 TTL（清理的只会是 cookie 已失效的身份）。
    checkpoints 三表可能未建（未跑过 agent 的库）：单条失败回滚后继续
    （emails 已在内存，后续语句开新事务，退出时统一提交）。
    """
    counts: dict[str, int] = {}
    with psycopg.connect(dsn) as conn:
        emails = [
            r[0]
            for r in conn.execute(
                "SELECT email FROM users WHERE role = %s"
                " AND created_at < now() - make_interval(days => %s)",
                (GUEST_ROLE, days),
            ).fetchall()
        ]
        if not emails:
            return {"users": 0}
        sessions = [
            r[0]
            for r in conn.execute(
                'SELECT session_id FROM chat_sessions WHERE "user" = ANY(%s)', (emails,)
            ).fetchall()
        ]
        for table in _CHECKPOINT_TABLES:
            try:
                cur = conn.execute(f"DELETE FROM {table} WHERE thread_id = ANY(%s)", (sessions,))
            except psycopg.errors.UndefinedTable:
                conn.rollback()
                continue
            counts[table] = cur.rowcount
        conn.execute(
            "DELETE FROM memory_episodic WHERE session_id = ANY(%s) OR user_id = ANY(%s)",
            (sessions, emails),
        )
        conn.execute('DELETE FROM bookings WHERE "user" = ANY(%s)', (emails,))
        conn.execute('DELETE FROM leave_tickets WHERE "user" = ANY(%s)', (emails,))
        conn.execute('DELETE FROM message_feedback WHERE "user" = ANY(%s)', (emails,))
        conn.execute('DELETE FROM chat_sessions WHERE "user" = ANY(%s)', (emails,))
        conn.execute("DELETE FROM token_usage WHERE user_id = ANY(%s)", (emails,))
        conn.execute("DELETE FROM memory_fact WHERE user_id = ANY(%s)", (emails,))
        cur = conn.execute(
            "DELETE FROM users WHERE role = %s AND created_at < now() - make_interval(days => %s)",
            (GUEST_ROLE, days),
        )
        counts["users"] = cur.rowcount  # auth_sessions 随 FK 级联
    return counts
