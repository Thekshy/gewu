"""按用户的每日 token 用量（P23：per-user 记账 + admin 巡查）。

与 budget.py 的分工：budget=闸（全局每日上限，usage.json 文件，公网兜底），
usage=账（按用户逐日落 PG，admin 可见可管限额）。记账贯通用 contextvar：
chat 入口 set(email) → LLMService 三个记账口读后双写（同线程自然传播到
graph 迭代与 agent 子图；异步线程不继承，需显式 set——consolidate 已带）。
"""

from __future__ import annotations

from contextvars import ContextVar
from datetime import date

from psycopg_pool import ConnectionPool

# 当前请求的用户标识（记账归属）；None=匿名/eval 直调（只记全局不记个人）。
current_user: ContextVar[str | None] = ContextVar("gewu_usage_user", default=None)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS token_usage (
    user_id TEXT NOT NULL,
    day     DATE NOT NULL,
    tokens  BIGINT NOT NULL DEFAULT 0,
    PRIMARY KEY (user_id, day)
);
CREATE INDEX IF NOT EXISTS idx_token_usage_day ON token_usage(day DESC);
"""


class UsageStore:
    """PG 按用户日用量（UPSERT 累加；失败不影响主链路，由调用方吞异常）。"""

    def __init__(self, dsn: str) -> None:
        # checkout 超时 5s：用量账是软防护，PG 抖动不该长挂 chat 入口
        self._pool = ConnectionPool(
            dsn, min_size=1, max_size=4, open=True, timeout=5, name="gewu-usage"
        )
        with self._pool.connection() as conn:
            conn.execute(_SCHEMA)

    def close(self) -> None:
        self._pool.close()

    def wipe(self) -> None:
        """测试专用：清全表。"""
        with self._pool.connection() as conn:
            conn.execute("DELETE FROM token_usage")

    def add(self, user_id: str, tokens: int) -> None:
        """同日累加（UPSERT）。"""
        if not user_id or tokens <= 0:
            return
        with self._pool.connection() as conn:
            conn.execute(
                "INSERT INTO token_usage (user_id, day, tokens) VALUES (%s, %s, %s)"
                " ON CONFLICT (user_id, day) DO UPDATE SET tokens = token_usage.tokens + %s",
                (user_id, date.today(), int(tokens), int(tokens)),
            )

    def today(self, user_id: str) -> int:
        with self._pool.connection() as conn:
            row = conn.execute(
                "SELECT tokens FROM token_usage WHERE user_id = %s AND day = %s",
                (user_id, date.today()),
            ).fetchone()
        return int(row[0]) if row else 0

    def today_all(self) -> list[tuple[str, int]]:
        """今日全员用量（tokens 倒序；admin 巡查与 top 榜）。"""
        with self._pool.connection() as conn:
            rows = conn.execute(
                "SELECT user_id, tokens FROM token_usage WHERE day = %s"
                " ORDER BY tokens DESC, user_id",
                (date.today(),),
            ).fetchall()
        return [(r[0], int(r[1])) for r in rows]

    def daily_totals(self, days: int = 7) -> list[tuple[str, int]]:
        """近 N 日全员合计（含今日；时间倒序）。"""
        with self._pool.connection() as conn:
            rows = conn.execute(
                "SELECT day::text, SUM(tokens)::bigint FROM token_usage"
                " WHERE day > current_date - %s GROUP BY day ORDER BY day DESC",
                (days,),
            ).fetchall()
        return [(r[0], int(r[1])) for r in rows]


def make_usage_store(dsn: str) -> UsageStore | None:
    """装配工厂：可达建表返回 store，PG 不可达退 None（per-user 记账软降级）。

    与 _make_checkpointer 的退路同思路：用量账是增强不是依赖——测试环境/
    PG 抖动时禁用 per-user 功能（全局闸 usage.json 兜底），主链路照常。
    """
    try:
        import psycopg  # noqa: PLC0415

        with psycopg.connect(dsn, connect_timeout=2, autocommit=True) as conn:
            conn.execute("SELECT 1")
        return UsageStore(dsn)
    except Exception as e:  # noqa: BLE001 - 不可达即软降级
        print(f"[app] UsageStore 不可用（per-user 记账禁用，全局闸兜底）：{e}", flush=True)
        return None
