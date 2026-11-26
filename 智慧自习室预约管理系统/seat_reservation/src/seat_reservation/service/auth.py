"""认证与会话。

* 密码校验使用 PBKDF2（见 ``security``），登录失败按 IP+账号限流。
* 用户名在**认证入口统一规范化**（NFKC → strip → casefold），查询与限流 key 使用同一结果，
  避免空白/大小写/全角变体绕过限流。
* PBKDF2 只在**只读事务**中取哈希，验证在事务外完成，写事务只负责建会话。
* 会话令牌只把 SHA-256 摘要落库；Cookie 为 HttpOnly，CSRF 令牌单独放在可读 Cookie。
* 空闲 30 分钟 / 绝对 8 小时过期（NFR-04），可在 ``Settings`` 调整。
"""

from __future__ import annotations

import threading
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from ..config import Settings
from ..domain.models import Role, User
from ..errors import Forbidden, RateLimited, Unauthenticated
from ..logging_setup import get_logger
from ..security import (
    generate_initial_password,
    hash_password,
    hash_token,
    new_csrf_token,
    new_session_token,
    verify_password,
)
from ..storage.database import Database
from ..storage.repository import SqliteRepository
from ..storage.seed import BOOTSTRAP_ADMIN_ID, remove_initial_admin_credentials

logger = get_logger(__name__)


def normalize_username(value: str) -> str:
    """登录标识的唯一规范化入口。

    NFKC 先把全角等兼容字符折叠为常规形式，再去除首尾空白（含 Tab/换行）并
    做 Unicode 大小写折叠。查询与限流 key 必须都使用本函数，不能各写一套。
    """
    return unicodedata.normalize("NFKC", value or "").strip().casefold()


# 未知账号也执行一次同强度 PBKDF2，避免用响应时间差判断账号是否存在。
_DUMMY_PASSWORD_HASH = hash_password(generate_initial_password(16))


@dataclass(slots=True)
class AuthSession:
    user: User
    token_hash: str
    csrf_token: str
    expires_at: datetime
    last_seen_at: datetime


class LoginRateLimiter:
    """进程内滑动窗口限流。

    容量与清理策略
    --------------
    * 锁到期、失败窗口过期的记录由 ``sweep`` 定期清理，不会无限增长。
    * key 总数有上限 ``max_keys``；每个 key 的失败时间戳最多保留 ``max_attempts`` 条。
    * 达到容量且没有可回收记录时，对**新 key** 直接返回 429（fail-closed）：
      宁可拒绝新的登录尝试，也不丢弃已有计数或提前解除有效锁定。
    * 每个进程独立计数；多实例部署时无法共享，需要时再替换为外部存储。
    """

    def __init__(
        self,
        max_attempts: int = 5,
        window_seconds: int = 300,
        lock_seconds: int = 300,
        *,
        max_keys: int = 10_000,
        sweep_every: int = 256,
    ) -> None:
        if max_attempts < 1 or max_keys < 1:
            raise ValueError("max_attempts / max_keys 必须为正数")
        self.max_attempts = max_attempts
        self.window = timedelta(seconds=window_seconds)
        self.lock = timedelta(seconds=lock_seconds)
        self.max_keys = max_keys
        self.sweep_every = max(1, sweep_every)
        self._failures: dict[str, list[datetime]] = {}
        self._locked_until: dict[str, datetime] = {}
        self._mutex = threading.Lock()
        self._ops = 0

    # -- 查询 ---------------------------------------------------------
    def size(self) -> int:
        """当前占用的 key 数量（用于监控与测试）。"""
        with self._mutex:
            return self._size_locked()

    def check(self, key: str, now: datetime) -> None:
        with self._mutex:
            self._tick(now)
            locked = self._locked_until.get(key)
            if locked is None:
                return
            if now < locked:
                raise RateLimited(
                    f"尝试次数过多，请在 {int((locked - now).total_seconds() // 60) + 1} 分钟后重试"
                )
            self._drop_locked(key)

    def fail(self, key: str, now: datetime) -> None:
        with self._mutex:
            self._tick(now)
            if key not in self._failures and key not in self._locked_until:
                if self._size_locked() >= self.max_keys:
                    # 先尝试回收过期记录；仍满则拒绝新 key，保持已有计数与锁定不变。
                    self._sweep_locked(now)
                    if self._size_locked() >= self.max_keys:
                        raise RateLimited("登录尝试过于频繁，请稍后重试")
            attempts = [t for t in self._failures.get(key, ()) if now - t < self.window]
            attempts.append(now)
            if len(attempts) > self.max_attempts:
                attempts = attempts[-self.max_attempts :]
            self._failures[key] = attempts
            if len(attempts) >= self.max_attempts:
                self._locked_until[key] = now + self.lock

    def reset(self, key: str) -> None:
        with self._mutex:
            self._failures.pop(key, None)
            self._locked_until.pop(key, None)

    # -- 清理 ---------------------------------------------------------
    def sweep(self, now: datetime) -> None:
        """立即清理过期记录（测试与运维可直接调用）。"""
        with self._mutex:
            self._sweep_locked(now)

    def _tick(self, now: datetime) -> None:
        self._ops += 1
        if self._ops % self.sweep_every == 0:
            self._sweep_locked(now)

    def _sweep_locked(self, now: datetime) -> None:
        # 只清理“已到期”的锁定，未到期的锁定绝不移除。
        for key in [key for key, until in self._locked_until.items() if now >= until]:
            del self._locked_until[key]
            self._failures.pop(key, None)
        for key, attempts in list(self._failures.items()):
            if key in self._locked_until:
                continue
            fresh = [t for t in attempts if now - t < self.window]
            if fresh:
                self._failures[key] = fresh
            else:
                del self._failures[key]

    def _drop_locked(self, key: str) -> None:
        self._locked_until.pop(key, None)
        self._failures.pop(key, None)

    def _size_locked(self) -> int:
        return len(self._failures.keys() | self._locked_until.keys())


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
        normalised = normalize_username(username)
        key = f"{ip}|{normalised}"
        self.limiter.check(key, now)

        # 1) 只读事务：取用户与密码哈希（不占写锁）。
        with self.db.transaction(write=False) as conn:
            user = SqliteRepository(conn).get_user_by_login(normalised)
            stored_hash = user.password_hash if user is not None else _DUMMY_PASSWORD_HASH
            active_at_read = bool(user and user.is_active)

        # 2) 事务外做 PBKDF2，避免把 260k 轮哈希放进 SQLite 写锁。
        password_ok = verify_password(password, stored_hash)
        if user is None or not password_ok:
            self.limiter.fail(key, now)
            raise Unauthenticated("账号或密码不正确")
        if not active_at_read:
            raise Forbidden("账号已停用，请联系系统管理员")

        # 3) 写事务：复核账号状态后建会话，保证状态变化不会被绕过。
        with self.db.transaction() as conn:
            repo = SqliteRepository(conn)
            fresh = repo.get_user(user.id)
            if fresh is None or not fresh.is_active:
                raise Forbidden("账号已停用，请联系系统管理员")
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
                    fresh.id,
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
                (fresh.id, fresh.id),
            )
            self.limiter.reset(key)
            logger.info("login user=%s role=%s ip=%s", fresh.id, fresh.role, ip)
            return token, csrf, fresh

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
        if user_id == BOOTSTRAP_ADMIN_ID:
            # 初始管理员完成首次改密后，一次性凭据文件即失效，顺手清理。
            remove_initial_admin_credentials(self.db)

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
