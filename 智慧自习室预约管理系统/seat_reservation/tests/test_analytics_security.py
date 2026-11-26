"""D 模块安全整改测试：09-D 的 25 条用例。

测试使用 conftest 的 ctx 夹具，因此账号/房间 ID 采用
tests/helpers.seed_fixture 的 S1~S5/T1/A1/Y1/R1/R2，不混用 demo 数据。
"""

from __future__ import annotations

import csv
import io
import json
from datetime import date, datetime, time, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from seat_reservation.api.app import create_app
from seat_reservation.api.routes.admin_routes import _csv_safe
from seat_reservation.config import Settings
from seat_reservation.domain import engine as engine_module
from seat_reservation.domain.engine import EXPORT_KINDS
from seat_reservation.domain.models import Complaint
from seat_reservation.errors import Forbidden, ValidationFailed
from seat_reservation.storage.repository import SqliteRepository
from tests.helpers import D, NOW, PASSWORD, Env


@pytest.fixture
def client(ctx):
    app = create_app(Settings.for_tests(ctx.settings.db_path))
    with TestClient(app) as test_client:
        yield test_client


def _login(client: TestClient, user: str) -> dict[str, str]:
    response = client.post("/api/auth/login", json={"username": user, "password": PASSWORD})
    assert response.status_code == 200, response.text
    return {"X-CSRF-Token": client.cookies.get("seat_csrf")}


def _csv_rows(response) -> list[list[str]]:
    return list(csv.reader(io.StringIO(response.content.decode("utf-8-sig"))))


def _export(env: Env, kind: str, *, date_from=None, date_to=None, audit=False):
    with env.ctx.engine(write=audit) as engine:
        return engine.export_rows(
            env.actor("Y1"),
            kind,
            date_from=date_from,
            date_to=date_to,
            audit=audit,
        )


def test_d_sec_001_csv_safe_prefixes() -> None:
    dangerous = ["=SUM(1,1)", "+cmd", "-1+1", "@SUM", "\t=1", "\r=1", "\n=1", "\x00=1", "\ufeff=1"]
    for value in dangerous:
        cleaned = _csv_safe(value)
        assert cleaned.startswith("'"), (value, cleaned)
        assert not cleaned.startswith(("=", "+", "-", "@"))
    for value in ["张三", "2026-05-11", "123"]:
        assert _csv_safe(value) == value


def test_d_sec_002_resolve_analytics_dates(ctx) -> None:
    with ctx.engine(write=False) as engine:
        default = engine._resolve_analytics_dates(None, None, NOW)
        only_from = engine._resolve_analytics_dates(date(2026, 5, 1), None, NOW)
        only_to = engine._resolve_analytics_dates(None, date(2026, 5, 10), NOW)
        leap = engine._resolve_analytics_dates(date(2024, 2, 29), None, NOW)
        assert default == (date(2026, 5, 5), date(2026, 5, 11))
        assert only_from == (date(2026, 5, 1), date(2026, 5, 7))
        assert only_to == (date(2026, 5, 4), date(2026, 5, 10))
        assert leap == (date(2024, 2, 29), date(2024, 3, 6))
        with pytest.raises(ValidationFailed):
            engine._resolve_analytics_dates(date(2026, 1, 1), date(2026, 4, 1), NOW)
        with pytest.raises(ValidationFailed):
            engine._resolve_analytics_dates(date(2026, 5, 11), date(2026, 5, 10), NOW)


def test_d_sec_003_export_kinds_and_permissions(env: Env) -> None:
    with env.ctx.engine(write=False) as engine:
        for kind in EXPORT_KINDS:
            headers, rows = engine.export_rows(
                env.actor("Y1"), kind, date_from=None, date_to=None
            )
            assert headers
            assert isinstance(rows, list)
        with pytest.raises(ValidationFailed):
            engine.export_rows(env.actor("Y1"), "credit", date_from=None, date_to=None)
        with pytest.raises(Forbidden):
            engine.export_rows(env.actor("A1"), "reservations", date_from=None, date_to=None)


def test_d_sec_004_export_limits_engine(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    Env.create(env, "S1", "R1-001", time(9, 0), time(10, 0))
    Env.create(env, "S2", "R1-002", time(9, 0), time(10, 0))
    monkeypatch.setattr(engine_module, "MAX_EXPORT_ROWS", 1)
    with env.ctx.engine(write=False) as engine:
        with pytest.raises(ValidationFailed):
            engine.export_rows(env.actor("Y1"), "reservations", date_from=D, date_to=D)
        with pytest.raises(ValidationFailed):
            engine.export_rows(
                env.actor("Y1"),
                "reservations",
                date_from=D - timedelta(days=367),
                date_to=D,
            )


def test_d_sec_005_audit_event_redacts_sensitive_fields(ctx) -> None:
    env = Env(ctx)
    with ctx.engine(write=True) as engine:
        engine.audit_event(
            env.actor("Y1"),
            "export_csv",
            "export",
            "reservations",
            result="success",
            details={"password": "x", "token": "y", "csrf": "z", "kind": "reservations"},
        )
    with ctx.db.read() as conn:
        row = conn.execute(
            "SELECT after_json FROM audit_logs WHERE action='export_csv' ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
    assert row is not None
    payload = json.loads(row["after_json"])
    assert payload["result"] == "success"
    assert payload["details"]["password"] == "[REDACTED]"
    assert payload["details"]["token"] == "[REDACTED]"
    assert payload["details"]["csrf"] == "[REDACTED]"
    assert payload["details"]["kind"] == "reservations"


def test_d_sec_006_analytics_permissions_api(client: TestClient) -> None:
    _login(client, "A1")
    assert client.get("/api/admin/analytics").status_code == 200
    _login(client, "S1")
    assert client.get("/api/admin/analytics").status_code == 403
    _login(client, "T1")
    assert client.get("/api/admin/analytics").status_code == 403
    _login(client, "Y1")
    assert client.get("/api/admin/analytics").status_code == 200


def test_d_sec_007_analytics_date_api(client: TestClient) -> None:
    _login(client, "Y1")
    ok = client.get(
        "/api/admin/analytics",
        params={"date_from": "2026-02-11", "date_to": "2026-05-11"},
    )
    assert ok.status_code == 200
    bad_span = client.get(
        "/api/admin/analytics",
        params={"date_from": "2026-02-10", "date_to": "2026-05-11"},
    )
    assert bad_span.status_code == 422
    inverted = client.get(
        "/api/admin/analytics",
        params={"date_from": "2026-05-11", "date_to": "2026-05-10"},
    )
    assert inverted.status_code == 422
    leap = client.get("/api/admin/analytics", params={"date_from": "2024-02-29"})
    assert leap.status_code == 200
    assert leap.json()["data"]["range"]["date_to"] == "2024-03-06"


def test_d_sec_008_export_all_kinds_api(client: TestClient) -> None:
    _login(client, "Y1")
    for kind in EXPORT_KINDS:
        response = client.get(f"/api/admin/export/{kind}.csv")
        assert response.status_code == 200, (kind, response.text)
        assert response.content.startswith(b"\xef\xbb\xbf")
        assert response.headers["content-type"].startswith("text/csv")
        rows = _csv_rows(response)
        assert rows and rows[0]


def test_d_sec_009_csv_formula_api(client: TestClient, ctx) -> None:
    with ctx.db.transaction() as conn:
        SqliteRepository(conn).insert_complaint(
            Complaint(
                id="CP-SEC-009",
                user_id="S1",
                complaint_type="facility",
                content="=SUM(1,1)",
                seat_id="R1-001",
                status="pending",
                created_at=NOW,
            )
        )
    _login(client, "Y1")
    response = client.get("/api/admin/export/complaints.csv")
    rows = _csv_rows(response)
    index = rows[0].index("内容")
    row = next(item for item in rows[1:] if item[0] == "CP-SEC-009")
    assert row[index] == "'=SUM(1,1)"


def test_d_sec_010_export_limits_api(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    _login(client, "Y1")
    too_wide = client.get(
        "/api/admin/export/reservations.csv",
        params={
            "date_from": (D - timedelta(days=367)).isoformat(),
            "date_to": D.isoformat(),
        },
    )
    assert too_wide.status_code == 422
    monkeypatch.setattr(engine_module, "MAX_EXPORT_ROWS", 1)
    env = Env(client.app.state.ctx)
    Env.create(env, "S1", "R1-001", time(9, 0), time(10, 0))
    Env.create(env, "S2", "R1-002", time(9, 0), time(10, 0))
    too_many = client.get(
        "/api/admin/export/reservations.csv",
        params={"date_from": D.isoformat(), "date_to": D.isoformat()},
    )
    assert too_many.status_code == 422


def test_d_sec_011_analytics_data_consistency(client: TestClient, ctx) -> None:
    env = Env(ctx)
    created = Env.create(env, "S1", "R1-001", time(9, 0), time(10, 0))
    assert created.ok
    _login(client, "Y1")
    response = client.get(
        "/api/admin/analytics",
        params={"date_from": D.isoformat(), "date_to": D.isoformat()},
    )
    assert response.status_code == 200
    assert response.json()["data"]["kpi"]["reservations"] >= 1


def test_d_sec_012_room_filter_api(client: TestClient) -> None:
    _login(client, "A1")
    all_rooms = client.get("/api/admin/analytics")
    assert all_rooms.status_code == 200
    assert {item["id"] for item in all_rooms.json()["data"]["rooms"]} == {"R1", "R2"}
    one_room = client.get("/api/admin/analytics", params={"room_id": "R1"})
    assert one_room.status_code == 200
    assert [item["id"] for item in one_room.json()["data"]["rooms"]] == ["R1"]
    missing = client.get("/api/admin/analytics", params={"room_id": "R404"})
    assert missing.status_code == 404


def test_d_sec_013_export_data_minimization(client: TestClient) -> None:
    _login(client, "Y1")
    for kind in EXPORT_KINDS:
        text = client.get(f"/api/admin/export/{kind}.csv").content.decode("utf-8-sig")
        for forbidden in ("password_hash", "session_token", "csrf_token", "credential_code"):
            assert forbidden not in text


def test_d_sec_014_export_audit_success_and_denied(client: TestClient, ctx) -> None:
    _login(client, "Y1")
    assert client.get("/api/admin/export/reservations.csv").status_code == 200
    _login(client, "A1")
    assert client.get("/api/admin/export/reservations.csv").status_code == 403
    with ctx.db.read() as conn:
        rows = conn.execute(
            "SELECT actor_id, after_json FROM audit_logs WHERE action='export_csv' ORDER BY created_at DESC, id DESC LIMIT 2"
        ).fetchall()
    assert len(rows) == 2
    results = {json.loads(row["after_json"])["result"] for row in rows}
    assert {"success", "denied"} <= results


def test_d_sec_015_notification_limit_api(client: TestClient) -> None:
    headers = _login(client, "S1")
    too_many = client.post(
        "/api/notifications/read",
        json={"ids": [f"N{i}" for i in range(101)]},
        headers=headers,
    )
    assert too_many.status_code == 422
    ok = client.post(
        "/api/notifications/read",
        json={"ids": [f"N{i}" for i in range(100)]},
        headers=headers,
    )
    assert ok.status_code == 200


def test_d_sec_016_export_permission_api(client: TestClient) -> None:
    _login(client, "A1")
    assert client.get("/api/admin/export/reservations.csv").status_code == 403
    _login(client, "S1")
    assert client.get("/api/admin/export/reservations.csv").status_code == 403
    _login(client, "T1")
    assert client.get("/api/admin/export/reservations.csv").status_code == 403
    _login(client, "Y1")
    assert client.get("/api/admin/export/reservations.csv").status_code == 200


def test_d_sec_017_analytics_and_export_injection_inputs(client: TestClient) -> None:
    _login(client, "Y1")
    injected_room = client.get(
        "/api/admin/analytics", params={"room_id": "' OR 1=1--"}
    )
    assert injected_room.status_code == 404
    injected_date = client.get(
        "/api/admin/export/reservations.csv",
        params={"date_from": "' OR 1=1--"},
    )
    assert injected_date.status_code == 422
    unknown_path = client.get("/api/admin/export/../../etc/passwd.csv")
    assert unknown_path.status_code in {400, 404, 405}


def test_d_sec_018_csv_nul_bom_newline_api(client: TestClient, ctx) -> None:
    values = ["\x00=1", "\ufeff=1", "\n=1"]
    with ctx.db.transaction() as conn:
        repo = SqliteRepository(conn)
        for index, value in enumerate(values):
            repo.insert_complaint(
                Complaint(
                    id=f"CP-SEC-018-{index}",
                    user_id="S1",
                    complaint_type="facility",
                    content=value,
                    status="pending",
                    created_at=NOW,
                )
            )
    _login(client, "Y1")
    rows = _csv_rows(client.get("/api/admin/export/complaints.csv"))
    index = rows[0].index("内容")
    exported = {row[0]: row[index] for row in rows[1:] if row[0].startswith("CP-SEC-018")}
    assert len(exported) == 3
    for value in exported.values():
        assert not value.startswith(("=", "+", "-", "@"))
        assert "\x00" not in value
        assert "\ufeff" not in value


def test_d_sec_019_export_audit_columns(client: TestClient) -> None:
    _login(client, "Y1")
    rows = _csv_rows(client.get("/api/admin/export/audit.csv"))
    assert rows
    assert "before" not in rows[0]
    assert "after" not in rows[0]
    assert "日志号" in rows[0]


def test_d_sec_020_extreme_date_api(client: TestClient) -> None:
    _login(client, "Y1")
    low = client.get("/api/admin/analytics", params={"date_to": "0001-01-01"})
    high = client.get("/api/admin/analytics", params={"date_from": "9999-12-31"})
    assert low.status_code == 422
    assert high.status_code == 422


def test_d_sec_021_empty_export_api(client: TestClient) -> None:
    _login(client, "Y1")
    response = client.get(
        "/api/admin/export/credit_logs.csv",
        params={"date_from": "2050-01-01", "date_to": "2050-01-02"},
    )
    assert response.status_code == 200
    assert response.content.startswith(b"\xef\xbb\xbf")
    rows = _csv_rows(response)
    assert len(rows) == 1
    assert rows[0]


def test_d_sec_022_export_row_cap_api(client: TestClient, ctx, monkeypatch: pytest.MonkeyPatch) -> None:
    env = Env(ctx)
    Env.create(env, "S1", "R1-001", time(9, 0), time(10, 0))
    Env.create(env, "S2", "R1-002", time(9, 0), time(10, 0))
    monkeypatch.setattr(engine_module, "MAX_EXPORT_ROWS", 1)
    _login(client, "Y1")
    response = client.get(
        "/api/admin/export/reservations.csv",
        params={"date_from": D.isoformat(), "date_to": D.isoformat()},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_d_sec_023_analytics_after_cancel(env: Env) -> None:
    created = Env.create(env, "S1", "R1-001", time(9, 0), time(10, 0))
    assert created.ok
    cancelled = Env.cancel(env, "S1", created.data["id"])
    assert cancelled.ok
    with env.ctx.engine(write=False) as engine:
        data = engine.admin_analytics(
            env.actor("Y1"),
            date_from=D,
            date_to=D,
            now=NOW,
        )
    assert data["kpi"]["cancelled"] >= 1


def test_d_sec_024_export_audit_has_no_secrets(client: TestClient, ctx) -> None:
    _login(client, "Y1")
    assert client.get("/api/admin/export/reservations.csv").status_code == 200
    with ctx.db.read() as conn:
        row = conn.execute(
            "SELECT after_json FROM audit_logs WHERE action='export_csv' ORDER BY created_at DESC, id DESC LIMIT 1"
        ).fetchone()
    assert row is not None
    text = row["after_json"].lower()
    assert "password" not in text
    assert "session_token" not in text
    assert "csrf" not in text


def test_d_sec_025_frontend_export_entries() -> None:
    app_js = Path(__file__).resolve().parents[1] / "src/seat_reservation/web/app.js"
    text = app_js.read_text(encoding="utf-8")
    for kind in EXPORT_KINDS:
        assert f"/api/admin/export/' + kind + '.csv" in text or kind in text
    for label in ("预约记录", "违规记录", "审计日志", "投诉记录", "黑名单记录", "信用流水", "候补记录"):
        assert label in text
