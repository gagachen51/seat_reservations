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


def _bootstrap(ctx: AppContext, password: str = "12345678") -> str:
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
