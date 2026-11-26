"""最终版临时密码策略：统一 12345678、强制改密、禁止改密沿用弱密码。"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from seat_reservation.api.app import create_app
from seat_reservation.config import Settings
from seat_reservation.security import (
    DEFAULT_TEMP_PASSWORD,
    password_change_error,
    verify_password,
)
from seat_reservation.service.container import AppContext
from seat_reservation.storage.repository import SqliteRepository
from seat_reservation.storage.seed import bootstrap_admin
from tests.helpers import PASSWORD


@pytest.fixture
def client(ctx):  # noqa: ANN001 - pytest fixture
    with TestClient(create_app(ctx.settings)) as test_client:
        yield test_client


def _login(client: TestClient, username: str, password: str) -> dict[str, str]:
    response = client.post(
        "/api/auth/login", json={"username": username, "password": password}
    )
    assert response.status_code == 200, response.text
    return {"X-CSRF-Token": client.cookies.get("seat_csrf") or ""}


def _create(client: TestClient, headers: dict[str, str], user_id: str = "TMP1"):
    return client.post(
        "/api/admin/users",
        headers=headers,
        json={"id": user_id, "name": "临时账号", "role": "student"},
    )


def test_temp_password_constant_and_policy() -> None:
    assert DEFAULT_TEMP_PASSWORD == "12345678"
    assert password_change_error("12345678") is not None
    assert password_change_error("abcdefgh") is not None
    assert password_change_error("Abcdefg1") is None
    assert password_change_error("NewPassw0rd!") is None


def test_create_user_without_password_uses_temp_and_forces_change(client) -> None:  # noqa: ANN001
    admin = _login(client, "Y1", PASSWORD)
    created = _create(client, admin)
    assert created.status_code == 201, created.text
    data = created.json()["data"]
    assert data["initial_password"] == "12345678"
    assert data["user"]["must_change_password"] is True

    student = _login(client, "TMP1", "12345678")
    forced = client.get("/api/meta", headers=student)
    assert forced.status_code == 403
    assert forced.json()["error"]["code"] == "PASSWORD_CHANGE_REQUIRED"

    same = client.post(
        "/api/auth/change-password",
        headers=student,
        json={"old_password": "12345678", "new_password": "12345678"},
    )
    assert same.status_code == 403
    assert "临时密码" in same.json()["error"]["message"]

    weak = client.post(
        "/api/auth/change-password",
        headers=student,
        json={"old_password": "12345678", "new_password": "abcdefgh"},
    )
    assert weak.status_code == 403
    assert "至少 3 类" in weak.json()["error"]["message"]

    strong = client.post(
        "/api/auth/change-password",
        headers=student,
        json={"old_password": "12345678", "new_password": "NewPassw0rd!"},
    )
    assert strong.status_code == 200, strong.text
    assert client.get("/api/auth/me").status_code == 401

    _login(client, "TMP1", "NewPassw0rd!")
    me = client.get("/api/auth/me")
    assert me.status_code == 200
    assert me.json()["data"]["user"]["must_change_password"] is False


def test_admin_reset_uses_temp_and_revokes_old_password(client) -> None:  # noqa: ANN001
    admin = _login(client, "Y1", PASSWORD)
    reset = client.post(
        "/api/admin/users/S1/reset-password", headers=admin, json={}
    )
    assert reset.status_code == 200, reset.text
    assert reset.json()["data"]["initial_password"] == "12345678"

    assert (
        client.post(
            "/api/auth/login", json={"username": "S1", "password": PASSWORD}
        ).status_code
        == 401
    )
    temp_headers = _login(client, "S1", "12345678")
    me = client.get("/api/auth/me", headers=temp_headers)
    assert me.status_code == 200
    assert me.json()["data"]["user"]["must_change_password"] is True
    assert client.get("/api/meta", headers=temp_headers).status_code == 403


def test_auth_service_reset_defaults_to_temp(ctx) -> None:  # noqa: ANN001
    ctx.auth.reset_password("S1")
    with ctx.db.read() as conn:
        user = SqliteRepository(conn).get_user("S1")
    assert user is not None
    assert verify_password("12345678", user.password_hash)
    assert user.must_change_password is True


def test_bootstrap_admin_still_uses_high_strength_random_password(tmp_path: Path) -> None:
    ctx = AppContext(Settings.for_tests(tmp_path / "bootstrap.db"))
    ctx.initialize(demo=False)
    password = bootstrap_admin(ctx.db, datetime.now())
    assert password is not None
    assert password != "12345678"
    assert len(password) >= 12
    assert not password_change_error(password)
    assert (tmp_path / "initial_admin_credentials.txt").exists()
    with ctx.db.read() as conn:
        admin = SqliteRepository(conn).get_user("Y0001")
    assert admin is not None
    assert admin.must_change_password is True
    assert verify_password(password, admin.password_hash)


def test_cli_style_default_password_is_temp(ctx) -> None:  # noqa: ANN001
    # CLI user create/passwd 在未显式指定密码时同样使用统一临时密码。
    ctx.auth.reset_password("S2", DEFAULT_TEMP_PASSWORD)
    with ctx.db.read() as conn:
        user = SqliteRepository(conn).get_user("S2")
    assert user is not None
    assert verify_password(DEFAULT_TEMP_PASSWORD, user.password_hash)
    assert user.must_change_password is True
