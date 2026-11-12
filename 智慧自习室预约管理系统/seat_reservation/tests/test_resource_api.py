"""资源管理 HTTP 接口与审计 IP 的回归测试。"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from seat_reservation.api.app import create_app
from seat_reservation.config import Settings
from tests.helpers import PASSWORD


@pytest.fixture
def client(ctx) -> TestClient:
    app = create_app(Settings.for_tests(ctx.settings.db_path))
    with TestClient(app) as test_client:
        yield test_client


def login(client: TestClient, username: str) -> dict[str, str]:
    response = client.post(
        "/api/auth/login", json={"username": username, "password": PASSWORD}
    )
    assert response.status_code == 200, response.text
    return {"X-CSRF-Token": client.cookies.get("seat_csrf") or ""}


def test_room_admin_can_manage_resources_and_audit_has_ip(client: TestClient) -> None:
    headers = login(client, "A1")
    room = client.post(
        "/api/admin/rooms",
        headers=headers,
        json={
            "id": "F601",
            "name": "F601",
            "location": "六楼",
            "open_time": "08:00",
            "close_time": "22:00",
            "status": "open",
        },
    )
    assert room.status_code == 201, room.text
    listing = client.get("/api/admin/rooms", headers=headers)
    assert listing.status_code == 200
    assert any(item["id"] == "F601" for item in listing.json()["data"]["items"])

    seat = client.post(
        "/api/admin/seats",
        headers=headers,
        json={
            "id": "F601-001",
            "room_id": "F601",
            "type": "standard",
            "capacity": 1,
            "has_power": True,
            "is_window": False,
            "note": "",
        },
    )
    assert seat.status_code == 201, seat.text
    updated = client.put(
        "/api/admin/seats/F601-001",
        headers=headers,
        json={"admin_status": "disabled"},
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["data"]["admin_status"] == "disabled"

    # 学生无权维护资源（PR-05）
    student = login(client, "S1")
    denied = client.post(
        "/api/admin/rooms",
        headers=student,
        json={"id": "F602", "name": "F602", "open_time": "08:00", "close_time": "22:00"},
    )
    assert denied.status_code == 403
    assert denied.json()["error"]["rule"] == "PR-05"

    # 审计记录必须带来源 IP（FR-72）
    sys_headers = login(client, "Y1")
    audit = client.get(
        "/api/admin/audit",
        headers=sys_headers,
        params={"action": "room_create"},
    )
    assert audit.status_code == 200
    items = audit.json()["data"]["items"]
    assert items
    assert items[0]["ip"] == "testclient"
