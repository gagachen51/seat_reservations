"""C 模块安全整改测试（C-SEC-001 ~ C-SEC-025）。

对应文档：
* ``03-C模块预约与候补整改任务书.md``：M-04 / M-06 / M-07 / L-07。
* ``08-C模块安全整改测试用例.md``：25 条用例逐条映射。

测试全部使用 ``ctx`` 临时 SQLite 与 ``helpers.seed_fixture`` 的
``S1~S5/T1/A1/Y1/R1/R2`` 夹具；时间通过 ``now`` 注入，HTTP 用例冻结
``student_routes.datetime``，避免系统时钟波动。
"""

from __future__ import annotations

import csv
import io
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import replace
from datetime import date, datetime, time, timedelta

import pytest
from fastapi.testclient import TestClient

import seat_reservation.api.routes.student_routes as student_routes
from seat_reservation.api.app import create_app
from seat_reservation.config import Settings
from seat_reservation.domain.models import (
    Actor,
    ReservationStatus,
    Role,
    Seat,
    User,
    WaitlistStatus,
)
from seat_reservation.domain.reservation_rules import (
    checkin_account_error,
    extension_account_error,
    extension_bounds_error,
    extension_minutes_error,
    extension_quota_error,
    extension_state_error,
    max_extension_minutes,
    waitlist_cancel_state_error,
    waitlist_confirm_state_error,
    waitlist_target_error,
)
from seat_reservation.errors import (
    Conflict,
    Forbidden,
    RuleViolation,
    SlotConflict,
    ValidationFailed,
)
from seat_reservation.security import hash_password
from seat_reservation.service.container import AppContext
from seat_reservation.storage.repository import SqliteRepository
from tests.helpers import D, NOW, PASSWORD, Env, make_credential

EIGHT_FIFTY = datetime(2026, 5, 11, 8, 50, 0)
NINE_THIRTY = datetime(2026, 5, 11, 9, 30, 0)
TEN_SIXTEEN = datetime(2026, 5, 11, 10, 16, 0)


# ---------------------------------------------------------------------------
# 辅助
# ---------------------------------------------------------------------------
@contextmanager
def api_client(
    env: Env,
    monkeypatch: pytest.MonkeyPatch,
    *,
    frozen: datetime = EIGHT_FIFTY,
    acs_token: str = "",
    username: str = "S1",
):
    """冻结学生路由时间并返回已登录的 TestClient。"""
    settings = replace(Settings.for_tests(env.ctx.settings.db_path), acs_token=acs_token)
    app = create_app(settings)

    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):  # noqa: ANN001
            return frozen

    monkeypatch.setattr(student_routes, "datetime", FrozenDatetime)
    with TestClient(app) as client:
        if username:
            response = client.post(
                "/api/auth/login", json={"username": username, "password": PASSWORD}
            )
            assert response.status_code == 200, response.text
        yield client


def login(client: TestClient, username: str = "S1") -> dict[str, str]:
    response = client.post(
        "/api/auth/login", json={"username": username, "password": PASSWORD}
    )
    assert response.status_code == 200, response.text
    return {"X-CSRF-Token": client.cookies.get("seat_csrf") or ""}


def _book(
    env: Env,
    user_id: str,
    seat_id: str,
    start: time = time(9, 0),
    end: time = time(10, 0),
    *,
    now: datetime = NOW,
    day: date = D,
    status: str | None = None,
) -> str:
    created = env.create(user_id, seat_id, start, end, now=now, day=day)
    assert created.ok, created.message
    reservation_id = created.data["id"]
    if status is not None:
        with env.ctx.db.transaction() as conn:
            conn.execute(
                "UPDATE reservations SET status=? WHERE id=?", (status, reservation_id)
            )
    return reservation_id


def _set_user(env: Env, user_id: str, **fields) -> None:
    with env.ctx.db.transaction() as conn:
        repo = SqliteRepository(conn)
        user = repo.get_user(user_id)
        assert user is not None
        for key, value in fields.items():
            setattr(user, key, value)
        user.updated_at = NOW
        repo.save_user(user)


def _set_seat(env: Env, seat_id: str, admin_status: str) -> None:
    with env.ctx.db.transaction() as conn:
        conn.execute(
            "UPDATE seats SET admin_status=? WHERE id=?", (admin_status, seat_id)
        )


def _set_room(env: Env, room_id: str, **fields) -> None:
    with env.ctx.db.transaction() as conn:
        assignments = ", ".join(f"{key}=?" for key in fields)
        conn.execute(
            f"UPDATE rooms SET {assignments} WHERE id=?",
            (*fields.values(), room_id),
        )


def _close_room(env: Env, room_id: str = "R1") -> None:
    with env.ctx.engine() as engine:
        engine.update_room(
            env.actor("A1"),
            room_id,
            name=None,
            location=None,
            status="closed",
            open_time=None,
            close_time=None,
            now=NOW,
        )


def _reservation_count(env: Env) -> int:
    with env.ctx.db.read() as conn:
        return int(conn.execute("SELECT COUNT(*) AS c FROM reservations").fetchone()["c"])


def _slots(env: Env, reservation_id: str) -> list[int]:
    with env.ctx.db.read() as conn:
        rows = conn.execute(
            "SELECT slot FROM reservation_slots WHERE reservation_id=? ORDER BY slot",
            (reservation_id,),
        ).fetchall()
    return [int(row["slot"]) for row in rows]


def _entry_row(env: Env, entry_id: str) -> dict:
    with env.ctx.db.read() as conn:
        row = conn.execute("SELECT * FROM waitlist WHERE id=?", (entry_id,)).fetchone()
    assert row is not None
    return dict(row)


def _extend(
    env: Env,
    user_id: str,
    reservation_id: str,
    extra_minutes: int = 30,
    *,
    now: datetime = NINE_THIRTY,
):
    with env.ctx.engine() as engine:
        return engine.extend_reservation(
            env.actor(user_id), reservation_id, extra_minutes, now
        )


def _add_students(env: Env, count: int) -> list[str]:
    ids: list[str] = []
    with env.ctx.db.transaction() as conn:
        repo = SqliteRepository(conn)
        for index in range(count):
            user_id = f"C{index:03d}"
            ids.append(user_id)
            repo.save_user(
                User(
                    id=user_id,
                    name=f"并发{index}",
                    role=Role.STUDENT.value,
                    credit=100,
                    password_hash=hash_password("Passw0rd!23", iterations=1000),
                    created_at=NOW,
                    updated_at=NOW,
                )
            )
    return ids


# ---------------------------------------------------------------------------
# C-SEC-001 续约规则单元
# ---------------------------------------------------------------------------
def test_c_sec_001_extension_rule_unit() -> None:
    end_at = datetime(2026, 5, 11, 10, 0)
    assert extension_state_error("in_use", NOW, end_at) is None
    assert extension_state_error("away", NOW, end_at) is None
    for status in ("pending", "completed", "cancelled", "no_show", "released"):
        error = extension_state_error(status, NOW, end_at)
        assert error is not None and error[0] == "ST-12"
    ended = extension_state_error("in_use", end_at, end_at)
    assert ended is not None and ended[0] == "ST-12"

    assert extension_minutes_error(30) is None
    assert extension_minutes_error(1440) is None
    for value in (0, 15, 45, -30, "30", None, True):
        assert extension_minutes_error(value) is not None

    assert extension_bounds_error(18, 22, 46, 240) is None
    assert extension_bounds_error(18, 48, 46, 240)[0] == "BR-01"
    assert extension_bounds_error(18, 30, 46, 240)[0] == "BR-01"
    assert extension_quota_error(120, 60, 240) is None
    assert extension_quota_error(200, 60, 240)[0] == "BR-02"
    assert max_extension_minutes(18, 20, 46, 240, 180, 240) == 60


# ---------------------------------------------------------------------------
# C-SEC-002 候补校验纯函数单元
# ---------------------------------------------------------------------------
def test_c_sec_002_waitlist_rule_unit() -> None:
    assert waitlist_cancel_state_error("queued") is None
    assert waitlist_cancel_state_error("notified") is None
    assert waitlist_cancel_state_error("cancelled")[0] == "ST-12"

    assert waitlist_confirm_state_error("notified") is None
    assert waitlist_confirm_state_error("queued")[0] == "ST-12"
    assert waitlist_confirm_state_error("cancelled")[0] == "ST-12"

    start_at = datetime(2026, 5, 11, 9, 0)
    args = ("open", "available", False, 18, 20, 14, 46, start_at, NOW)
    assert waitlist_target_error(*args) is None
    assert waitlist_target_error("closed", "available", False, 18, 20, 14, 46, start_at, NOW)[0] == "FR-21"
    assert waitlist_target_error("open", "disabled", False, 18, 20, 14, 46, start_at, NOW)[0] == "BR-09"
    assert waitlist_target_error("open", "maintenance", False, 18, 20, 14, 46, start_at, NOW)[0] == "BR-09"
    assert waitlist_target_error("open", "available", True, 18, 20, 14, 46, start_at, NOW)[0] == "BR-09"
    assert waitlist_target_error("open", "available", False, 18, 20, 20, 46, start_at, NOW)[0] == "BR-03"
    assert waitlist_target_error("open", "available", False, 18, 20, 14, 46, start_at, start_at)[0] == "BR-08"


# ---------------------------------------------------------------------------
# C-SEC-003 签到账号状态单元
# ---------------------------------------------------------------------------
def test_c_sec_003_checkin_account_state_unit() -> None:
    assert checkin_account_error(True, None) is None
    assert checkin_account_error(False, None)[0] == "PR-02"
    assert checkin_account_error(True, ("BR-07", "低信用"))[0] == "BR-07"
    assert checkin_account_error(True, ("BR-20", "黑名单"))[0] == "BR-20"
    assert checkin_account_error(True, ("BR-15", "连续违约"))[0] == "BR-15"
    assert extension_account_error(False, None)[0] == "PR-02"
    assert extension_account_error(True, None) is None


# ---------------------------------------------------------------------------
# C-SEC-004 凭证签发幂等/并发单元
# ---------------------------------------------------------------------------
def test_c_sec_004_credential_issue_concurrent(env: Env) -> None:
    reservation_id = _book(env, "S1", "R1-001")

    def issue_once() -> tuple[bool, str]:
        context = AppContext(env.ctx.settings)
        try:
            with context.engine() as engine:
                result = engine.issue_credential(
                    env.actor("S1"), reservation_id, datetime(2026, 5, 11, 8, 0)
                )
            return result.ok, str(result.data.get("code") if result.ok else "")
        except Conflict:
            return False, ""

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: issue_once(), range(2)))

    codes = [code for ok, code in results if ok and code]
    assert codes, results
    stored = env.reservation(reservation_id).credential_code
    assert stored in codes
    assert len(set(codes)) == 1


# ---------------------------------------------------------------------------
# C-SEC-005 sweep 分类单元
# ---------------------------------------------------------------------------
def test_c_sec_005_sweep_classification(env: Env) -> None:
    with env.ctx.db.transaction() as conn:
        repo = SqliteRepository(conn)
        for index in (3, 4, 5):
            repo.save_seat(Seat(id=f"R2-{index:03d}", room_id="R2", created_at=NOW))

    closed = _book(env, "S1", "R1-001")
    normal = _book(env, "S2", "R2-001", time(10, 0), time(11, 0))
    away = _book(env, "S3", "R2-002")
    in_use = _book(env, "S5", "R2-003")
    with env.ctx.db.transaction() as conn:
        conn.execute(
            "UPDATE reservations SET status='away', away_deadline='2026-05-11 09:30:00' WHERE id=?",
            (away,),
        )
        conn.execute(
            "UPDATE reservations SET status='in_use', checked_in_at='2026-05-11 08:50:00' WHERE id=?",
            (in_use,),
        )
    _close_room(env)

    summary = env.sweep(now=TEN_SIXTEEN)
    assert summary["resource_unavailable"] >= 1
    assert summary["no_show"] == 1
    assert summary["away_timeout"] == 1
    assert summary["late_checkout"] == 1

    assert env.reservation(closed).status == ReservationStatus.CANCELLED.value
    assert env.reservation(closed).end_reason == "resource_unavailable"
    assert env.reservation(normal).status == ReservationStatus.NO_SHOW.value
    assert env.reservation(away).status == ReservationStatus.RELEASED.value
    assert env.reservation(in_use).status == ReservationStatus.COMPLETED.value
    assert env.user("S1").credit == 100
    assert env.violations("S1") == []

    repeated = env.sweep(now=TEN_SIXTEEN)
    assert repeated["no_show"] == 0
    assert repeated["resource_unavailable"] == 0
    assert repeated["away_timeout"] == 0
    assert repeated["late_checkout"] == 0


# ---------------------------------------------------------------------------
# C-SEC-006 预约创建与冲突 API
# ---------------------------------------------------------------------------
def test_c_sec_006_reservation_create_conflict_api(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    with api_client(env, monkeypatch, frozen=datetime(2026, 5, 11, 8, 0)) as client:
        headers = {"X-CSRF-Token": client.cookies.get("seat_csrf") or ""}
        payload = {
            "seat_id": "R1-001",
            "date": "2026-05-11",
            "start": "09:00",
            "end": "10:00",
        }
        created = client.post("/api/reservations", headers=headers, json=payload)
        assert created.status_code == 201, created.text

        conflict = client.post("/api/reservations", headers=headers, json=payload)
        assert conflict.status_code == 409
        assert conflict.json()["error"]["rule"] == "BR-11"

        user_conflict = client.post(
            "/api/reservations",
            headers=headers,
            json={**payload, "seat_id": "R1-002", "start": "09:30", "end": "10:30"},
        )
        assert user_conflict.status_code == 409

        _close_room(env)
        closed = client.post(
            "/api/reservations",
            headers=headers,
            json={**payload, "seat_id": "R1-003", "start": "11:00", "end": "12:00"},
        )
        assert closed.status_code == 400
        assert closed.json()["error"]["rule"] == "FR-21"

        _set_seat(env, "R2-001", "disabled")
        disabled = client.post(
            "/api/reservations",
            headers=headers,
            json={**payload, "seat_id": "R2-001", "start": "13:00", "end": "14:00"},
        )
        assert disabled.status_code == 400
        assert disabled.json()["error"]["rule"] == "BR-09"

        with env.ctx.db.read() as conn:
            slots = conn.execute(
                "SELECT COUNT(*) AS c FROM reservation_slots WHERE seat_id='R1-001' AND date=?",
                (D.isoformat(),),
            ).fetchone()["c"]
        assert slots == 2


# ---------------------------------------------------------------------------
# C-SEC-007 签到/签退/暂离/返座 API
# ---------------------------------------------------------------------------
def test_c_sec_007_checkin_lifecycle_api(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    reservation_id = _book(env, "S1", "R1-001")
    with api_client(env, monkeypatch, frozen=EIGHT_FIFTY) as client:
        headers = {"X-CSRF-Token": client.cookies.get("seat_csrf") or ""}
        issued = client.post(f"/api/reservations/{reservation_id}/credential", headers=headers)
        assert issued.status_code == 200, issued.text
        code = issued.json()["data"]["code"]

        checked = client.post(
            f"/api/reservations/{reservation_id}/check-in",
            headers=headers,
            json={"code": code},
        )
        assert checked.status_code == 200, checked.text
        assert env.reservation(reservation_id).status == ReservationStatus.IN_USE.value

        away = client.post(f"/api/reservations/{reservation_id}/away", headers=headers)
        assert away.status_code == 200, away.text
        assert env.reservation(reservation_id).status == ReservationStatus.AWAY.value

        resumed = client.post(f"/api/reservations/{reservation_id}/resume", headers=headers)
        assert resumed.status_code == 200, resumed.text
        assert env.reservation(reservation_id).status == ReservationStatus.IN_USE.value

        checked_out = client.post(f"/api/reservations/{reservation_id}/check-out", headers=headers)
        assert checked_out.status_code == 200, checked_out.text
        assert env.reservation(reservation_id).status == ReservationStatus.COMPLETED.value

        repeated_out = client.post(f"/api/reservations/{reservation_id}/check-out", headers=headers)
        assert repeated_out.status_code == 400
        assert env.reservation(reservation_id).status == ReservationStatus.COMPLETED.value


# ---------------------------------------------------------------------------
# C-SEC-008 续约 API
# ---------------------------------------------------------------------------
def test_c_sec_008_extend_api(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    reservation_id = _book(env, "S1", "R1-001", status=ReservationStatus.IN_USE.value)
    with api_client(env, monkeypatch, frozen=NINE_THIRTY) as client:
        headers = {"X-CSRF-Token": client.cookies.get("seat_csrf") or ""}
        extended = client.post(
            f"/api/reservations/{reservation_id}/extend",
            headers=headers,
            json={"extra_minutes": 30},
        )
        assert extended.status_code == 200, extended.text
        assert extended.json()["data"]["end"] == "10:30"

        invalid = client.post(
            f"/api/reservations/{reservation_id}/extend",
            headers=headers,
            json={"extra_minutes": 45},
        )
        assert invalid.status_code == 422

        assert env.create("S2", "R1-001", time(10, 30), time(11, 30), now=NOW).ok
        conflict = client.post(
            f"/api/reservations/{reservation_id}/extend",
            headers=headers,
            json={"extra_minutes": 30},
        )
        assert conflict.status_code == 409
        assert conflict.json()["error"]["rule"] == "BR-11"
        assert _slots(env, reservation_id) == [18, 19, 20]

    restricted = _book(env, "S5", "R1-002", status=ReservationStatus.IN_USE.value)
    _set_user(env, "S5", credit=55, limit_type="credit")
    with api_client(env, monkeypatch, frozen=NINE_THIRTY, username="S5") as client:
        headers = {"X-CSRF-Token": client.cookies.get("seat_csrf") or ""}
        denied = client.post(
            f"/api/reservations/{restricted}/extend",
            headers=headers,
            json={"extra_minutes": 30},
        )
        assert denied.status_code == 403
        assert denied.json()["error"]["rule"] == "BR-07"


# ---------------------------------------------------------------------------
# C-SEC-009 候补加入/确认/取消 API
# ---------------------------------------------------------------------------
def test_c_sec_009_waitlist_api(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    held = _book(env, "S1", "R1-001")
    with api_client(env, monkeypatch, frozen=datetime(2026, 5, 11, 8, 12), username="S2") as client:
        headers = {"X-CSRF-Token": client.cookies.get("seat_csrf") or ""}
        joined = client.post(
            "/api/waitlist",
            headers=headers,
            json={"seat_id": "R1-001", "date": "2026-05-11", "start": "09:00", "end": "10:00"},
        )
        assert joined.status_code == 201, joined.text
        entry_id = joined.json()["data"]["id"]
        assert _entry_row(env, entry_id)["status"] == WaitlistStatus.QUEUED.value

        duplicate = client.post(
            "/api/waitlist",
            headers=headers,
            json={"seat_id": "R1-001", "date": "2026-05-11", "start": "09:00", "end": "10:00"},
        )
        assert duplicate.status_code == 409

        not_notified = client.post(f"/api/waitlist/{entry_id}/confirm", headers=headers, json={})
        assert not_notified.status_code == 400
        assert not_notified.json()["error"]["rule"] == "ST-12"

        assert env.cancel("S1", held, now=datetime(2026, 5, 11, 8, 10)).ok
        assert _entry_row(env, entry_id)["status"] == WaitlistStatus.NOTIFIED.value

        confirmed = client.post(f"/api/waitlist/{entry_id}/confirm", headers=headers, json={})
        assert confirmed.status_code == 200, confirmed.text
        assert confirmed.json()["data"]["status"] == ReservationStatus.PENDING.value
        assert _entry_row(env, entry_id)["status"] == WaitlistStatus.CONFIRMED.value

    assert env.create("S5", "R1-002", time(9, 0), time(10, 0), now=NOW).ok
    queued = env.join_waitlist("S3", "R1-002", time(9, 0), time(10, 0), now=NOW)
    assert queued.ok, queued.message
    with api_client(env, monkeypatch, frozen=datetime(2026, 5, 11, 8, 12), username="S3") as client:
        headers = {"X-CSRF-Token": client.cookies.get("seat_csrf") or ""}
        cancelled = client.post(
            f"/api/waitlist/{queued.data['id']}/cancel", headers=headers, json={}
        )
        assert cancelled.status_code == 200, cancelled.text
        assert cancelled.json()["data"]["entry"]["status"] == WaitlistStatus.CANCELLED.value


# ---------------------------------------------------------------------------
# C-SEC-010 ACS 签到 API
# ---------------------------------------------------------------------------
def test_c_sec_010_acs_checkin_api(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    normal = _book(env, "S1", "R1-001")
    normal_code = make_credential(
        env, "S1", normal, now=datetime(2026, 5, 11, 8, 49, 30)
    )
    expired = _book(env, "S2", "R1-002")
    expired_code = make_credential(
        env, "S2", expired, now=datetime(2026, 5, 11, 8, 20)
    )
    disabled = _book(env, "S3", "R1-003")
    disabled_code = make_credential(
        env, "S3", disabled, now=datetime(2026, 5, 11, 8, 49, 30)
    )
    _set_user(env, "S3", status="disabled")

    with api_client(env, monkeypatch, frozen=EIGHT_FIFTY, acs_token="acs-secret", username="") as client:
        unauthorized = client.post("/api/acs/check-in", json={"code": normal_code})
        assert unauthorized.status_code == 403

        wrong_token = client.post(
            "/api/acs/check-in",
            json={"code": normal_code},
            headers={"X-ACS-Token": "wrong"},
        )
        assert wrong_token.status_code == 403

        valid = client.post(
            "/api/acs/check-in",
            json={"code": normal_code},
            headers={"X-ACS-Token": "acs-secret"},
        )
        assert valid.status_code == 200, valid.text
        assert env.reservation(normal).status == ReservationStatus.IN_USE.value

        reused = client.post(
            "/api/acs/check-in",
            json={"code": normal_code},
            headers={"X-ACS-Token": "acs-secret"},
        )
        assert reused.status_code == 400

        expired_response = client.post(
            "/api/acs/check-in",
            json={"code": expired_code},
            headers={"X-ACS-Token": "acs-secret"},
        )
        assert expired_response.status_code == 400
        assert env.reservation(expired).status == ReservationStatus.PENDING.value

        disabled_response = client.post(
            "/api/acs/check-in",
            json={"code": disabled_code},
            headers={"X-ACS-Token": "acs-secret"},
        )
        assert disabled_response.status_code == 403
        assert disabled_response.json()["error"]["rule"] == "PR-02"
        assert env.reservation(disabled).status == ReservationStatus.PENDING.value


# ---------------------------------------------------------------------------
# C-SEC-011 房间关闭后的预约不 no_show
# ---------------------------------------------------------------------------
def test_c_sec_011_closed_room_no_no_show(env: Env) -> None:
    reservation_id = _book(env, "S1", "R1-001")
    notification_before = len(env.notifications("S1"))
    _close_room(env)

    summary = env.sweep(now=datetime(2026, 5, 11, 9, 16))
    assert summary["no_show"] == 0
    assert summary["resource_unavailable"] == 1

    saved = env.reservation(reservation_id)
    assert saved.status == ReservationStatus.CANCELLED.value
    assert saved.end_reason == "resource_unavailable"
    assert env.user("S1").credit == 100
    assert env.violations("S1") == []
    assert len(env.notifications("S1")) == notification_before + 1
    assert any("不记违约" in item.content for item in env.notifications("S1"))
    assert any(
        log.action == "reservation_resource_unavailable" and log.target_id == reservation_id
        for log in env.audit()
    )

    repeated = env.sweep(now=datetime(2026, 5, 11, 9, 17))
    assert repeated["no_show"] == 0
    assert repeated["resource_unavailable"] == 0
    assert len(env.notifications("S1")) == notification_before + 1
    assert len(
        [log for log in env.audit() if log.action == "reservation_resource_unavailable"]
    ) == 1


# ---------------------------------------------------------------------------
# C-SEC-012 座位停用/维护后的预约处理
# ---------------------------------------------------------------------------
def test_c_sec_012_seat_disabled_active_reservations(env: Env) -> None:
    pending = _book(env, "S1", "R1-001")
    in_use = _book(env, "S2", "R1-002", status=ReservationStatus.IN_USE.value)
    away = _book(env, "S3", "R1-003", status=ReservationStatus.AWAY.value)
    _set_seat(env, "R1-001", "disabled")
    _set_seat(env, "R1-002", "maintenance")
    _set_seat(env, "R1-003", "disabled")

    summary = env.sweep(now=datetime(2026, 5, 11, 8, 30))
    assert summary["resource_unavailable"] == 3
    assert summary["no_show"] == 0
    for reservation_id in (pending, in_use, away):
        saved = env.reservation(reservation_id)
        assert saved.status == ReservationStatus.CANCELLED.value
        assert saved.end_reason == "resource_unavailable"
    for user_id in ("S1", "S2", "S3"):
        assert env.user(user_id).credit == 100
        assert env.violations(user_id) == []

    repeated = env.sweep(now=datetime(2026, 5, 11, 8, 31))
    assert repeated["resource_unavailable"] == 0
    assert len(
        [log for log in env.audit() if log.action == "reservation_resource_unavailable"]
    ) == 3


# ---------------------------------------------------------------------------
# C-SEC-013 停用/黑名单/信用限制账号
# ---------------------------------------------------------------------------
def test_c_sec_013_restricted_account_rejected(env: Env) -> None:
    cases = [
        ("S1", "R1-001", {"status": "disabled"}, "PR-02"),
        (
            "S2",
            "R1-002",
            {"limit_type": "blacklist", "limit_until": datetime(2026, 5, 12, 0, 0)},
            "BR-20",
        ),
        ("S3", "R1-003", {"credit": 55, "limit_type": "credit"}, "BR-07"),
        (
            "S5",
            "R1-004",
            {
                "limit_type": "consecutive",
                "limit_until": datetime(2026, 5, 12, 0, 0),
                "consecutive_no_show": 3,
            },
            "BR-15",
        ),
    ]
    for user_id, seat_id, fields, expected_rule in cases:
        reservation_id = _book(env, user_id, seat_id)
        code = make_credential(
            env, user_id, reservation_id, now=datetime(2026, 5, 11, 8, 49, 30)
        )
        _set_user(env, user_id, **fields)

        with pytest.raises(Forbidden) as normal:
            env.check_in(user_id, reservation_id, code, now=EIGHT_FIFTY)
        assert normal.value.rule == expected_rule
        assert env.reservation(reservation_id).status == ReservationStatus.PENDING.value

        with env.ctx.engine() as engine:
            with pytest.raises(Forbidden) as acs:
                engine.check_in_by_credential(code, EIGHT_FIFTY, method="acs")
        assert acs.value.rule == expected_rule
        assert env.reservation(reservation_id).status == ReservationStatus.PENDING.value

        with env.ctx.db.transaction() as conn:
            conn.execute(
                "UPDATE reservations SET status='in_use' WHERE id=?", (reservation_id,)
            )
        with pytest.raises(Forbidden) as denied_extend:
            _extend(env, user_id, reservation_id, 30)
        assert denied_extend.value.rule == expected_rule
        assert env.reservation(reservation_id).status == ReservationStatus.IN_USE.value


# ---------------------------------------------------------------------------
# C-SEC-014 候补资源/开放时间校验
# ---------------------------------------------------------------------------
def test_c_sec_014_waitlist_resource_and_hours(env: Env) -> None:
    _book(env, "S1", "R1-001")
    _close_room(env)
    with pytest.raises(RuleViolation) as closed:
        env.join_waitlist("S2", "R1-001", time(9, 0), time(10, 0), now=NOW)
    assert closed.value.rule == "FR-21"
    _set_room(env, "R1", status="open")

    _book(env, "S2", "R2-001", time(10, 0), time(11, 0))
    _set_seat(env, "R2-001", "disabled")
    with pytest.raises(RuleViolation) as disabled:
        env.join_waitlist("S3", "R2-001", time(9, 0), time(10, 0), now=NOW)
    assert disabled.value.rule == "BR-09"

    _book(env, "S3", "R2-002")
    env.block_seat(
        "A1",
        "R2-002",
        "候补时段维修",
        datetime(2026, 5, 11, 9, 0),
        datetime(2026, 5, 11, 10, 0),
        now=NOW,
    )
    with pytest.raises(RuleViolation) as blocked:
        env.join_waitlist("S5", "R2-002", time(9, 0), time(10, 0), now=NOW)
    assert blocked.value.rule == "BR-09"

    _set_room(env, "R1", open_slot=20, close_slot=46)
    with pytest.raises(RuleViolation) as out_of_hours:
        env.join_waitlist("S5", "R1-004", time(9, 0), time(10, 0), now=NOW)
    assert out_of_hours.value.rule == "BR-03"

    # 已有 queued 候补：管理员缩短开放时间后确认失败，不生成预约/slot。
    _set_room(env, "R1", open_slot=14, close_slot=46)
    held_id = _book(env, "S5", "R1-005")
    entry = env.join_waitlist("S2", "R1-005", time(9, 0), time(10, 0), now=NOW)
    assert entry.ok, entry.message
    assert env.cancel("S5", held_id, now=datetime(2026, 5, 11, 8, 10)).ok
    entry_id = entry.data["id"]
    assert _entry_row(env, entry_id)["status"] == WaitlistStatus.NOTIFIED.value
    _set_room(env, "R1", close_slot=19)
    before = _reservation_count(env)
    result = env.confirm_waitlist("S2", entry_id, now=datetime(2026, 5, 11, 8, 12))
    assert result.ok is False
    assert result.rule == "BR-03"
    assert _entry_row(env, entry_id)["status"] == WaitlistStatus.EXPIRED.value
    assert _reservation_count(env) == before
    with env.ctx.db.read() as conn:
        slots = conn.execute(
            "SELECT COUNT(*) AS c FROM reservation_slots WHERE seat_id='R1-005' AND date=?",
            (D.isoformat(),),
        ).fetchone()["c"]
    assert slots == 0


# ---------------------------------------------------------------------------
# C-SEC-015 正常 no_show 仍被正确判定
# ---------------------------------------------------------------------------
def test_c_sec_015_normal_no_show_still_works(env: Env) -> None:
    reservation_id = _book(env, "S1", "R1-001", time(10, 0), time(10, 30))
    summary = env.sweep(now=datetime(2026, 5, 11, 10, 16))
    assert summary["no_show"] == 1
    assert summary["resource_unavailable"] == 0
    saved = env.reservation(reservation_id)
    assert saved.status == ReservationStatus.NO_SHOW.value
    assert saved.end_reason == "no_show"
    assert env.user("S1").credit == 90
    assert len(env.violations("S1")) == 1

    repeated = env.sweep(now=datetime(2026, 5, 11, 10, 17))
    assert repeated["no_show"] == 0
    assert env.user("S1").credit == 90
    assert len(env.violations("S1")) == 1


# ---------------------------------------------------------------------------
# C-SEC-016 预约所有权
# ---------------------------------------------------------------------------
def test_c_sec_016_reservation_ownership(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    owned = _book(env, "S1", "R1-001")
    code = make_credential(env, "S1", owned, now=datetime(2026, 5, 11, 8, 49, 30))
    _book(env, "S2", "R1-002", time(11, 0), time(12, 0))
    entry = env.join_waitlist("S1", "R1-002", time(11, 0), time(12, 0), now=NOW)
    assert entry.ok, entry.message

    with api_client(env, monkeypatch, frozen=EIGHT_FIFTY, username="S2") as client:
        headers = {"X-CSRF-Token": client.cookies.get("seat_csrf") or ""}
        assert client.get(f"/api/reservations/{owned}").status_code == 403
        assert (
            client.post(
                f"/api/reservations/{owned}/check-in",
                headers=headers,
                json={"code": code},
            ).status_code
            == 403
        )
        assert client.post(f"/api/reservations/{owned}/cancel", headers=headers).status_code == 403
        assert (
            client.post(
                f"/api/reservations/{owned}/extend",
                headers=headers,
                json={"extra_minutes": 30},
            ).status_code
            == 403
        )
        assert (
            client.post(f"/api/waitlist/{entry.data['id']}/confirm", headers=headers, json={}).status_code
            == 403
        )
        assert (
            client.post(f"/api/waitlist/{entry.data['id']}/cancel", headers=headers, json={}).status_code
            == 403
        )

    assert env.reservation(owned).status == ReservationStatus.PENDING.value
    assert _entry_row(env, entry.data["id"])["status"] == WaitlistStatus.QUEUED.value
    assert env.user("S1").credit == 100


# ---------------------------------------------------------------------------
# C-SEC-017 管理/教师不能执行学生操作
# ---------------------------------------------------------------------------
def test_c_sec_017_admin_teacher_cannot_student_ops(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    owned = _book(env, "S1", "R1-001")
    code = make_credential(env, "S1", owned, now=datetime(2026, 5, 11, 8, 49, 30))
    _book(env, "S2", "R1-002", time(11, 0), time(12, 0))
    entry = env.join_waitlist("S1", "R1-002", time(11, 0), time(12, 0), now=NOW)
    assert entry.ok, entry.message

    for username in ("T1", "A1", "Y1"):
        with api_client(env, monkeypatch, frozen=EIGHT_FIFTY, username=username) as client:
            headers = {"X-CSRF-Token": client.cookies.get("seat_csrf") or ""}
            created = client.post(
                "/api/reservations",
                headers=headers,
                json={
                    "seat_id": "R1-004",
                    "date": "2026-05-11",
                    "start": "13:00",
                    "end": "14:00",
                },
            )
            assert created.status_code == 403, created.text
            assert (
                client.post(
                    f"/api/reservations/{owned}/check-in",
                    headers=headers,
                    json={"code": code},
                ).status_code
                == 403
            )
            assert (
                client.post(
                    f"/api/reservations/{owned}/extend",
                    headers=headers,
                    json={"extra_minutes": 30},
                ).status_code
                == 403
            )
            joined = client.post(
                "/api/waitlist",
                headers=headers,
                json={
                    "seat_id": "R1-004",
                    "date": "2026-05-11",
                    "start": "13:00",
                    "end": "14:00",
                },
            )
            assert joined.status_code == 403
            assert (
                client.post(f"/api/waitlist/{entry.data['id']}/confirm", headers=headers, json={}).status_code
                == 403
            )

    assert env.reservation(owned).status == ReservationStatus.PENDING.value


# ---------------------------------------------------------------------------
# C-SEC-018 预约 mutation CSRF
# ---------------------------------------------------------------------------
def test_c_sec_018_reservation_csrf(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    extendable = _book(env, "S1", "R1-001", status=ReservationStatus.IN_USE.value)
    with api_client(env, monkeypatch, frozen=NINE_THIRTY) as client:
        payload = {
            "seat_id": "R1-002",
            "date": "2026-05-11",
            "start": "13:00",
            "end": "14:00",
        }
        missing = client.post("/api/reservations", json=payload)
        assert missing.status_code == 403
        assert missing.json()["error"]["code"] == "CSRF_FAILED"

        wrong = client.post(
            "/api/reservations", headers={"X-CSRF-Token": "wrong"}, json=payload
        )
        assert wrong.status_code == 403
        assert wrong.json()["error"]["code"] == "CSRF_FAILED"

        headers = {"X-CSRF-Token": client.cookies.get("seat_csrf") or ""}
        ok = client.post("/api/reservations", headers=headers, json=payload)
        assert ok.status_code == 201, ok.text

        assert client.post(f"/api/reservations/{extendable}/extend", json={"extra_minutes": 30}).status_code == 403
        assert (
            client.post(
                "/api/waitlist",
                json={"seat_id": "R1-003", "date": "2026-05-11", "start": "09:00", "end": "10:00"},
            ).status_code
            == 403
        )
        assert (
            client.post(
                f"/api/reservations/{extendable}/extend",
                headers=headers,
                json={"extra_minutes": 30},
            ).status_code
            == 200
        )


# ---------------------------------------------------------------------------
# C-SEC-019 ACS token 与凭证安全
# ---------------------------------------------------------------------------
def test_c_sec_019_acs_token_and_credential_security(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    reservation_id = _book(env, "S1", "R1-001")
    code = make_credential(env, "S1", reservation_id, now=datetime(2026, 5, 11, 8, 49, 30))
    with api_client(env, monkeypatch, frozen=EIGHT_FIFTY, acs_token="acs-secret", username="") as client:
        assert client.post("/api/acs/check-in", json={"code": code}).status_code == 403
        assert (
            client.post(
                "/api/acs/check-in", json={"code": code}, headers={"X-ACS-Token": "bad"}
            ).status_code
            == 403
        )
        first = client.post(
            "/api/acs/check-in", json={"code": code}, headers={"X-ACS-Token": "acs-secret"}
        )
        assert first.status_code == 200, first.text
        second = client.post(
            "/api/acs/check-in", json={"code": code}, headers={"X-ACS-Token": "acs-secret"}
        )
        assert second.status_code == 400
        assert env.reservation(reservation_id).status == ReservationStatus.IN_USE.value


# ---------------------------------------------------------------------------
# C-SEC-020 非法时段/日期/配额
# ---------------------------------------------------------------------------
def test_c_sec_020_illegal_time_and_quota(env: Env) -> None:
    with pytest.raises(RuleViolation) as reversed_range:
        env.create("S1", "R1-001", time(10, 0), time(9, 0))
    assert reversed_range.value.rule == "BR-01"

    with pytest.raises(ValidationFailed):
        env.create("S1", "R1-001", time(9, 15), time(10, 0))

    with pytest.raises(RuleViolation) as too_long:
        env.create("S1", "R1-001", time(9, 0), time(14, 0))
    assert too_long.value.rule == "BR-01"

    with pytest.raises(RuleViolation) as out_of_window:
        env.create("S1", "R1-001", time(9, 0), time(10, 0), day=D + timedelta(days=30))
    assert out_of_window.value.rule == "BR-03"

    assert env.create("S1", "R1-001", time(9, 0), time(11, 0)).ok
    assert env.create("S1", "R1-002", time(13, 0), time(15, 0)).ok
    before = _reservation_count(env)
    with pytest.raises(RuleViolation) as quota:
        env.create("S1", "R1-003", time(16, 0), time(18, 0))
    assert quota.value.rule == "BR-02"
    assert _reservation_count(env) == before

    assert env.create("S2", "R1-004", time(9, 0), time(11, 0)).ok
    with pytest.raises((RuleViolation, ValidationFailed)):
        env.join_waitlist("S3", "R1-004", time(9, 0), time(9, 0), now=NOW)
    with pytest.raises(RuleViolation) as past:
        env.join_waitlist("S3", "R1-004", time(8, 0), time(9, 0), now=datetime(2026, 5, 11, 9, 0))
    assert past.value.rule in {"BR-03", "BR-08"}


# ---------------------------------------------------------------------------
# C-SEC-021 并发预约唯一性
# ---------------------------------------------------------------------------
def test_c_sec_021_concurrent_reservation_uniqueness(env: Env) -> None:
    users = _add_students(env, 8)

    def book(user_id: str) -> str:
        context = AppContext(env.ctx.settings)
        try:
            with context.engine() as engine:
                result = engine.create_reservation(
                    Actor(id=user_id, name=user_id, role=Role.STUDENT),
                    "R1-001",
                    D,
                    time(9, 0),
                    time(10, 0),
                    NOW,
                )
            return "ok" if result.ok else "conflict"
        except Exception:  # noqa: BLE001 - 并发失败统一记 conflict
            return "conflict"

    with ThreadPoolExecutor(max_workers=8) as pool:
        outcomes = list(pool.map(book, users))

    assert outcomes.count("ok") == 1, outcomes
    with env.ctx.db.read() as conn:
        reservations = conn.execute(
            "SELECT COUNT(*) AS c FROM reservations WHERE seat_id='R1-001' AND date=? AND status='pending'",
            (D.isoformat(),),
        ).fetchone()["c"]
        slots = conn.execute(
            "SELECT COUNT(*) AS c FROM reservation_slots WHERE seat_id='R1-001' AND date=?",
            (D.isoformat(),),
        ).fetchone()["c"]
    assert reservations == 1
    assert slots == 2


# ---------------------------------------------------------------------------
# C-SEC-022 事务失败与凭证并发
# ---------------------------------------------------------------------------
def test_c_sec_022_transaction_rollback_and_credential_concurrency(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    held = _book(env, "S1", "R1-001")
    entry = env.join_waitlist("S2", "R1-001", time(9, 0), time(10, 0), now=NOW)
    assert entry.ok, entry.message
    assert env.cancel("S1", held, now=datetime(2026, 5, 11, 8, 10)).ok
    assert _entry_row(env, entry.data["id"])["status"] == WaitlistStatus.NOTIFIED.value
    credential_target = _book(env, "S3", "R1-002", time(10, 0), time(11, 0))

    def forced_conflict(self, reservation):  # noqa: ANN001, ARG001
        raise SlotConflict("强制占用失败", rule="BR-11")

    monkeypatch.setattr(SqliteRepository, "occupy", forced_conflict)
    before = _reservation_count(env)
    result = env.confirm_waitlist("S2", entry.data["id"], now=datetime(2026, 5, 11, 8, 12))
    assert result.ok is False
    assert _entry_row(env, entry.data["id"])["status"] == WaitlistStatus.EXPIRED.value
    assert _reservation_count(env) == before
    with env.ctx.db.read() as conn:
        waitlisted = conn.execute(
            "SELECT COUNT(*) AS c FROM reservations WHERE source='waitlist'"
        ).fetchone()["c"]
        slots = conn.execute(
            "SELECT COUNT(*) AS c FROM reservation_slots WHERE seat_id='R1-001' AND date=?",
            (D.isoformat(),),
        ).fetchone()["c"]
    assert waitlisted == 0
    assert slots == 0

    def issue_once() -> tuple[bool, str]:
        context = AppContext(env.ctx.settings)
        try:
            with context.engine() as engine:
                response = engine.issue_credential(
                    env.actor("S3"), credential_target, datetime(2026, 5, 11, 8, 0)
                )
            return response.ok, str(response.data.get("code") if response.ok else "")
        except Conflict:
            return False, ""

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: issue_once(), range(2)))
    codes = [code for ok, code in results if ok and code]
    assert codes
    assert env.reservation(credential_target).credential_code in codes


# ---------------------------------------------------------------------------
# C-SEC-023 C+B 资源变更联动
# ---------------------------------------------------------------------------
def test_c_sec_023_c_b_resource_linkage(env: Env) -> None:
    first = _book(env, "S1", "R1-001")
    second = _book(env, "S2", "R1-002")
    _close_room(env)
    with env.ctx.engine() as engine:
        affected = engine.release_affected_reservations(
            "R1", None, "自习室停用", env.actor("A1"), NOW
        )
    assert set(affected) == {first, second}
    for reservation_id in (first, second):
        saved = env.reservation(reservation_id)
        assert saved.status == ReservationStatus.CANCELLED.value
        assert saved.end_reason == "resource_unavailable"
    assert env.user("S1").credit == 100 and env.user("S2").credit == 100
    assert any("不记违约" in item.content for item in env.notifications("S1"))
    assert any(log.action == "reservation_resource_unavailable" for log in env.audit())

    with env.ctx.engine() as engine:
        assert engine.release_affected_reservations("R1", None, "自习室停用", env.actor("A1"), NOW) == []
    assert len(
        [log for log in env.audit() if log.action == "reservation_resource_unavailable"]
    ) == 2

    _set_room(env, "R1", status="open")
    third = _book(env, "S3", "R1-003")
    with env.ctx.engine() as engine:
        engine.update_seat(
            env.actor("A1"),
            "R1-003",
            room_id=None,
            seat_type=None,
            capacity=None,
            admin_status="disabled",
            has_power=None,
            is_window=None,
            note=None,
            now=NOW,
        )
        affected = engine.release_affected_reservations(
            None, "R1-003", "座位停用", env.actor("A1"), NOW
        )
    assert affected == [third]
    assert env.reservation(third).end_reason == "resource_unavailable"


# ---------------------------------------------------------------------------
# C-SEC-024 C+A 账号状态集成
# ---------------------------------------------------------------------------
def test_c_sec_024_c_a_account_integration(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    checkin_target = _book(env, "S1", "R1-001")
    code = make_credential(env, "S1", checkin_target, now=datetime(2026, 5, 11, 8, 49, 30))
    extend_target = _book(
        env, "S1", "R1-002", time(10, 30), time(11, 30), status=ReservationStatus.IN_USE.value
    )

    with api_client(env, monkeypatch, frozen=EIGHT_FIFTY) as client:
        headers = {"X-CSRF-Token": client.cookies.get("seat_csrf") or ""}
        assert client.get("/api/reservations", headers=headers).status_code == 200
        with env.ctx.engine() as engine:
            engine.set_user_status(env.actor("Y1"), "S1", "disabled", "安全整改测试", NOW)
        assert client.get("/api/reservations", headers=headers).status_code == 401

        assert (
            client.post(
                f"/api/reservations/{checkin_target}/check-in",
                headers=headers,
                json={"code": code},
            ).status_code
            == 401
        )
        assert env.reservation(checkin_target).status == ReservationStatus.PENDING.value

    with api_client(env, monkeypatch, frozen=EIGHT_FIFTY, acs_token="acs-secret", username="") as acs:
        denied = acs.post(
            "/api/acs/check-in",
            json={"code": code},
            headers={"X-ACS-Token": "acs-secret"},
        )
        assert denied.status_code == 403
        assert denied.json()["error"]["rule"] == "PR-02"
    assert env.reservation(checkin_target).status == ReservationStatus.PENDING.value

    with pytest.raises(Forbidden) as denied_extend:
        _extend(env, "S1", extend_target, 30)
    assert denied_extend.value.rule == "PR-02"
    assert env.reservation(extend_target).status == ReservationStatus.IN_USE.value
    assert env.violations("S1") == []


# ---------------------------------------------------------------------------
# C-SEC-025 C+D 预约事件进入分析/导出
# ---------------------------------------------------------------------------
def test_c_sec_025_c_d_analytics_export(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    completed = _book(env, "S1", "R1-001", time(9, 0), time(10, 0))
    completed_code = make_credential(
        env, "S1", completed, now=datetime(2026, 5, 11, 8, 49, 30)
    )
    assert env.check_in("S1", completed, completed_code, now=EIGHT_FIFTY).ok
    assert env.check_out("S1", completed, now=datetime(2026, 5, 11, 9, 30)).ok

    no_show = _book(env, "S2", "R1-002", time(10, 0), time(10, 30))
    env.sweep(now=datetime(2026, 5, 11, 10, 16))
    assert env.reservation(no_show).status == ReservationStatus.NO_SHOW.value

    cancelled = _book(env, "S3", "R1-003", time(11, 0), time(12, 0))
    assert env.cancel("S3", cancelled, now=NOW).ok

    _book(env, "S5", "R1-004", time(13, 0), time(14, 0))
    assert env.join_waitlist("S1", "R1-004", time(13, 0), time(14, 0), now=NOW).ok

    with env.ctx.engine(write=False) as engine:
        analytics = engine.admin_analytics(
            env.actor("Y1"),
            date_from=D,
            date_to=D,
            now=datetime(2026, 5, 11, 14, 0),
        )
    with env.ctx.db.read() as conn:
        day_count = conn.execute(
            "SELECT COUNT(*) AS c FROM reservations WHERE date=?", (D.isoformat(),)
        ).fetchone()["c"]
    assert analytics["kpi"]["reservations"] == day_count
    assert analytics["kpi"]["completed"] == 1
    assert analytics["kpi"]["no_show"] == 1
    assert analytics["kpi"]["cancelled"] == 1
    assert analytics["waitlist"]["total"] == 1

    with api_client(env, monkeypatch, frozen=datetime(2026, 5, 11, 14, 0), username="Y1") as client:
        headers = {"X-CSRF-Token": client.cookies.get("seat_csrf") or ""}
        exported = client.get(
            "/api/admin/export/reservations.csv",
            headers=headers,
            params={"date_from": D.isoformat(), "date_to": D.isoformat()},
        )
        assert exported.status_code == 200, exported.text
        text = exported.text.lstrip("\ufeff")
        rows = list(csv.reader(io.StringIO(text)))
        assert rows[0][0] == "预约号"
        assert len(rows) - 1 == analytics["kpi"]["reservations"]
        assert "password" not in text.lower()
        assert {row[7] for row in rows[1:]} >= {"completed", "no_show", "cancelled"}
