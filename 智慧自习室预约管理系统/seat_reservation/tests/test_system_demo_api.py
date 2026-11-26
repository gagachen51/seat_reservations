"""系统管理页与「演示数据初始化」接口测试。

覆盖：
* ``/api/admin/system`` 的权限、空库与演示数据两种形态、Schema 版本；
* 前端误用 ``/api/health`` 时缺失字段的回归背景（health 仍只返回 status）；
* 演示数据初始化的确认短语、权限、生产环境禁用、重复初始化、审计只增不删，
  以及统一演示密码只回传一次且不落入审计。
"""

from __future__ import annotations

import dataclasses
from datetime import datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from seat_reservation.api.app import create_app
from seat_reservation.config import Settings
from seat_reservation.domain.models import Actor, Role
from seat_reservation.security import verify_password
from seat_reservation.service.container import AppContext
from seat_reservation.storage.repository import SqliteRepository
from seat_reservation.storage.seed import DEMO_USER_IDS, bootstrap_admin
from tests.helpers import PASSWORD


@pytest.fixture
def client(ctx):  # noqa: ANN001 - pytest fixture
    app = create_app(ctx.settings)
    with TestClient(app) as test_client:
        yield test_client


def _client(ctx: AppContext, *, base_url: str = "http://testserver") -> TestClient:
    app = create_app(ctx.settings, context=ctx)
    return TestClient(app, base_url=base_url)


def _login(client: TestClient, username: str, password: str) -> dict[str, str]:
    response = client.post(
        "/api/auth/login", json={"username": username, "password": password}
    )
    assert response.status_code == 200, response.text
    return {"X-CSRF-Token": client.cookies.get("seat_csrf") or ""}


def _fresh_ctx(tmp_path: Path, *, demo: bool = False) -> AppContext:
    ctx = AppContext(Settings.for_tests(tmp_path / "fresh.db"))
    ctx.initialize(demo=False)
    if demo:
        # 演示模式初始化需要显式密码，测试里不依赖随机值。
        from seat_reservation.storage.seed import seed_demo

        seed_demo(ctx.db, datetime.now(), password="Seed-Demo-Pass-123")
    return ctx


def _bootstrap(ctx: AppContext, password: str = "Admin-Pass-123") -> str:
    created = bootstrap_admin(ctx.db, datetime.now(), password=password)
    assert created == password
    # 首次初始化的管理员必须先改密；测试直接放开，以便访问管理接口。
    with ctx.db.transaction() as conn:
        repo = SqliteRepository(conn)
        user = repo.get_user("Y0001")
        assert user is not None
        user.must_change_password = False
        repo.save_user(user)
    return password


def test_system_status_requires_sysadmin(client) -> None:  # noqa: ANN001 - pytest fixture
    headers = _login(client, "S1", PASSWORD)
    assert client.get("/api/admin/system", headers=headers).status_code == 403
    teacher = _login(client, "T1", PASSWORD)
    assert client.get("/api/admin/system", headers=teacher).status_code == 403


def test_system_status_on_empty_database_returns_schema_v4(tmp_path: Path) -> None:
    ctx = _fresh_ctx(tmp_path)
    password = _bootstrap(ctx)
    with _client(ctx) as client:
        headers = _login(client, "Y0001", password)
        response = client.get("/api/admin/system", headers=headers)
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["status"] == "ok"
    assert data["version"]
    assert data["db"]["schema_version"] == 4
    assert data["db"]["size_bytes"] > 0
    assert data["db"]["users"] == 1
    assert data["demo_data"] is False
    assert data["demo_mode"] is True  # Settings.for_tests 默认演示开关
    assert data["scheduler"]["enabled"] is False
    assert data["scheduler"]["running"] is False


def test_system_status_reports_demo_data_after_seed(tmp_path: Path) -> None:
    ctx = _fresh_ctx(tmp_path, demo=True)
    with _client(ctx) as client:
        headers = _login(client, "Y0001", "Seed-Demo-Pass-123")
        data = client.get("/api/admin/system", headers=headers).json()["data"]
    assert data["demo_data"] is True
    assert data["db"]["users"] == 9
    assert data["db"]["schema_version"] == 4


def test_public_health_still_hides_internal_fields(client) -> None:  # noqa: ANN001
    """回归背景：前端不能再从 /health 读取 db/scheduler。"""
    body = client.get("/api/health").json()["data"]
    assert body == {"status": "ok"}
    assert "db" not in body and "scheduler" not in body


def test_initialize_demo_requires_sysadmin(client) -> None:  # noqa: ANN001
    headers = _login(client, "S1", PASSWORD)
    response = client.post(
        "/api/admin/system/demo-data",
        json={"confirm": "INITIALIZE_DEMO_DATA"},
        headers=headers,
    )
    assert response.status_code == 403


def test_initialize_demo_requires_confirm_phrase(client, ctx) -> None:  # noqa: ANN001
    headers = _login(client, "Y1", PASSWORD)
    response = client.post(
        "/api/admin/system/demo-data", json={"confirm": "wrong"}, headers=headers
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "DEMO_CONFIRM_REQUIRED"
    # 拒绝不能有副作用：原有账号仍在。
    with ctx.db.read() as conn:
        assert SqliteRepository(conn).get_user("S1") is not None


def test_initialize_demo_writes_accounts_audits_and_invalidates_session(
    client, ctx  # noqa: ANN001
) -> None:
    headers = _login(client, "Y1", PASSWORD)
    with ctx.engine() as engine:
        engine.audit_event(
            Actor(id="Y1", name="陈刚", role=Role.SYS_ADMIN),
            "before_demo_reset",
            "system",
            "sentinel",
            now=datetime.now(),
        )

    response = client.post(
        "/api/admin/system/demo-data",
        json={"confirm": "INITIALIZE_DEMO_DATA"},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    password = data["demo_password"]
    assert data["must_relogin"] is True
    assert data["counts"] == {"users": 9, "seats": 24, "reservations": 21, "rooms": 3}
    assert data["admin_password_preserved"] is True
    assert data["preserved_admin_ids"] == ["Y1"]
    assert {item["username"] for item in data["accounts"]} == {
        "S2023001",
        "T7001",
        "A9001",
        "Y1",
    }
    assert next(item for item in data["accounts"] if item["username"] == "Y1")["password_preserved"] is True
    assert len(password) >= 8

    # 会话表被清空：旧会话立即失效。
    assert client.get("/api/admin/system", headers=headers).status_code == 401

    # 统一演示密码只作用于学生/教师/自习室管理员；系统管理员 Y1 原密码保留。
    with ctx.db.read() as conn:
        repo = SqliteRepository(conn)
        for user_id in DEMO_USER_IDS:
            user = repo.get_user(user_id)
            assert user is not None
            assert verify_password(password, user.password_hash)
        admin = repo.get_user("Y1")
        assert admin is not None
        assert verify_password(PASSWORD, admin.password_hash)
        assert not verify_password(password, admin.password_hash)

    # 重新登录后审计仍保留历史记录，并新增初始化事件；密码不进入审计。
    new_headers = _login(client, "Y1", PASSWORD)
    audit = client.get(
        "/api/admin/audit",
        params={"page_size": 100},
        headers=new_headers,
    )
    assert audit.status_code == 200, audit.text
    actions = {item["action"] for item in audit.json()["data"]["items"]}
    assert "before_demo_reset" in actions
    assert "initialize_demo_data" in actions
    assert password not in audit.text


def test_initialize_demo_twice_uses_new_password_and_keeps_audit(
    client, ctx, monkeypatch: pytest.MonkeyPatch  # noqa: ANN001
) -> None:
    headers = _login(client, "Y1", PASSWORD)
    monkeypatch.setenv("SEAT_DEMO_PASSWORD", "First-Demo-Pass-123")
    first = client.post(
        "/api/admin/system/demo-data",
        json={"confirm": "INITIALIZE_DEMO_DATA"},
        headers=headers,
    )
    assert first.status_code == 200, first.text
    first_password = first.json()["data"]["demo_password"]

    # 系统管理员原密码保留，重新初始化前用原密码登录。
    second_headers = _login(client, "Y1", PASSWORD)
    monkeypatch.setenv("SEAT_DEMO_PASSWORD", "Second-Demo-Pass-456")
    second = client.post(
        "/api/admin/system/demo-data",
        json={"confirm": "INITIALIZE_DEMO_DATA"},
        headers=second_headers,
    )
    assert second.status_code == 200, second.text
    assert second.json()["data"]["demo_password"] == "Second-Demo-Pass-456"

    with ctx.db.read() as conn:
        audit_count = conn.execute(
            "SELECT COUNT(*) AS c FROM audit_logs WHERE action = 'initialize_demo_data'"
        ).fetchone()["c"]
    assert audit_count == 2

    # 演示账号使用最新演示密码；系统管理员仍使用原密码。
    assert (
        client.post(
            "/api/auth/login",
            json={"username": "S2023001", "password": "Second-Demo-Pass-456"},
        ).status_code
        == 200
    )
    assert (
        client.post(
            "/api/auth/login",
            json={"username": "Y1", "password": PASSWORD},
        ).status_code
        == 200
    )
    assert (
        client.post(
            "/api/auth/login",
            json={"username": "Y1", "password": first_password},
        ).status_code
        == 401
    )


def test_demo_accounts_visible_after_ui_initialization(client) -> None:  # noqa: ANN001
    headers = _login(client, "Y1", PASSWORD)
    response = client.post(
        "/api/admin/system/demo-data",
        json={"confirm": "INITIALIZE_DEMO_DATA"},
        headers=headers,
    )
    assert response.status_code == 200
    accounts = client.get("/api/auth/demo-accounts")
    assert accounts.status_code == 200, accounts.text
    items = accounts.json()["data"]
    assert len(items) == 4
    assert {item["role"] for item in items} == {
        "student",
        "teacher",
        "room_admin",
        "sys_admin",
    }
    assert "password" not in accounts.text


def test_initialize_demo_forbidden_in_production(tmp_path: Path) -> None:
    settings = dataclasses.replace(
        Settings.for_tests(tmp_path / "prod.db"),
        env="production",
        cookie_secure=True,
        demo_mode=False,
    )
    ctx = AppContext(settings)
    ctx.initialize(demo=False)
    password = _bootstrap(ctx)
    with _client(ctx, base_url="https://testserver") as client:
        headers = _login(client, "Y0001", password)
        response = client.post(
            "/api/admin/system/demo-data",
            json={"confirm": "INITIALIZE_DEMO_DATA"},
            headers=headers,
        )
    assert response.status_code == 403
    assert "生产环境" in response.json()["error"]["message"]
