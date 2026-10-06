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
# P39 游客影子用户：不在 VALID_ROLES（admin 改角色面不可设），仅签发/清理例程使用
GUEST_ROLE = "guest"
GUEST_EMAIL_DOMAIN = "guest.local"  # 游客 email 域特征（prune 清理例程的圈定口径）

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
# P23：per-user token 限额（NULL=用 DAILY_USER_BUDGET 全局缺省）；CREATE TABLE
# IF NOT EXISTS 对已有表不生效，幂等加列单独走 ALTER。
# P39：users.role CHECK 约束重建加 'guest'（幂等：先 DROP 再 ADD，存量行合法）。
_MIGRATE = """
ALTER TABLE users ADD COLUMN IF NOT EXISTS daily_token_limit BIGINT;
ALTER TABLE users DROP CONSTRAINT IF EXISTS users_role_check;
ALTER TABLE users ADD CONSTRAINT users_role_check
    CHECK (role IN ('student', 'counselor', 'admin', 'guest'));
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
            conn.execute(_MIGRATE)

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

    def create_guest(self, *, ttl: timedelta, daily_token_limit: int) -> tuple[User, str]:
        """P39 游客影子用户：随机 email + 受限限额 + 短 TTL 会话；返回 (user, 原始 token)。

        密码哈希存随机不可用串（游客永不经密码登录）；display_name 固定「游客」。
        全链路身份锚点是 email 字符串（下游表无 FK），故一个 users 行即贯通
        会话/用量/台账/反馈；过期清理由 maintenance.prune_guests 负责。
        email 用小写 hex——login/daily_limit 等按 lower(email) 查询，大写会失配。
        """
        email = f"guest-{secrets.token_hex(6)}@{GUEST_EMAIL_DOMAIN}"
        token = secrets.token_urlsafe(32)
        with self._pool.connection() as conn:  # 上下文 = 事务边界
            urow = conn.execute(
                "INSERT INTO users (email, password_hash, display_name, role, daily_token_limit)"
                f" VALUES (%s, %s, '游客', '{GUEST_ROLE}', %s) RETURNING " + _USER_COLS,
                (email, hash_password(secrets.token_urlsafe(24)), int(daily_token_limit)),
            ).fetchone()
            conn.execute(
                "INSERT INTO auth_sessions (token_hash, user_id, expires_at)"
                " VALUES (%s, %s, now() + %s)",
                (_token_hash(token), urow[0], ttl),
            )
        return _user_row(urow), token

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
            user = _user_row(row)
            if user.role != GUEST_ROLE:  # 游客短 TTL 硬过期，不滑动续期
                conn.execute(
                    "UPDATE auth_sessions SET last_seen_at = now(),"
                    " expires_at = now() + %s WHERE token_hash = %s AND expires_at < now() + %s",
                    (SESSION_TTL, th, SESSION_REFRESH_AHEAD),
                )
        return user

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

    # ---------- 管理后台（P23：admin 列表/改写/邀请码/限额） ----------

    def list_users(self) -> list[User]:
        """全量用户（admin 巡查；按 email 排序稳定）。"""
        with self._pool.connection() as conn:
            rows = conn.execute(f"SELECT {_USER_COLS} FROM users ORDER BY email").fetchall()
        return [_user_row(r) for r in rows]

    def update_user(
        self,
        email: str,
        *,
        role: str | None = None,
        status: str | None = None,
        daily_token_limit: int | None = None,
        clear_limit: bool = False,
    ) -> User | None:
        """admin 改写（role/status/限额）；无此邮箱返回 None。

        clear_limit=True 把限额恢复 NULL（走全局缺省）；否则 limit 显式传入。
        """
        sets: list[str] = []
        args: list = []
        if role is not None:
            if role not in VALID_ROLES:
                raise ValueError(f"role 必须为 {'/'.join(VALID_ROLES)}")
            sets.append("role = %s")
            args.append(role)
        if status is not None:
            if status not in ("active", "disabled"):
                raise ValueError("status 必须为 active/disabled")
            sets.append("status = %s")
            args.append(status)
        if clear_limit:
            sets.append("daily_token_limit = NULL")
        elif daily_token_limit is not None:
            sets.append("daily_token_limit = %s")
            args.append(int(daily_token_limit))
        if not sets:
            with self._pool.connection() as conn:
                row = conn.execute(
                    f"SELECT {_USER_COLS} FROM users WHERE email = %s",
                    (email.strip().lower(),),
                ).fetchone()
            return _user_row(row) if row else None
        args.append(email.strip().lower())
        with self._pool.connection() as conn:
            row = conn.execute(
                f"UPDATE users SET {', '.join(sets)} WHERE email = %s RETURNING {_USER_COLS}",
                tuple(args),
            ).fetchone()
            if row is not None and status == "disabled":
                # 停用即踢下线且不可复活（旧 cookie 全部失效；重新启用需重新登录）
                conn.execute("DELETE FROM auth_sessions WHERE user_id = %s", (row[0],))
        return _user_row(row) if row else None

    def daily_limit(self, email: str) -> int | None:
        """该用户的个性化 token 限额（NULL=未设，用全局缺省）。"""
        with self._pool.connection() as conn:
            row = conn.execute(
                "SELECT daily_token_limit FROM users WHERE email = %s",
                (email.strip().lower(),),
            ).fetchone()
        return int(row[0]) if row and row[0] is not None else None

    def list_invites(self) -> list[dict]:
        """邀请码列表（admin 巡查；创建时间倒序）。"""
        cols = "code, max_uses, used_count, expires_at, note, created_at, created_by"
        with self._pool.connection() as conn:
            rows = conn.execute(
                f"SELECT {cols} FROM invite_codes ORDER BY created_at DESC"
            ).fetchall()
        return [
            {
                "code": r[0],
                "max_uses": int(r[1]),
                "used_count": int(r[2]),
                "expires_at": r[3].isoformat() if r[3] else None,
                "note": r[4],
                "created_at": r[5].isoformat(),
                "created_by": r[6],
            }
            for r in rows
        ]

    def stats(self) -> dict[str, int]:
        with self._pool.connection() as conn:
            users, sessions, invites = conn.execute(
                "SELECT (SELECT COUNT(*) FROM users), (SELECT COUNT(*) FROM auth_sessions),"
                " (SELECT COUNT(*) FROM invite_codes)"
            ).fetchone()
        return {"users": int(users), "sessions": int(sessions), "invites": int(invites)}


# 并发核销语义依赖 UPDATE 行锁；本类无跨语句共享态，不需要 Business 式
# 进程锁（事务边界由 pool connection 上下文保证）。
