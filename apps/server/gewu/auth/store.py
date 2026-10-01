"""auth 域存储（P21）：users / auth_sessions / invite_codes（PG + psycopg pool）。

凭证形态：服务端 session 表 + httpOnly cookie——cookie 存原始随机 token，
库存 sha256 摘要（拖库不可复用）；密码 argon2id 哈希。对外用户键=email
（台账/记忆/agent 链路沿用 TEXT user 列），users.id 仅为内部代理键、不外露。
注册 = 邀请码原子核销 + 建 user + 建 session，同一事务（邮箱冲突回滚核销）。
"""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from psycopg_pool import ConnectionPool

COOKIE_NAME = "gewu_session"
SESSION_TTL = timedelta(days=30)
SESSION_REFRESH_AHEAD = timedelta(days=15)  # 剩余不足 15d 时滑动续期

VALID_ROLES = ("student", "counselor", "admin")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id            BIGSERIAL PRIMARY KEY,
    email         TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    display_name  TEXT NOT NULL DEFAULT '',
    role          TEXT NOT NULL DEFAULT 'student'
                  CHECK (role IN ('student', 'counselor', 'admin')),
    status        TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'disabled')),
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_login_at TIMESTAMPTZ
);
CREATE TABLE IF NOT EXISTS auth_sessions (
    token_hash  TEXT PRIMARY KEY,
    user_id     BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at  TIMESTAMPTZ NOT NULL,
    last_seen_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_auth_sessions_user ON auth_sessions(user_id);
CREATE TABLE IF NOT EXISTS invite_codes (
    code       TEXT PRIMARY KEY,
    max_uses   INT NOT NULL DEFAULT 1,
    used_count INT NOT NULL DEFAULT 0,
    expires_at TIMESTAMPTZ,
    note       TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_by TEXT NOT NULL DEFAULT ''
);
"""


class AuthError(Exception):
    """auth 域语义错误（API 层映射 4xx，见 gewu/api/auth.py）。"""


class InviteInvalid(AuthError):
    """邀请码不存在 / 已用尽 / 已过期。"""


class EmailTaken(AuthError):
    """邮箱已注册（封闭注册无存在性泄露顾虑，错误可区分）。"""


class InvalidCredentials(AuthError):
    """邮箱不存在 / 密码错 / 账号停用（统一口径，不区分原因）。"""


@dataclass(frozen=True)
class User:
    """登录态用户（/api/auth/me 与 chat 装配的取值面）。"""

    id: int
    email: str
    display_name: str
    role: str
    status: str


_ph = PasswordHasher()  # 缺省即 argon2id


def hash_password(password: str) -> str:
    return _ph.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return _ph.verify(password_hash, password)
    except VerifyMismatchError:
        return False
    except Exception:  # noqa: BLE001 - 哈希格式异常视为不匹配
        return False


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _user_row(row) -> User:
    return User(id=int(row[0]), email=row[1], display_name=row[2], role=row[3], status=row[4])


_USER_COLS = "id, email, display_name, role, status"


class AuthStore:
    """PG 连接池封装：用户/会话/邀请码读写。"""

    def __init__(self, dsn: str) -> None:
        self._pool = ConnectionPool(dsn, min_size=1, max_size=4, open=True, name="gewu-auth")
        with self._pool.connection() as conn:
            conn.execute(_SCHEMA)

    def close(self) -> None:
        self._pool.close()

    def wipe(self) -> None:
        """清库重建（测试用）：sessions 随 FK 级联，序列归零保证可断言。"""
        with self._pool.connection() as conn:
            conn.execute("DELETE FROM users")
            conn.execute("DELETE FROM invite_codes")
            conn.execute("SELECT setval('users_id_seq', 1, false)")

    # ---------- 邀请码 ----------

    def create_invite(
        self, uses: int = 1, days: int | None = None, note: str = "", created_by: str = ""
    ) -> str:
        """生成邀请码（make invite 发放口；后台 UI 属 P23）。"""
        code = secrets.token_hex(5)  # 10 位 hex，演示可手抄
        expires = datetime.now(UTC) + timedelta(days=days) if days else None
        with self._pool.connection() as conn:
            conn.execute(
                "INSERT INTO invite_codes (code, max_uses, expires_at, note, created_by)"
                " VALUES (%s, %s, %s, %s, %s)",
                (code, max(1, uses), expires, note, created_by),
            )
        return code

    # ---------- 注册 / 登录 ----------

    def register(
        self, email: str, password: str, invite_code: str, display_name: str = ""
    ) -> tuple[User, str]:
        """邀请码核销 + 建 user + 建 session，同一事务；返回 (user, 原始 token)。

        核销用单条 UPDATE ... RETURNING 原子完成（并发只有一个成功）；
        邮箱冲突时整个事务回滚（核销不占用）。
        """
        email = email.strip().lower()
        token = secrets.token_urlsafe(32)
        with self._pool.connection() as conn:  # 上下文 = 事务边界
            row = conn.execute(
                "UPDATE invite_codes SET used_count = used_count + 1"
                " WHERE code = %s AND used_count < max_uses"
                " AND (expires_at IS NULL OR expires_at > now())"
                " RETURNING used_count",
                (invite_code,),
            ).fetchone()
            if row is None:
                raise InviteInvalid("邀请码无效、已用尽或已过期")
            try:
                urow = conn.execute(
                    "INSERT INTO users (email, password_hash, display_name)"
                    " VALUES (%s, %s, %s) RETURNING " + _USER_COLS,
                    (email, hash_password(password), display_name),
                ).fetchone()
            except Exception as e:  # noqa: BLE001 - 唯一约束 → 邮箱已注册
                if getattr(e, "sqlstate", None) == "23505":
                    raise EmailTaken("该邮箱已注册") from e
                raise
            conn.execute(
                "INSERT INTO auth_sessions (token_hash, user_id, expires_at)"
                " VALUES (%s, %s, now() + %s)",
                (_token_hash(token), urow[0], SESSION_TTL),
            )
        return _user_row(urow), token

    def login(self, email: str, password: str) -> tuple[User, str]:
        """登录校验（失败统一 InvalidCredentials）；返回 (user, 原始 token)。"""
        email = email.strip().lower()
        with self._pool.connection() as conn:
            row = conn.execute(
                f"SELECT {_USER_COLS}, password_hash FROM users WHERE email = %s", (email,)
            ).fetchone()
            if row is None or row[5] is None:
                raise InvalidCredentials("邮箱或密码错误")
            user = _user_row(row)
            if user.status != "active":
                raise InvalidCredentials("邮箱或密码错误")
            if not verify_password(password, row[5]):
                raise InvalidCredentials("邮箱或密码错误")
            conn.execute("UPDATE users SET last_login_at = now() WHERE id = %s", (user.id,))
            token = secrets.token_urlsafe(32)
            conn.execute(
                "INSERT INTO auth_sessions (token_hash, user_id, expires_at)"
                " VALUES (%s, %s, now() + %s)",
                (_token_hash(token), user.id, SESSION_TTL),
            )
        return user, token

    def user_for_token(self, token: str) -> User | None:
        """cookie token → 登录用户（过期/停用 → None）；剩余 <15d 时滑动续期。"""
        if not token:
            return None
        th = _token_hash(token)
        with self._pool.connection() as conn:
            row = conn.execute(
                f"SELECT u.{_USER_COLS.replace(', ', ', u.')} FROM auth_sessions s"
                " JOIN users u ON u.id = s.user_id"
                " WHERE s.token_hash = %s AND s.expires_at > now() AND u.status = 'active'",
                (th,),
            ).fetchone()
            if row is None:
                return None
            conn.execute(
                "UPDATE auth_sessions SET last_seen_at = now(),"
                " expires_at = now() + %s WHERE token_hash = %s AND expires_at < now() + %s",
                (SESSION_TTL, th, SESSION_REFRESH_AHEAD),
            )
        return _user_row(row)

    def delete_session(self, token: str) -> None:
        if not token:
            return
        with self._pool.connection() as conn:
            conn.execute("DELETE FROM auth_sessions WHERE token_hash = %s", (_token_hash(token),))

    # ---------- 管理员工具（make admin / P23 后台） ----------

    def promote_admin(self, email: str) -> bool:
        """提权已有账号为 admin（无此邮箱返回 False——先注册再提权）。"""
        with self._pool.connection() as conn:
            row = conn.execute(
                "UPDATE users SET role = 'admin' WHERE email = %s RETURNING id",
                (email.strip().lower(),),
            ).fetchone()
        return row is not None

    def _stats(self) -> dict[str, int]:  # pragma: no cover - 调试用
        with self._pool.connection() as conn:
            users, sessions, invites = conn.execute(
                "SELECT (SELECT COUNT(*) FROM users), (SELECT COUNT(*) FROM auth_sessions),"
                " (SELECT COUNT(*) FROM invite_codes)"
            ).fetchone()
        return {"users": int(users), "sessions": int(sessions), "invites": int(invites)}


# 并发核销语义依赖 UPDATE 行锁；本类无跨语句共享态，不需要 Business 式
# 进程锁（事务边界由 pool connection 上下文保证）。
