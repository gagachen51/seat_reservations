"""HTTP 层测试：登录、会话、CSRF、RBAC、校验与错误封装。"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from seat_reservation.api.app import create_app
from seat_reservation.config import Settings
from tests.helpers import PASSWORD


@pytest.fixture
def client(ctx):
    settings = Settings.for_tests(ctx.settings.db_path)
    app = create_app(settings)
    with TestClient(app) as test_client:
        yield test_client


def future_day(days: int = 1) -> str:
    from datetime import datetime, timedelta

    return (datetime.now() + timedelta(days=days)).date().isoformat()


def login(client: TestClient, username: str = "S1", password: str = PASSWORD):
    response = client.post("/api/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200, response.text
    csrf = client.cookies.get("seat_csrf")
    assert csrf
    return {"X-CSRF-Token": csrf}, response.json()["data"]["user"]


def test_health_is_public(client: TestClient) -> None:
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json()["data"]["status"] == "ok"
    assert response.json()["data"]["db"]["schema_version"] == 1


def test_login_sets_httponly_session_and_readable_csrf(client: TestClient) -> None:
    response = client.post(
        "/api/auth/login", json={"username": "S1", "password": PASSWORD}
    )
    assert response.status_code == 200
    cookies = response.headers.get_list("set-cookie")
    session_cookie = next(item for item in cookies if item.startswith("seat_session="))
    csrf_cookie = next(item for item in cookies if item.startswith("seat_csrf="))
    assert "HttpOnly" in session_cookie
    assert "HttpOnly" not in csrf_cookie
    assert "SameSite=lax" in session_cookie.lower() or "samesite=lax" in session_cookie.lower()


def test_login_wrong_password_and_unknown_user(client: TestClient) -> None:
    bad = client.post("/api/auth/login", json={"username": "S1", "password": "wrong-pass"})
    assert bad.status_code == 401
    assert bad.json()["error"]["code"] == "UNAUTHENTICATED"
    unknown = client.post("/api/auth/login", json={"username": "nobody", "password": "whatever"})
    assert unknown.status_code == 401


def test_login_rate_limited_after_five_failures(client: TestClient) -> None:
    for _ in range(5):
        client.post("/api/auth/login", json={"username": "S2", "password": "bad"})
    blocked = client.post("/api/auth/login", json={"username": "S2", "password": "bad"})
    assert blocked.status_code == 429


def test_unauthenticated_request_is_401(client: TestClient) -> None:
    assert client.get("/api/reservations").status_code == 401


def test_csrf_header_required_for_mutations(client: TestClient) -> None:
    headers, _ = login(client)
    payload = {"seat_id": "R1-003", "date": future_day(), "start": "09:00", "end": "10:00"}
    missing = client.post("/api/reservations", json=payload)
    assert missing.status_code == 403
    assert missing.json()["error"]["code"] == "CSRF_FAILED"
    ok = client.post("/api/reservations", json=payload, headers=headers)
    assert ok.status_code == 201, ok.text


def test_student_cannot_reach_admin_endpoints(client: TestClient) -> None:
    headers, _ = login(client)
    assert client.get("/api/admin/stats", headers=headers).status_code == 403
    assert client.get("/api/admin/params", headers=headers).status_code == 403
    assert client.get("/api/admin/audit", headers=headers).status_code == 403
    assert client.get("/api/admin/export/reservations.csv", headers=headers).status_code == 403


def test_teacher_sees_only_summary(client: TestClient) -> None:
    headers, _ = login(client, "T1")
    assert client.get("/api/teacher/dashboard", headers=headers).status_code == 200
    assert client.get("/api/reservations", headers=headers).status_code == 403
    assert client.get("/api/admin/stats", headers=headers).status_code == 403


def test_sysadmin_can_read_params_and_change_one(client: TestClient) -> None:
    headers, _ = login(client, "Y1")
    listing = client.get("/api/admin/params", headers=headers)
    assert listing.status_code == 200
    assert any(item["code"] == "checkin.before_minutes" for item in listing.json()["data"]["items"])

    changed = client.put(
        "/api/admin/params/checkin.before_minutes", json={"value": "10"}, headers=headers
    )
    assert changed.status_code == 200
    invalid = client.put(
        "/api/admin/params/booking.window_days", json={"value": "0"}, headers=headers
    )
    assert invalid.status_code == 422
    cross = client.put(
        "/api/admin/params/booking.min_minutes", json={"value": "300"}, headers=headers
    )
    assert cross.status_code == 422


def test_room_admin_can_view_stats_but_not_params(client: TestClient) -> None:
    headers, _ = login(client, "A1")
    assert client.get("/api/admin/stats", headers=headers).status_code == 200
    assert client.get("/api/admin/params", headers=headers).status_code == 403
    assert client.get("/api/admin/audit", headers=headers).status_code == 403


def test_validation_error_shape(client: TestClient) -> None:
    headers, _ = login(client)
    response = client.post(
        "/api/reservations",
        json={"seat_id": "R1-001", "date": future_day(), "start": "9:00", "end": "10:00"},
        headers=headers,
    )
    assert response.status_code == 422
    body = response.json()
    assert body["ok"] is False
    assert body["error"]["code"] == "VALIDATION_ERROR"
    assert isinstance(body["error"]["details"], list)


def test_business_rejection_returns_409_with_rule(client: TestClient) -> None:
    headers, _ = login(client, "S1")
    payload = {"seat_id": "R1-004", "date": future_day(), "start": "09:00", "end": "10:00"}
    assert client.post("/api/reservations", json=payload, headers=headers).status_code == 201
    headers2, _ = login(client, "S2")
    second = client.post("/api/reservations", json=payload, headers=headers2)
    assert second.status_code == 409
    assert second.json()["error"]["rule"] == "BR-11"


def test_pagination_and_keyword_search(client: TestClient) -> None:
    headers, _ = login(client, "A1")
    response = client.get("/api/admin/reservations", params={"page": 1, "page_size": 5}, headers=headers)
    assert response.status_code == 200
    data = response.json()["data"]
    assert set(data) == {"items", "total", "page", "page_size"}
    assert data["page_size"] == 5


def test_acs_endpoint_requires_device_token(client: TestClient) -> None:
    response = client.post("/api/acs/check-in", json={"code": "ABCDEFGH"})
    assert response.status_code == 403
    assert response.json()["error"]["rule"] == "IF-02"


def test_logout_invalidates_session(client: TestClient) -> None:
    headers, _ = login(client)
    assert client.get("/api/auth/me").status_code == 200
    assert client.post("/api/auth/logout", headers=headers).status_code == 200
    assert client.get("/api/auth/me").status_code == 401


def test_change_password_invalidates_sessions(client: TestClient) -> None:
    headers, _ = login(client)
    response = client.post(
        "/api/auth/change-password",
        json={"old_password": PASSWORD, "new_password": "NewPassw0rd!"},
        headers=headers,
    )
    assert response.status_code == 200
    assert client.get("/api/auth/me").status_code == 401
