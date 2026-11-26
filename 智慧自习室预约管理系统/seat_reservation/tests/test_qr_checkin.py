"""座位桌贴二维码：令牌签名、签到规则与打印页。"""

from __future__ import annotations

from datetime import datetime, time

import pytest
from fastapi.testclient import TestClient

from seat_reservation.api.app import create_app
from seat_reservation.config import Settings
from seat_reservation.errors import RuleViolation
from seat_reservation.security import build_seat_qr_token, seat_id_from_qr_token
from tests.test_api import login

BOOK_START = time(9, 0)
BOOK_END = time(10, 0)
CHECKIN_AT = datetime(2026, 5, 11, 8, 50)  # 窗口 8:45 开启
EARLY_AT = datetime(2026, 5, 11, 8, 0)


def _token(env, seat: str = "R1-001") -> str:
    with env.ctx.engine(write=False) as engine:
        return engine.seat_qr_token(seat)


def _client(env) -> TestClient:
    return TestClient(create_app(Settings.for_tests(env.ctx.settings.db_path)))


def test_seat_token_roundtrip_and_tamper() -> None:
    token = build_seat_qr_token("R1-001", "secret")
    assert seat_id_from_qr_token(token, "secret") == "R1-001"
    # 密钥不同、签名被改、版本不符、格式错误一律拒绝
    assert seat_id_from_qr_token(token, "other-secret") is None
    assert seat_id_from_qr_token(token[:-1] + ("A" if token[-1] != "A" else "B"), "secret") is None
    assert seat_id_from_qr_token("q9." + token.split(".", 1)[1], "secret") is None
    assert seat_id_from_qr_token("q1.bad", "secret") is None
    assert seat_id_from_qr_token("", "secret") is None


def test_qr_check_in_success(env) -> None:
    created = env.create("S1", "R1-001", BOOK_START, BOOK_END)
    assert created.ok
    token = _token(env)
    with env.ctx.engine() as engine:
        result = engine.check_in_by_seat_qr(env.actor("S1"), token, CHECKIN_AT)
    assert result.ok
    assert result.data["status"] == "in_use"
    assert env.reservation(created.data["id"]).status == "in_use"


def test_qr_check_in_requires_own_reservation(env) -> None:
    env.create("S1", "R1-001", BOOK_START, BOOK_END)
    token = _token(env)
    with env.ctx.engine() as engine:
        with pytest.raises(RuleViolation) as exc:
            engine.check_in_by_seat_qr(env.actor("S2"), token, CHECKIN_AT)
    assert exc.value.rule == "BR-13"


def test_qr_check_in_without_reservation(env) -> None:
    token = _token(env)
    with env.ctx.engine() as engine:
        with pytest.raises(RuleViolation):
            engine.check_in_by_seat_qr(env.actor("S1"), token, CHECKIN_AT)


def test_qr_check_in_early_is_rejected(env) -> None:
    env.create("S1", "R1-001", BOOK_START, BOOK_END)
    token = _token(env)
    with env.ctx.engine() as engine:
        with pytest.raises(RuleViolation) as exc:
            engine.check_in_by_seat_qr(env.actor("S1"), token, EARLY_AT)
    assert exc.value.rule == "BR-04"


def test_qr_check_in_rejects_token_for_another_seat(env) -> None:
    env.create("S1", "R1-001", BOOK_START, BOOK_END)
    token = _token(env, "R1-002")
    with env.ctx.engine() as engine:
        with pytest.raises(RuleViolation):
            engine.check_in_by_seat_qr(env.actor("S1"), token, CHECKIN_AT)


def test_qr_check_in_rejects_tampered_token(env) -> None:
    env.create("S1", "R1-001", BOOK_START, BOOK_END)
    token = _token(env)
    broken = token[:-3] + "abc"
    with env.ctx.engine() as engine:
        with pytest.raises(RuleViolation):
            engine.check_in_by_seat_qr(env.actor("S1"), broken, CHECKIN_AT)


def test_qr_check_in_api(env, monkeypatch) -> None:
    created = env.create("S1", "R1-001", BOOK_START, BOOK_END)
    assert created.ok
    token = _token(env)

    import seat_reservation.api.routes.student_routes as routes

    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):  # noqa: ANN001 - 与 datetime.now 签名保持一致
            return CHECKIN_AT

    monkeypatch.setattr(routes, "datetime", FixedDatetime)
    with _client(env) as client:
        headers, _ = login(client, "S1")
        response = client.post("/api/check-in/qr", json={"token": token}, headers=headers)
        assert response.status_code == 200, response.text
        assert response.json()["data"]["status"] == "in_use"


def test_qr_sheet_renders_for_admin_and_rejects_student(env) -> None:
    with _client(env) as client:
        login(client, "A1")
        response = client.get("/api/admin/rooms/R1/qr-sheet")
        assert response.status_code == 200
        assert "R1-001" in response.text
        assert "<svg" in response.text
    with _client(env) as client:
        login(client, "S1")
        assert client.get("/api/admin/rooms/R1/qr-sheet").status_code == 403


def test_qr_entry_serves_spa(env) -> None:
    token = _token(env)
    with _client(env) as client:
        response = client.get(f"/q/{token}")
        assert response.status_code == 200
        assert 'id="login"' in response.text
