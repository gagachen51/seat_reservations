"""P0 H-01：默认凭据、Demo 隔离与桌面初始化回归测试。

覆盖：
* 正式初始化使用随机密码，首次登录必须改密；
* 初始化幂等、失败不留半初始化状态；
* 演示密码不再硬编码、不通过接口下发、不写入明文文件；
* Demo 库与正式库默认分离；
* 桌面启动器非 Demo 只建随机密码的系统管理员，并在运行期数据目录写一次性凭据文件。
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import desktop_launcher
from seat_reservation.api.app import create_app
from seat_reservation.config import DEFAULT_DB_PATH, DEFAULT_DEMO_DB_PATH, Settings
from seat_reservation.errors import Unauthenticated
from seat_reservation.security import verify_password
from seat_reservation.service.container import AppContext
from seat_reservation.storage import seed as seed_module
from seat_reservation.storage.repository import SqliteRepository
from seat_reservation.storage.seed import resolve_demo_password, seed_demo

NOW = datetime(2026, 5, 11, 8, 0, 0)
SRC_ROOT = Path(__file__).resolve().parents[1] / "src" / "seat_reservation"


def _settings(tmp_path: Path, name: str = "h01.db", *, demo_mode: bool = False) -> Settings:
    return Settings(
        env="test",
        db_path=tmp_path / name,
        scheduler_enabled=False,
        demo_mode=demo_mode,
    )


def _admin(ctx: AppContext):
    with ctx.db.read() as conn:
        return SqliteRepository(conn).get_user("Y0001")


def _counts(ctx: AppContext) -> dict[str, int]:
    with ctx.db.read() as conn:
        return {
            "users": conn.execute("SELECT COUNT(*) AS c FROM users").fetchone()["c"],
            "params": conn.execute("SELECT COUNT(*) AS c FROM system_params").fetchone()["c"],
            "audit": conn.execute("SELECT COUNT(*) AS c FROM audit_logs").fetchone()["c"],
            "reservations": conn.execute("SELECT COUNT(*) AS c FROM reservations").fetchone()["c"],
        }


# ---------------------------------------------------------------------------
# 正式初始化
# ---------------------------------------------------------------------------
def test_bootstrap_admin_random_password_and_must_change(tmp_path: Path) -> None:
    ctx = AppContext(_settings(tmp_path))
    ctx.initialize()

    password = ctx.bootstrap_admin()
    assert password and len(password) >= 12

    user = _admin(ctx)
    assert user is not None
    assert user.role == "sys_admin"
    assert user.must_change_password is True
    assert verify_password(password, user.password_hash)

    token, _csrf, logged_in = ctx.auth.login("Y0001", password, now=NOW)
    assert token
    assert logged_in.must_change_password is True

    # 改密后标记清除，旧密码失效
    ctx.auth.change_password("Y0001", password, "Changed-Pass-123", now=NOW)
    assert _admin(ctx).must_change_password is False
    with pytest.raises(Unauthenticated):
        ctx.auth.login("Y0001", password, now=NOW)


def test_bootstrap_admin_passwords_are_not_shared_between_databases(tmp_path: Path) -> None:
    first = AppContext(_settings(tmp_path, "a.db"))
    second = AppContext(_settings(tmp_path, "b.db"))
    password_a = first.bootstrap_admin()
    password_b = second.bootstrap_admin()
    assert password_a and password_b
    assert password_a != password_b


def test_bootstrap_admin_is_idempotent(tmp_path: Path) -> None:
    ctx = AppContext(_settings(tmp_path))
    ctx.initialize()

    assert ctx.bootstrap_admin() is not None
    assert ctx.bootstrap_admin() is None
    assert _counts(ctx)["users"] == 1
    assert _counts(ctx)["reservations"] == 0


def test_bootstrap_failure_leaves_no_partial_state(tmp_path: Path) -> None:
    ctx = AppContext(_settings(tmp_path))
    ctx.initialize()

    with pytest.raises(ValueError):
        ctx.bootstrap_admin("short")

    counts = _counts(ctx)
    assert counts == {"users": 0, "params": 0, "audit": 0, "reservations": 0}

    # 半初始化已回滚，随后仍可正常初始化
    assert ctx.bootstrap_admin() is not None
    assert _counts(ctx)["users"] == 1


def test_non_demo_bootstrap_creates_no_demo_users(tmp_path: Path) -> None:
    ctx = AppContext(_settings(tmp_path))
    ctx.initialize()
    ctx.bootstrap_admin()

    with ctx.db.read() as conn:
        ids = [row["id"] for row in conn.execute("SELECT id FROM users").fetchall()]
    assert ids == ["Y0001"]
    assert _counts(ctx)["reservations"] == 0


# ---------------------------------------------------------------------------
# 演示凭据
# ---------------------------------------------------------------------------
def test_demo_password_is_not_hardcoded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SEAT_DEMO_PASSWORD", raising=False)

    assert not hasattr(seed_module, "DEMO_PASSWORD")
    first, second = resolve_demo_password(), resolve_demo_password()
    assert first and second and first != second

    for source in (
        "storage/seed.py",
        "api/routes/auth_routes.py",
        "service/container.py",
        "cli.py",
    ):
        text = (SRC_ROOT / source).read_text(encoding="utf-8")
        assert "Seat-Demo-2026" not in text


def test_demo_password_env_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SEAT_DEMO_PASSWORD", "Explicit-Demo-Pass-123")
    assert resolve_demo_password() == "Explicit-Demo-Pass-123"
    assert resolve_demo_password("Caller-Pass-123") == "Caller-Pass-123"


def test_seed_demo_uses_given_password(tmp_path: Path) -> None:
    ctx = AppContext(_settings(tmp_path, demo_mode=True))
    ctx.db.migrate()
    seed_demo(ctx.db, NOW, password="Seed-Demo-Pass-123")

    with ctx.db.read() as conn:
        repo = SqliteRepository(conn)
        for user_id in ("S2023001", "T7001", "A9001", "Y0001"):
            user = repo.get_user(user_id)
            assert user is not None
            assert verify_password("Seed-Demo-Pass-123", user.password_hash)


def test_demo_accounts_api_does_not_disclose_password(tmp_path: Path) -> None:
    settings = _settings(tmp_path, demo_mode=True)
    ctx = AppContext(settings)
    ctx.initialize(demo=True, demo_password="Api-Demo-Pass-123")

    app = create_app(settings)
    with TestClient(app) as client:
        response = client.get("/api/auth/demo-accounts")

    assert response.status_code == 200
    items = response.json()["data"]
    assert len(items) == 4
    assert all("password" not in item for item in items)
    assert "Api-Demo-Pass-123" not in response.text


# ---------------------------------------------------------------------------
# Demo / 正式库隔离
# ---------------------------------------------------------------------------
def test_demo_mode_uses_separate_default_database(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("SEAT_DB_PATH", "SEAT_DEMO_MODE", "SEAT_ENV"):
        monkeypatch.delenv(name, raising=False)

    normal = Settings.from_env()
    assert normal.db_path == DEFAULT_DB_PATH

    monkeypatch.setenv("SEAT_DEMO_MODE", "true")
    demo = Settings.from_env()
    assert demo.db_path == DEFAULT_DEMO_DB_PATH
    assert demo.db_path != normal.db_path

    # 显式指定 SEAT_DB_PATH 时以显式值为准
    monkeypatch.setenv("SEAT_DB_PATH", "var/custom.db")
    explicit = Settings.from_env()
    assert explicit.db_path.name == "custom.db"


def test_repo_credential_files_have_no_plaintext_password() -> None:
    project_root = Path(__file__).resolve().parents[1]
    candidates = [
        project_root.parent / "初始账号说明.txt",
        project_root.parent / "初始账号与密码.txt",
        project_root / "初始账号说明.txt",
        project_root / "初始账号与密码.txt",
    ]
    existing = [path for path in candidates if path.exists()]
    assert existing, "初始账号说明文件应保留（只允许移除其中的明文密码）"
    import re

    for path in existing:
        text = path.read_text(encoding="utf-8-sig")
        assert "Seat-Demo-2026" not in text
        assert not re.search(r"密码[:：]\s*\S+", text)


# ---------------------------------------------------------------------------
# 桌面启动器
# ---------------------------------------------------------------------------
def test_launcher_non_demo_provisioning_creates_only_admin(tmp_path: Path) -> None:
    ctx = AppContext(_settings(tmp_path, "launcher.db"))
    ctx.initialize()

    password, created = desktop_launcher.create_accounts(ctx, demo=False)
    assert created is True
    assert password and verify_password(password, _admin(ctx).password_hash)

    with ctx.db.read() as conn:
        user_ids = [row["id"] for row in conn.execute("SELECT id FROM users").fetchall()]
    assert user_ids == ["Y0001"]

    # 正式模式把一次性凭据写入运行期数据目录（与 DB 同级，已被 .gitignore 排除）
    credentials = tmp_path / seed_module.INITIAL_CREDENTIALS_FILENAME
    assert credentials.is_file()
    text = credentials.read_text(encoding="utf-8")
    assert "Y0001" in text and password in text
    assert "删除此文件" in text
    assert list(tmp_path.glob("*.txt")) == [credentials]

    # 再次启动不重复建号、不再返回密码
    again, created_again = desktop_launcher.create_accounts(ctx, demo=False)
    assert again is None and created_again is False


def test_launcher_demo_provisioning_seeds_demo_data(tmp_path: Path) -> None:
    ctx = AppContext(_settings(tmp_path, "launcher-demo.db", demo_mode=True))
    ctx.initialize()

    password, created = desktop_launcher.create_accounts(ctx, demo=True)
    assert created is True
    assert password and verify_password(password, _admin(ctx).password_hash)
    assert _counts(ctx)["reservations"] > 0
    assert not list(tmp_path.glob("*.txt"))


def test_launcher_has_no_plaintext_writer() -> None:
    assert not hasattr(desktop_launcher, "write_account_card")


def test_launcher_demo_mode_requested(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SEAT_DEMO_MODE", raising=False)
    assert desktop_launcher.demo_mode_requested([]) is False
    assert desktop_launcher.demo_mode_requested(["--demo"]) is True
    monkeypatch.setenv("SEAT_DEMO_MODE", " TRUE ")
    assert desktop_launcher.demo_mode_requested([]) is True
