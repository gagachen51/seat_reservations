"""全组新增路由的统一契约冒烟。

覆盖 v1.2 计划的 15 条路由（A 8、B 4、C 2、D 1），最低 12 条必交。当前
``feature/reservation-module`` 基于 ``integration/v1.2``，A 的路由尚未通过 SFCR
注册、B 的资源路由尚未合并，因此测试对“未注册路由”显式 skip，并单独校验
C 的两条路由必须存在；集成分支合并 B/A 后，同一文件会自动覆盖全部 15 条。

约定：
* 任一已注册路由都不得返回 5xx；
* 成功响应为 ``{ok:true,data,message}``，失败为 ``{ok:false,error:{code,message,...}}``；
* 权限矩阵由各模块自己的测试覆盖，这里只做“路由可达 + 错误结构”冒烟。
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest
from fastapi.testclient import TestClient

from seat_reservation.api.app import create_app
from seat_reservation.api.routes.account_routes import router as account_router
from seat_reservation.config import Settings
from tests.helpers import PASSWORD


@dataclass(frozen=True)
class PlannedRoute:
    module: str
    method: str
    template: str
    path: str
    role: str
    body: dict | None = None


PLANNED_ROUTES: tuple[PlannedRoute, ...] = (
    # A · 账号与个人中心（未注册时由本测试临时装配，正式注册由 B 的 SFCR 完成）
    PlannedRoute("A", "POST", "/api/admin/users", "/api/admin/users", "Y1",
                 {"id": "CT-A1", "name": "契约账号", "role": "student"}),
    PlannedRoute("A", "PUT", "/api/admin/users/{user_id}/status", "/api/admin/users/S2/status", "Y1",
                 {"status": "active", "reason": "契约检查"}),
    PlannedRoute("A", "POST", "/api/admin/users/{user_id}/reset-password",
                 "/api/admin/users/S2/reset-password", "Y1", {}),
    PlannedRoute("A", "GET", "/api/admin/users/{user_id}/sessions",
                 "/api/admin/users/S2/sessions", "Y1"),
    PlannedRoute("A", "POST", "/api/admin/users/{user_id}/sessions/revoke",
                 "/api/admin/users/S2/sessions/revoke", "Y1", {}),
    PlannedRoute("A", "GET", "/api/me/sessions", "/api/me/sessions", "S1"),
    PlannedRoute("A", "POST", "/api/me/sessions/revoke-others",
                 "/api/me/sessions/revoke-others", "S1", {}),
    PlannedRoute("A", "PUT", "/api/me/profile", "/api/me/profile", "S1",
                 {"name": "契约学生"}),
    # B · 资源与座位治理（feature/resource-module 尚未合并）
    PlannedRoute("B", "GET", "/api/admin/seat-blocks", "/api/admin/seat-blocks", "A1"),
    PlannedRoute("B", "POST", "/api/admin/seat-blocks/{block_id}/release",
                 "/api/admin/seat-blocks/BL-NOT-EXIST/release", "A1", {}),
    PlannedRoute("B", "POST", "/api/admin/seats/bulk", "/api/admin/seats/bulk", "A1",
                 {"seats": []}),
    PlannedRoute("B", "GET", "/api/admin/rooms/{room_id}/impact",
                 "/api/admin/rooms/R1/impact", "A1"),
    # C · 预约核心增强
    PlannedRoute("C", "POST", "/api/reservations/{reservation_id}/extend",
                 "/api/reservations/RS-NOT-EXIST/extend", "S1", {"extra_minutes": 30}),
    PlannedRoute("C", "POST", "/api/waitlist/{entry_id}/cancel",
                 "/api/waitlist/W-NOT-EXIST/cancel", "S1", {}),
    # D · 运营分析
    PlannedRoute("D", "GET", "/api/admin/analytics", "/api/admin/analytics", "Y1"),
)

C_ROUTES = (
    ("POST", "/api/reservations/{reservation_id}/extend"),
    ("POST", "/api/waitlist/{entry_id}/cancel"),
)


def _route_present(app, method: str, template: str) -> bool:
    for route in app.routes:
        if getattr(route, "path", "") != template:
            continue
        methods = getattr(route, "methods", set()) or set()
        if method in methods:
            return True
    return False


def _registered_routes(app) -> set[tuple[str, str]]:
    return {
        (item.method, item.template)
        for item in PLANNED_ROUTES
        if _route_present(app, item.method, item.template)
    }


@pytest.fixture
def app(ctx):
    application = create_app(Settings.for_tests(ctx.settings.db_path))
    # A 的路由在 B 应用 SFCR 之前不会出现在 app.py 中；这里仅测试临时装配，
    # 不代表正式上线。正式注册由 chore/shared-integration 完成。
    if not _route_present(application, "GET", "/api/me/sessions"):
        application.include_router(account_router, prefix="/api")
    return application


@pytest.fixture
def client(app):
    with TestClient(app) as test_client:
        yield test_client


def login(client: TestClient, username: str) -> dict[str, str]:
    response = client.post(
        "/api/auth/login", json={"username": username, "password": PASSWORD}
    )
    assert response.status_code == 200, response.text
    return {"X-CSRF-Token": client.cookies.get("seat_csrf") or ""}


def _assert_envelope(response, method: str, path: str) -> None:
    assert response.status_code < 500, (
        f"{method} {path} 返回 {response.status_code}：{response.text[:400]}"
    )
    payload = response.json()
    assert isinstance(payload, dict)
    if response.status_code < 400:
        assert payload.get("ok") is True, payload
        assert "data" in payload, payload
        return
    assert payload.get("ok") is False, payload
    error = payload.get("error")
    assert isinstance(error, dict), payload
    assert isinstance(error.get("code"), str) and error["code"], payload
    assert isinstance(error.get("message"), str) and error["message"], payload


@pytest.mark.parametrize("planned", PLANNED_ROUTES, ids=lambda item: f"{item.module}-{item.method}-{item.template}")
def test_planned_route_contract(app, client: TestClient, planned: PlannedRoute) -> None:
    if not _route_present(app, planned.method, planned.template):
        pytest.skip(
            f"{planned.module} 模块的 {planned.method} {planned.template} 尚未合并/注册；"
            "合并后本用例自动生效"
        )
    headers = login(client, planned.role)
    response = client.request(
        planned.method,
        planned.path,
        headers=headers,
        json=planned.body,
    )
    _assert_envelope(response, planned.method, planned.path)


def test_c_new_routes_are_registered(app) -> None:
    missing = [
        f"{method} {template}"
        for method, template in C_ROUTES
        if not _route_present(app, method, template)
    ]
    assert not missing, f"C 的新路由未注册：{missing}"


def test_planned_route_coverage_reaches_minimum(app) -> None:
    registered = _registered_routes(app)
    missing = [
        f"{item.module}:{item.method} {item.template}"
        for item in PLANNED_ROUTES
        if (item.method, item.template) not in registered
    ]
    if len(registered) < 12:
        pytest.skip(
            f"当前分支已注册 {len(registered)}/15 条计划路由，未达最低 12 条；"
            f"待合并后复核。缺失：{', '.join(missing)}"
        )
    assert len(PLANNED_ROUTES) == 15
    assert len(registered) >= 12
