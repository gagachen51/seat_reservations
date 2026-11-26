"""P0 M-01：登录认证安全回归测试。

覆盖：
* 用户名规范化（空白 / Tab / 大小写 / 全角）无法绕过限流；
* 锁定期间正确密码同样被限制；
* PBKDF2 不在 SQLite 写事务内执行；
* 登录后会话正确创建；
* 限流记录可清理、有容量上限、清理不会误解除有效锁定。
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from seat_reservation.api.app import create_app
from seat_reservation.config import Settings
from seat_reservation.errors import Forbidden, RateLimited, Unauthenticated
from seat_reservation.service import auth as auth_module
from seat_reservation.service.auth import LoginRateLimiter, normalize_username
from seat_reservation.storage.database import Database
from seat_reservation.storage.repository import SqliteRepository
from tests.helpers import NOW, PASSWORD

IP = "10.1.2.3"


def _fail_login(ctx, username: str, times: int = 5, *, ip: str = IP) -> None:
    for _ in range(times):
        with pytest.raises(Unauthenticated):
            ctx.auth.login(username, "definitely-wrong", ip=ip, now=NOW)


# ---------------------------------------------------------------------------
# 用户名规范化
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "variant",
    [" S1", "S1 ", "  S1  ", "\tS1", "S1\n", "\tS1\n"],
)
def test_whitespace_variants_cannot_bypass_rate_limit(ctx, variant: str) -> None:
    assert normalize_username(variant) == normalize_username("S1")
    _fail_login(ctx, variant)
    with pytest.raises(RateLimited):
        ctx.auth.login("S1", PASSWORD, ip=IP, now=NOW)


@pytest.mark.parametrize("variant", ["s1", "S1", "ｓ１"])
def test_case_and_width_variants_share_counter(ctx, variant: str) -> None:
    assert normalize_username(variant) == normalize_username("S1")
    _fail_login(ctx, variant)
    with pytest.raises(RateLimited):
        ctx.auth.login("S1", PASSWORD, ip=IP, now=NOW)


def test_normalization_contract() -> None:
    assert normalize_username("  S1  ") == "s1"
    assert normalize_username("\tS1\n") == "s1"
    assert normalize_username("Ｓ１") == "s1"  # 全角 NFKC
    assert normalize_username("Straße") == normalize_username("STRASSE")  # casefold
    assert normalize_username("   ") == ""


def test_lock_blocks_correct_password(ctx) -> None:
    _fail_login(ctx, "S1")
    with pytest.raises(RateLimited):
        ctx.auth.login("S1", PASSWORD, ip=IP, now=NOW)
    # 锁定按 IP+账号隔离，其他 IP 不受影响
    token, _csrf, user = ctx.auth.login("S1", PASSWORD, ip="10.9.9.9", now=NOW)
    assert token and user.id == "S1"


def test_login_is_case_insensitive_after_fix(ctx) -> None:
    token, _csrf, user = ctx.auth.login("s1", PASSWORD, ip=IP, now=NOW)
    assert token and user.id == "S1"


# ---------------------------------------------------------------------------
# 认证流程与事务边界
# ---------------------------------------------------------------------------
def test_login_creates_valid_session(ctx) -> None:
    token, csrf, user = ctx.auth.login("S1", PASSWORD, ip=IP, now=NOW)
    assert user.id == "S1"
    assert csrf

    session = ctx.auth.authenticate(token, now=NOW)
    assert session is not None
    assert session.user.id == "S1"
    assert session.csrf_token == csrf

    ctx.auth.logout(token)
    assert ctx.auth.authenticate(token, now=NOW) is None


def test_unknown_user_and_wrong_password_look_identical(ctx) -> None:
    with pytest.raises(Unauthenticated) as wrong:
        ctx.auth.login("S1", "wrong-password", ip=IP, now=NOW)
    with pytest.raises(Unauthenticated) as unknown:
        ctx.auth.login("no-such-user", "wrong-password", ip=IP, now=NOW)
    assert wrong.value.code == unknown.value.code == "UNAUTHENTICATED"
    assert wrong.value.message == unknown.value.message


def test_disabled_user_cannot_login(ctx) -> None:
    with ctx.db.transaction() as conn:
        repo = SqliteRepository(conn)
        user = repo.get_user("S2")
        user.status = "disabled"
        repo.save_user(user)

    with pytest.raises(Forbidden):
        ctx.auth.login("S2", PASSWORD, ip=IP, now=NOW)


def test_pbkdf2_does_not_run_inside_write_transaction(ctx, monkeypatch: pytest.MonkeyPatch) -> None:
    """密码校验期间记录当前线程的写事务深度，必须为 0。"""
    depth = {"write": 0}
    original_transaction = Database.transaction

    @contextmanager
    def traced_transaction(self, *, write: bool = True):
        if write:
            depth["write"] += 1
        try:
            with original_transaction(self, write=write) as conn:
                yield conn
        finally:
            if write:
                depth["write"] -= 1

    observed: list[int] = []
    original_verify = auth_module.verify_password

    def spy_verify(password: str, encoded: str) -> bool:
        observed.append(depth["write"])
        return original_verify(password, encoded)

    monkeypatch.setattr(Database, "transaction", traced_transaction)
    monkeypatch.setattr(auth_module, "verify_password", spy_verify)

    token, _csrf, user = ctx.auth.login("S1", PASSWORD, ip=IP, now=NOW)
    assert user.id == "S1"
    assert observed == [0], f"PBKDF2 期间写事务深度不为 0：{observed}"
    assert ctx.auth.authenticate(token, now=NOW) is not None


# ---------------------------------------------------------------------------
# 限流器清理与容量
# ---------------------------------------------------------------------------
def test_limiter_sweep_removes_expired_failures() -> None:
    limiter = LoginRateLimiter(max_attempts=5, window_seconds=300, lock_seconds=300)
    for key in ("a", "b", "c"):
        limiter.fail(key, NOW)
    assert limiter.size() == 3

    limiter.sweep(NOW + timedelta(seconds=301))
    assert limiter.size() == 0


def test_limiter_sweep_keeps_active_lock() -> None:
    limiter = LoginRateLimiter(max_attempts=3, window_seconds=300, lock_seconds=600)
    for _ in range(3):
        limiter.fail("locked", NOW)
    for _ in range(2):
        limiter.fail("warming", NOW)

    limiter.sweep(NOW + timedelta(seconds=301))
    # 未到期锁定必须保留；失败窗口过期的普通记录被清理
    assert limiter.size() == 1
    with pytest.raises(RateLimited):
        limiter.check("locked", NOW + timedelta(seconds=301))
    assert limiter.size() == 1


def test_limiter_capacity_fails_closed_and_keeps_lock() -> None:
    limiter = LoginRateLimiter(max_attempts=3, max_keys=3, window_seconds=300, lock_seconds=600)
    for _ in range(3):
        limiter.fail("victim", NOW)  # 形成有效锁定
    limiter.fail("other", NOW)
    limiter.fail("third", NOW)
    assert limiter.size() == 3

    with pytest.raises(RateLimited):
        limiter.fail("flood-1", NOW)
    assert limiter.size() <= 3

    # 新 key 被拒绝后，原锁定仍然生效
    with pytest.raises(RateLimited):
        limiter.check("victim", NOW + timedelta(seconds=1))

    # 过期后容量释放，可继续记录
    limiter.sweep(NOW + timedelta(seconds=700))
    limiter.fail("flood-1", NOW + timedelta(seconds=701))
    assert limiter.size() == 1


def test_limiter_per_key_records_are_capped() -> None:
    limiter = LoginRateLimiter(max_attempts=5, window_seconds=300)
    for _ in range(50):
        limiter.fail("spam", NOW)
    assert len(limiter._failures["spam"]) <= limiter.max_attempts  # noqa: SLF001 - 内部容量断言


def test_limiter_rejects_invalid_configuration() -> None:
    with pytest.raises(ValueError):
        LoginRateLimiter(max_attempts=0)
    with pytest.raises(ValueError):
        LoginRateLimiter(max_keys=0)


# ---------------------------------------------------------------------------
# HTTP 契约
# ---------------------------------------------------------------------------
@pytest.fixture
def client(ctx):
    app = create_app(Settings.for_tests(ctx.settings.db_path))
    with TestClient(app) as test_client:
        yield test_client


def test_http_login_variant_does_not_bypass_limit(client: TestClient) -> None:
    for _ in range(5):
        response = client.post(
            "/api/auth/login", json={"username": " S1 ", "password": "wrong-pass"}
        )
        assert response.status_code == 401
    blocked = client.post("/api/auth/login", json={"username": "S1", "password": PASSWORD})
    assert blocked.status_code == 429
    assert blocked.json()["error"]["code"] == "RATE_LIMITED"


def test_http_login_still_works(client: TestClient) -> None:
    response = client.post("/api/auth/login", json={"username": "S1", "password": PASSWORD})
    assert response.status_code == 200
    assert response.json()["data"]["user"]["id"] == "S1"
