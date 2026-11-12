"""认证与会话。

* 密码校验使用 PBKDF2（见 ``security``），登录失败按 IP+账号限流。
* 会话令牌只把 SHA-256 摘要落库；Cookie 为 HttpOnly，CSRF 令牌单独放在可读 Cookie。
* 空闲 30 分钟 / 绝对 8 小时过期（NFR-04），可在 ``Settings`` 调整。
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from ..config import Settings
from ..domain.models import Role, User
from ..errors import Forbidden, RateLimited, Unauthenticated
from ..logging_setup import get_logger
from ..security import (
    hash_password,
    hash_token,
    new_csrf_token,
    new_session_token,
    verify_password,
)
from ..storage.database import Database
from ..storage.repository import SqliteRepository

logger = get_logger(__name__)


@dataclass(slots=True)
class AuthSession:
    user: User
    token_hash: str
    csrf_token: str
    expires_at: datetime
    last_seen_at: datetime


class LoginRateLimiter:
    """进程内滑动窗口限流；多实例部署时应替换为 Redis 实现。"""

    def __init__(self, max_attempts: int = 5, window_seconds: int = 300, lock_seconds: int = 300) -> None:
        self.max_attempts = max_attempts
        self.window = timedelta(seconds=window_seconds)
        self.lock = timedelta(seconds=lock_seconds)
        self._failures: dict[str, list[datetime]] = {}
        self._locked_until: dict[str, datetime] = {}
        self._mutex = threading.Lock()

    def check(self, key: str, now: datetime) -> None:
        with self._mutex:
            locked = self._locked_until.get(key)
            if locked and now < locked:
                raise RateLimited(
                    f"尝试次数过多，请在 {int((locked - now).total_seconds() // 60) + 1} 分钟后重试"
                )
            if locked and now >= locked:
                self._locked_until.pop(key, None)
                self._failures.pop(key, None)

    def fail(self, key: str, now: datetime) -> None:
        with self._mutex:
            attempts = [item for item in self._failures.get(key, []) if now - item < self.window]
            attempts.append(now)
            self._failures[key] = attempts
            if len(attempts) >= self.max_attempts:
                self._locked_until[key] = now + self.lock

    def reset(self, key: str) -> None:
        with self._mutex:
            self._failures.pop(key, None)
            self._locked_until.pop(key, None)


class AuthService:
    def __init__(self, db: Database, settings: Settings) -> None:
        self.db = db
        self.settings = settings
        self.limiter = LoginRateLimiter()

    # ------------------------------------------------------------------
    def login(
        self,
        username: str,
        password: str,
        *,
        ip: str = "",
        user_agent: str = "",
        now: datetime | None = None,
    ) -> tuple[str, str, User]:
        now = now or datetime.now()
        key = f"{ip}|{username.lower()}"
        self.limiter.check(key, now)

        with self.db.transaction() as conn:
            repo = SqliteRepository(conn)
            user = repo.get_user(username.strip())
            if user is None or not verify_password(password, user.password_hash):
                self.limiter.fail(key, now)
                raise Unauthenticated("账号或密码不正确")
            if not user.is_active:
                raise Forbidden("账号已停用，请联系系统管理员")
            self.limiter.reset(key)

            token = new_session_token()
            csrf = new_csrf_token()
            expires_at = now + timedelta(hours=self.settings.session_absolute_hours)
            conn.execute(
                """
                INSERT INTO sessions(token_hash, user_id, created_at, expires_at, last_seen_at, ip, user_agent, csrf_token)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    hash_token(token),
                    user.id,
                    now.strftime("%Y-%m-%d %H:%M:%S"),
                    expires_at.strftime("%Y-%m-%d %H:%M:%S"),
                    now.strftime("%Y-%m-%d %H:%M:%S"),
                    ip,
                    user_agent[:300],
                    csrf,
                ),
            )
            # 单账号最多保留 5 个活跃会话
            conn.execute(
                """
                DELETE FROM sessions WHERE user_id = ? AND token_hash NOT IN (
                    SELECT token_hash FROM sessions WHERE user_id = ? ORDER BY created_at DESC LIMIT 5
                )
                """,
                (user.id, user.id),
            )
            logger.info("login user=%s role=%s ip=%s", user.id, user.role, ip)
            return token, csrf, user

    def authenticate(self, token: str | None, *, now: datetime | None = None) -> AuthSession | None:
        if not token:
            return None
        now = now or datetime.now()
        token_hash = hash_token(token)
        expired = False
        row = None
        user = None
        # 读事务：鉴权是每个请求的高频路径，不应占用写锁（并发与只读介质都更稳）。
        with self.db.transaction(write=False) as conn:
            row = conn.execute(
                "SELECT * FROM sessions WHERE token_hash = ?", (token_hash,)
            ).fetchone()
            if row is None:
                return None
            expires_at = datetime.strptime(row["expires_at"], "%Y-%m-%d %H:%M:%S")
            last_seen = datetime.strptime(row["last_seen_at"], "%Y-%m-%d %H:%M:%S")
            idle_deadline = last_seen + timedelta(minutes=self.settings.session_idle_minutes)
            if now >= expires_at or now >= idle_deadline:
                expired = True
            else:
                user = SqliteRepository(conn).get_user(row["user_id"])
                if user is None or not user.is_active:
                    expired = True

        if expired:
            self._delete_session(token_hash)
            return None
        assert row is not None and user is not None
        if (now - last_seen).total_seconds() > 120:
            # 滑动续期是尽力而为：写失败不影响本次请求。
            try:
                with self.db.transaction() as conn:
                    conn.execute(
                        "UPDATE sessions SET last_seen_at = ? WHERE token_hash = ?",
                        (now.strftime("%Y-%m-%d %H:%M:%S"), token_hash),
                    )
            except Exception:  # noqa: BLE001 - 续期失败不阻断业务
                logger.warning("session touch failed", exc_info=True)
        return AuthSession(
            user=user,
            token_hash=token_hash,
            csrf_token=row["csrf_token"],
            expires_at=expires_at,
            last_seen_at=now,
        )

    def _delete_session(self, token_hash: str) -> None:
        try:
            with self.db.transaction() as conn:
                conn.execute("DELETE FROM sessions WHERE token_hash = ?", (token_hash,))
        except Exception:  # noqa: BLE001
            logger.warning("session cleanup failed", exc_info=True)

    def logout(self, token: str | None) -> None:
        if not token:
            return
        with self.db.transaction() as conn:
            conn.execute("DELETE FROM sessions WHERE token_hash = ?", (hash_token(token),))

    def change_password(
        self, user_id: str, old_password: str, new_password: str, *, now: datetime | None = None
    ) -> None:
        now = now or datetime.now()
        if len(new_password) < 8:
            raise Forbidden("新密码至少 8 位")
        if old_password == new_password:
            raise Forbidden("新密码不能与旧密码相同")
        with self.db.transaction() as conn:
            repo = SqliteRepository(conn)
            user = repo.get_user(user_id)
            if user is None:
                raise Unauthenticated("账号不存在")
            if not verify_password(old_password, user.password_hash):
                raise Forbidden("原密码不正确")
            user.password_hash = hash_password(new_password)
            user.must_change_password = False
            user.updated_at = now
            repo.save_user(user)
            conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))

    def purge_expired(self, now: datetime | None = None) -> int:
        now = now or datetime.now()
        with self.db.transaction() as conn:
            cursor = conn.execute("DELETE FROM sessions WHERE expires_at < ?", (now.strftime("%Y-%m-%d %H:%M:%S"),))
            return cursor.rowcount or 0

    def reset_password(self, user_id: str, new_password: str, *, now: datetime | None = None) -> None:
        """管理员重置密码；调用方负责鉴权。"""
        now = now or datetime.now()
        with self.db.transaction() as conn:
            repo = SqliteRepository(conn)
            user = repo.get_user(user_id)
            if user is None:
                raise Unauthenticated("账号不存在")
            user.password_hash = hash_password(new_password)
            user.must_change_password = True
            user.updated_at = now
            repo.save_user(user)
            conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))


def actor_of(user: User) -> Any:
    from ..domain.models import Actor

    return Actor(
        id=user.id,
        name=user.name,
        role=Role(user.role),
        class_name=user.class_name,
        department=user.department,
    )
