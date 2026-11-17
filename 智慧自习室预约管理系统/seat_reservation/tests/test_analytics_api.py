"""D 模块 API 测试：运营分析与 CSV 导出。"""

from __future__ import annotations

import csv
import io
from datetime import date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from seat_reservation.api.app import create_app
from seat_reservation.config import Settings
from seat_reservation.domain.engine import EXPORT_KINDS
from seat_reservation.domain.models import Complaint
from seat_reservation.storage.repository import SqliteRepository
from tests.helpers import D, PASSWORD


@pytest.fixture
def client(ctx):
    app = create_app(Settings.for_tests(ctx.settings.db_path))
    with TestClient(app) as test_client:
        yield test_client


def login(client: TestClient, username: str) -> None:
    response = client.post(
        "/api/auth/login", json={"username": username, "password": PASSWORD}
    )
    assert response.status_code == 200, response.text


def _csv_rows(response) -> list[list[str]]:
    text = response.content.decode("utf-8-sig")
    return list(csv.reader(io.StringIO(text)))


def test_analytics_permissions_and_room_filtering(client: TestClient) -> None:
    login(client, "A1")
    all_rooms = client.get("/api/admin/analytics")
    assert all_rooms.status_code == 200, all_rooms.text
    data = all_rooms.json()["data"]
    assert data["range"]["room_id"] is None
    assert {room["id"] for room in data["rooms"]} == {"R1", "R2"}

    one_room = client.get("/api/admin/analytics", params={"room_id": "R1"})
    assert one_room.status_code == 200, one_room.text
    assert one_room.json()["data"]["range"]["room_id"] == "R1"
    assert [room["id"] for room in one_room.json()["data"]["rooms"]] == ["R1"]

    missing_room = client.get("/api/admin/analytics", params={"room_id": "R404"})
    assert missing_room.status_code == 404
    assert missing_room.json()["error"]["code"] == "NOT_FOUND"

    login(client, "S1")
    assert client.get("/api/admin/analytics").status_code == 403
    login(client, "T1")
    denied_teacher = client.get("/api/admin/analytics")
    assert denied_teacher.status_code == 403
    assert denied_teacher.json()["error"]["rule"] == "PR-12"

    login(client, "Y1")
    assert client.get("/api/admin/analytics").status_code == 200


def test_analytics_date_rules(client: TestClient) -> None:
    login(client, "Y1")

    default = client.get("/api/admin/analytics")
    assert default.status_code == 200
    default_range = default.json()["data"]["range"]
    today = date.today()
    assert default_range["date_to"] == today.isoformat()
    assert default_range["date_from"] == (today - timedelta(days=6)).isoformat()
    assert default_range["days"] == 7

    only_from = client.get(
        "/api/admin/analytics", params={"date_from": "2026-05-01"}
    )
    assert only_from.status_code == 200
    assert only_from.json()["data"]["range"]["date_to"] == "2026-05-07"

    only_to = client.get("/api/admin/analytics", params={"date_to": "2026-05-10"})
    assert only_to.status_code == 200
    assert only_to.json()["data"]["range"]["date_from"] == "2026-05-04"

    custom = client.get(
        "/api/admin/analytics",
        params={"date_from": "2026-05-09", "date_to": "2026-05-11"},
    )
    assert custom.status_code == 200
    assert custom.json()["data"]["range"] == {
        "date_from": "2026-05-09",
        "date_to": "2026-05-11",
        "days": 3,
        "room_id": None,
    }

    inverted = client.get(
        "/api/admin/analytics",
        params={"date_from": "2026-05-11", "date_to": "2026-05-10"},
    )
    assert inverted.status_code == 422
    assert inverted.json()["error"]["code"] == "VALIDATION_ERROR"

    over_limit = client.get(
        "/api/admin/analytics",
        params={"date_from": "2026-01-01", "date_to": "2026-04-01"},
    )
    assert over_limit.status_code == 422
    assert over_limit.json()["error"]["code"] == "VALIDATION_ERROR"

    bad_format = client.get(
        "/api/admin/analytics", params={"date_from": "not-a-date"}
    )
    assert bad_format.status_code == 422
    assert bad_format.json()["error"]["code"] == "VALIDATION_ERROR"


def test_analytics_response_fields_preserved(client: TestClient) -> None:
    login(client, "Y1")
    response = client.get(
        "/api/admin/analytics",
        params={"date_from": "2026-05-09", "date_to": "2026-05-11"},
    )
    assert response.status_code == 200
    data = response.json()["data"]
    for key in (
        "range",
        "kpi",
        "daily",
        "peak_hours",
        "rooms",
        "violations",
        "ratings",
        "waitlist",
        "complaints",
        "metric_notes",
        "generated_at",
    ):
        assert key in data
    assert "booking_utilization" in data["rooms"][0]
    assert "booking_utilization" in data["metric_notes"]
    assert "checkin_rate" in data["metric_notes"]
    assert "checkin_base" in data["kpi"]
    assert "attendance_base" in data["kpi"]
    assert data["generated_at"]


def test_export_all_kinds_and_headers(client: TestClient) -> None:
    login(client, "Y1")
    for kind in EXPORT_KINDS:
        response = client.get(f"/api/admin/export/{kind}.csv")
        assert response.status_code == 200, f"{kind}: {response.text[:300]}"
        assert response.headers["content-type"].startswith("text/csv")
        assert "attachment" in response.headers["content-disposition"]
        assert response.content.startswith("\ufeff".encode("utf-8"))
        rows = _csv_rows(response)
        assert rows, kind
        assert all(len(row) == len(rows[0]) for row in rows), kind


def test_export_permissions(client: TestClient) -> None:
    for username in ("S1", "T1", "A1"):
        login(client, username)
        for kind in EXPORT_KINDS:
            response = client.get(f"/api/admin/export/{kind}.csv")
            assert response.status_code == 403, f"{username}/{kind}"
            assert response.json()["error"]["code"] == "FORBIDDEN"
            assert response.json()["error"]["rule"] == "PR-16"


def test_export_date_filter_and_unknown_type(client: TestClient) -> None:
    login(client, "Y1")
    day = D - timedelta(days=2)
    response = client.get(
        "/api/admin/export/reservations.csv",
        params={"date_from": day.isoformat(), "date_to": day.isoformat()},
    )
    assert response.status_code == 200
    assert len(_csv_rows(response)) == 2  # 表头 + 1 条历史预约

    response = client.get(
        "/api/admin/export/reservations.csv",
        params={"date_from": (D - timedelta(days=1)).isoformat()},
    )
    assert response.status_code == 200
    assert len(_csv_rows(response)) == 1  # 仅表头

    unknown = client.get("/api/admin/export/credit.csv")
    assert unknown.status_code == 422
    assert unknown.json()["error"]["code"] == "VALIDATION_ERROR"

    inverted = client.get(
        "/api/admin/export/reservations.csv",
        params={"date_from": "2026-05-11", "date_to": "2026-05-10"},
    )
    assert inverted.status_code == 422
    assert inverted.json()["error"]["code"] == "VALIDATION_ERROR"

    bad_format = client.get(
        "/api/admin/export/reservations.csv", params={"date_to": "bad-date"}
    )
    assert bad_format.status_code == 422

    login(client, "S1")
    denied = client.get("/api/admin/export/credit.csv")
    assert denied.status_code == 403
    assert denied.json()["error"]["rule"] == "PR-16"


def test_csv_security_and_headers(client: TestClient, ctx) -> None:
    with ctx.db.transaction() as conn:
        repo = SqliteRepository(conn)
        repo.insert_complaint(
            Complaint(
                id="CP-API-0001",
                user_id="S1",
                complaint_type="facility",
                content="=SUM(1,1)",
                seat_id="R1-001",
                status="pending",
                created_at=datetime(2026, 5, 11, 6, 0),
            )
        )

    login(client, "Y1")
    response = client.get("/api/admin/export/complaints.csv")
    assert response.status_code == 200
    rows = _csv_rows(response)
    header = rows[0]
    content_index = header.index("内容")
    row = next(row for row in rows[1:] if row[0] == "CP-API-0001")
    assert row[content_index] == "'=SUM(1,1)"
    disposition = response.headers["content-disposition"]
    assert "attachment" in disposition
    assert "complaints" in disposition
    assert response.headers["content-type"].startswith("text/csv")


def test_legacy_admin_stats_still_available(client: TestClient) -> None:
    login(client, "A1")
    response = client.get("/api/admin/stats")
    assert response.status_code == 200
    assert "kpi" in response.json()["data"]
