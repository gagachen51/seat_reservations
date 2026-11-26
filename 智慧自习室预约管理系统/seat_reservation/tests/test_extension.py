"""续约（延长时段）的规则、引擎、事务与 HTTP 契约测试。"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, time

import pytest
from fastapi.testclient import TestClient

from seat_reservation.api.app import create_app
from seat_reservation.config import Settings
from seat_reservation.domain.models import ReservationStatus
from seat_reservation.domain.reservation_rules import (
    EXTENSION_STEP_MINUTES,
    extended_end_slot,
    extension_bounds_error,
    extension_minutes_error,
    extension_quota_error,
    extension_state_error,
    max_extension_minutes,
)
from seat_reservation.errors import Conflict, Forbidden, RuleViolation, ValidationFailed
from seat_reservation.storage.repository import SqliteRepository
from tests.helpers import NOW, PASSWORD, Env

NINE_THIRTY = datetime(2026, 5, 11, 9, 30, 0)


# ---------------------------------------------------------------------------
# 夹具
# ---------------------------------------------------------------------------
def _make_reservation(
    env: Env,
    *,
    user_id: str = "S1",
    seat_id: str = "R1-001",
    start: time = time(9, 0),
    end: time = time(10, 0),
    status: str = ReservationStatus.IN_USE.value,
    now: datetime = NOW,
) -> str:
    created = env.create(user_id, seat_id, start, end, now=now)
    assert created.ok, created.message
    reservation_id = created.data["id"]
    with env.ctx.db.transaction() as conn:
        conn.execute(
            "UPDATE reservations SET status=? WHERE id=?", (status, reservation_id)
        )
    return reservation_id


def _extend(env: Env, reservation_id: str, extra_minutes: int, *, user_id: str = "S1", now: datetime = NINE_THIRTY):
    with env.ctx.engine() as engine:
        return engine.extend_reservation(
            env.actor(user_id), reservation_id, extra_minutes, now
        )


def _slots(env: Env, reservation_id: str) -> list[int]:
    with env.ctx.db.read() as conn:
        rows = conn.execute(
            "SELECT slot FROM reservation_slots WHERE reservation_id=? ORDER BY slot",
            (reservation_id,),
        ).fetchall()
    return [int(row["slot"]) for row in rows]


# ---------------------------------------------------------------------------
# 纯规则
# ---------------------------------------------------------------------------
def test_st12_only_in_use_or_away_can_extend() -> None:
    assert extension_state_error("in_use", NOW, datetime(2026, 5, 11, 10, 0)) is None
    assert extension_state_error("away", NOW, datetime(2026, 5, 11, 10, 0)) is None
    for status in ("pending", "completed", "cancelled", "no_show", "released"):
        error = extension_state_error(status, NOW, datetime(2026, 5, 11, 10, 0))
        assert error is not None and error[0] == "ST-12"


def test_st12_ended_reservation_cannot_extend() -> None:
    error = extension_state_error("in_use", datetime(2026, 5, 11, 10, 0), datetime(2026, 5, 11, 10, 0))
    assert error is not None and error[0] == "ST-12"


def test_br01_extension_must_be_30_minute_multiple() -> None:
    assert EXTENSION_STEP_MINUTES == 30
    assert extension_minutes_error(30) is None
    assert extension_minutes_error(60) is None
    for value in (0, -30, 15, 45, "30", None, True):
        assert extension_minutes_error(value) is not None


def test_br01_extension_bounds() -> None:
    # 09:00-10:00 + 60 分钟 = 3 小时，未超 4 小时且未过 23:00。
    assert extension_bounds_error(18, 22, 46, 240) is None
    over_close = extension_bounds_error(18, 48, 46, 240)
    assert over_close is not None and over_close[0] == "BR-01"
    over_max = extension_bounds_error(18, 30, 46, 240)
    assert over_max is not None and over_max[0] == "BR-01"


def test_br02_extension_daily_quota() -> None:
    assert extension_quota_error(120, 60, 240) is None
    error = extension_quota_error(200, 60, 240)
    assert error is not None and error[0] == "BR-02"


def test_extension_helpers() -> None:
    assert extended_end_slot(20, 30) == 21
    assert extended_end_slot(20, 90) == 23
    # 09:00-10:00 已用 60/240；关闭 23:00；当日剩余 60 分钟 → 最多 60。
    assert max_extension_minutes(18, 20, 46, 240, 180, 240) == 60
    assert max_extension_minutes(18, 20, 21, 240, 0, 240) == 30
    assert max_extension_minutes(18, 20, 46, 60, 0, 240) == 0


# ---------------------------------------------------------------------------
# 引擎：正常路径
# ---------------------------------------------------------------------------
def test_br01_in_use_reservation_can_extend_30(env: Env) -> None:
    reservation_id = _make_reservation(env)
    original = env.reservation(reservation_id)
    result = _extend(env, reservation_id, 30)

    assert result.ok, result.message
    assert result.data["end"] == "10:30"
    assert result.data["end_slot"] == 21
    assert result.data["minutes"] == 90
    updated = env.reservation(reservation_id)
    assert updated.end_slot == 21
    assert updated.end_at == datetime(2026, 5, 11, 10, 30)
    assert updated.version == original.version + 1
    assert _slots(env, reservation_id) == [18, 19, 20]


def test_br01_away_reservation_can_extend_60(env: Env) -> None:
    reservation_id = _make_reservation(env, status=ReservationStatus.AWAY.value)
    result = _extend(env, reservation_id, 60)

    assert result.ok, result.message
    updated = env.reservation(reservation_id)
    assert updated.status == ReservationStatus.AWAY.value
    assert updated.end_slot == 22
    assert _slots(env, reservation_id) == [18, 19, 20, 21]


def test_br01_extension_writes_audit_before_after(env: Env) -> None:
    reservation_id = _make_reservation(env)
    assert _extend(env, reservation_id, 30).ok

    logs = [log for log in env.audit() if log.action == "reservation_extend"]
    assert len(logs) == 1
    log = logs[0]
    assert log.target_type == "reservation"
    assert log.target_id == reservation_id
    assert log.before == {"end_slot": 20, "end_at": "2026-05-11 10:00:00"}
    assert log.after == {
        "end_slot": 21,
        "end_at": "2026-05-11 10:30:00",
        "extra_minutes": 30,
    }


def test_br01_multiple_sequential_extensions_append_slots(env: Env) -> None:
    reservation_id = _make_reservation(env)
    assert _extend(env, reservation_id, 30, now=datetime(2026, 5, 11, 9, 10)).ok
    assert _extend(env, reservation_id, 30, now=datetime(2026, 5, 11, 9, 20)).ok

    updated = env.reservation(reservation_id)
    assert updated.end_slot == 22
    assert updated.end_at == datetime(2026, 5, 11, 11, 0)
    assert updated.version == 3
    assert _slots(env, reservation_id) == [18, 19, 20, 21]


def test_br11_extension_to_adjacent_boundary_is_allowed(env: Env) -> None:
    reservation_id = _make_reservation(env)
    # 下一预约从 10:30 开始，延长到 10:30 是相邻而非重叠。
    assert env.create("S2", "R1-001", time(10, 30), time(11, 30), now=NOW).ok
    assert _extend(env, reservation_id, 30).ok

    with pytest.raises(Conflict) as exc:
        _extend(env, reservation_id, 30, now=datetime(2026, 5, 11, 9, 40))
    assert exc.value.rule == "BR-11"
    assert env.reservation(reservation_id).end_slot == 21


# ---------------------------------------------------------------------------
# 引擎：拒绝路径
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "status",
    [
        ReservationStatus.PENDING.value,
        ReservationStatus.COMPLETED.value,
        ReservationStatus.CANCELLED.value,
        ReservationStatus.NO_SHOW.value,
        ReservationStatus.RELEASED.value,
    ],
)
def test_st12_disallowed_status_cannot_extend(env: Env, status: str) -> None:
    reservation_id = _make_reservation(env, status=status)
    with pytest.raises(RuleViolation) as exc:
        _extend(env, reservation_id, 30)
    assert exc.value.rule == "ST-12"
    assert env.reservation(reservation_id).end_slot == 20


def test_pr03_only_owner_can_extend(env: Env) -> None:
    reservation_id = _make_reservation(env, user_id="S1")
    with pytest.raises(Forbidden) as exc:
        _extend(env, reservation_id, 30, user_id="S2")
    assert exc.value.rule == "PR-03"

    with pytest.raises(Forbidden) as teacher:
        _extend(env, reservation_id, 30, user_id="T1")
    assert teacher.value.rule == "PR-03"


@pytest.mark.parametrize("extra", [0, -30, 15, 45])
def test_br01_invalid_extension_minutes_rejected(env: Env, extra: int) -> None:
    reservation_id = _make_reservation(env)
    with pytest.raises(ValidationFailed):
        _extend(env, reservation_id, extra)
    assert env.reservation(reservation_id).end_slot == 20


def test_br01_extension_cannot_exceed_max_minutes(env: Env) -> None:
    reservation_id = _make_reservation(env, start=time(9, 0), end=time(12, 0))
    with pytest.raises(RuleViolation) as exc:
        _extend(env, reservation_id, 90)
    assert exc.value.rule == "BR-01"
    assert env.reservation(reservation_id).end_slot == 24


def test_br01_extension_cannot_exceed_room_close_slot(env: Env) -> None:
    with env.ctx.db.transaction() as conn:
        conn.execute("UPDATE rooms SET close_slot=22 WHERE id='R1'")
    reservation_id = _make_reservation(env, start=time(10, 0), end=time(10, 30))
    with pytest.raises(RuleViolation) as exc:
        _extend(env, reservation_id, 60)
    assert exc.value.rule == "BR-01"
    assert env.reservation(reservation_id).end_slot == 21


def test_br02_extension_cannot_break_daily_quota(env: Env) -> None:
    first = _make_reservation(env, start=time(9, 0), end=time(11, 0))
    assert env.create("S1", "R1-002", time(13, 0), time(15, 0), now=NOW).ok
    with pytest.raises(RuleViolation) as exc:
        _extend(env, first, 30)
    assert exc.value.rule == "BR-02"
    assert env.reservation(first).end_slot == 22


def test_br09_maintenance_seat_cannot_extend(env: Env) -> None:
    reservation_id = _make_reservation(env)
    with env.ctx.db.transaction() as conn:
        conn.execute("UPDATE seats SET admin_status='maintenance' WHERE id='R1-001'")
    with pytest.raises(RuleViolation) as exc:
        _extend(env, reservation_id, 30)
    assert exc.value.rule == "BR-09"


def test_br09_closed_room_cannot_extend(env: Env) -> None:
    reservation_id = _make_reservation(env)
    with env.ctx.db.transaction() as conn:
        conn.execute("UPDATE rooms SET status='closed' WHERE id='R1'")
    with pytest.raises(RuleViolation) as exc:
        _extend(env, reservation_id, 30)
    assert exc.value.rule == "BR-09"


def test_br09_blocked_extension_window_rejected(env: Env) -> None:
    reservation_id = _make_reservation(env)
    env.block_seat(
        "A1",
        "R1-001",
        "延长时段临时维修",
        datetime(2026, 5, 11, 10, 0),
        datetime(2026, 5, 11, 10, 30),
        now=datetime(2026, 5, 11, 9, 0),
    )
    with pytest.raises(RuleViolation) as exc:
        _extend(env, reservation_id, 30)
    assert exc.value.rule == "BR-09"
    assert env.reservation(reservation_id).end_slot == 20


def test_br11_seat_conflict_rejects_and_leaves_no_partial_slots(env: Env) -> None:
    reservation_id = _make_reservation(env)
    assert env.create("S2", "R1-001", time(10, 0), time(11, 0), now=NOW).ok
    before_slots = _slots(env, reservation_id)
    with pytest.raises(Conflict) as exc:
        _extend(env, reservation_id, 30)
    assert exc.value.rule == "BR-11"
    assert env.reservation(reservation_id).end_slot == 20
    assert _slots(env, reservation_id) == before_slots


def test_br11_user_conflict_rejects(env: Env) -> None:
    reservation_id = _make_reservation(env)
    assert env.create("S1", "R1-003", time(10, 0), time(11, 0), now=NOW).ok
    with pytest.raises(Conflict) as exc:
        _extend(env, reservation_id, 30)
    assert exc.value.rule == "BR-11"


# ---------------------------------------------------------------------------
# 事务与并发
# ---------------------------------------------------------------------------
def test_extension_version_conflict_rolls_back_extension_slots(env: Env) -> None:
    reservation_id = _make_reservation(env)
    assert _extend(env, reservation_id, 30).ok

    with pytest.raises(RuntimeError):
        with env.ctx.db.transaction() as conn:
            repo = SqliteRepository(conn)
            stale = repo.get_reservation(reservation_id)
            assert stale is not None
            # 模拟另一请求持有旧版本：先占新增时段，再按过期版本更新。
            repo.occupy_extension(stale, stale.end_slot, stale.end_slot + 1)
            assert repo.extend_reservation(
                stale, stale.end_slot + 1, stale.end_at, stale.version - 1
            ) is False
            raise RuntimeError("rollback")

    # 版本检查失败必须整体回滚，不能留下半截时段。
    assert _slots(env, reservation_id) == [18, 19, 20]
    assert env.reservation(reservation_id).end_slot == 21


def test_concurrent_extension_only_one_wins(env: Env) -> None:
    reservation_id = _make_reservation(env, start=time(9, 0), end=time(11, 0))

    def extend_once() -> str:
        context = env.ctx.__class__(env.ctx.settings)
        try:
            with context.engine() as engine:
                result = engine.extend_reservation(
                    env.actor("S1"), reservation_id, 120, NINE_THIRTY
                )
            return "ok" if result.ok else "rejected"
        except Exception:  # noqa: BLE001 - 并发失败统一记 rejected
            return "rejected"

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(lambda _: extend_once(), range(2)))

    assert outcomes.count("ok") == 1, outcomes
    assert _slots(env, reservation_id) == list(range(18, 26))
    assert env.reservation(reservation_id).end_slot == 26


# ---------------------------------------------------------------------------
# HTTP 契约
# ---------------------------------------------------------------------------
@pytest.fixture
def app(ctx):
    return create_app(Settings.for_tests(ctx.settings.db_path))


@pytest.fixture
def client(app, monkeypatch):
    import seat_reservation.api.routes.student_routes as student_routes

    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):  # noqa: ANN001
            return NINE_THIRTY

    monkeypatch.setattr(student_routes, "datetime", FrozenDatetime)
    with TestClient(app) as test_client:
        yield test_client


def login(client: TestClient, username: str = "S1") -> dict[str, str]:
    response = client.post(
        "/api/auth/login", json={"username": username, "password": PASSWORD}
    )
    assert response.status_code == 200, response.text
    return {"X-CSRF-Token": client.cookies.get("seat_csrf") or ""}


def test_post_extend_returns_updated_reservation(client: TestClient, env: Env) -> None:
    reservation_id = _make_reservation(env)
    headers = login(client)
    response = client.post(
        f"/api/reservations/{reservation_id}/extend",
        headers=headers,
        json={"extra_minutes": 30},
    )
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["end"] == "10:30"
    assert data["end_slot"] == 21
    assert data["minutes"] == 90
    assert env.reservation(reservation_id).end_slot == 21


def test_post_extend_requires_login(client: TestClient, env: Env) -> None:
    reservation_id = _make_reservation(env)
    client.cookies.clear()
    client.cookies.set("seat_csrf", "test-csrf")
    response = client.post(
        f"/api/reservations/{reservation_id}/extend",
        headers={"X-CSRF-Token": "test-csrf"},
        json={"extra_minutes": 30},
    )
    assert response.status_code == 401


def test_post_extend_rejects_other_user(client: TestClient, app, env: Env) -> None:
    reservation_id = _make_reservation(env, user_id="S1")
    with TestClient(app) as other:
        headers = login(other, "S2")
        response = other.post(
            f"/api/reservations/{reservation_id}/extend",
            headers=headers,
            json={"extra_minutes": 30},
        )
    assert response.status_code == 403
    assert response.json()["error"]["rule"] == "PR-03"


def test_post_extend_validation_and_rule_errors(client: TestClient, env: Env) -> None:
    headers = login(client)
    reservation_id = _make_reservation(env)

    invalid = client.post(
        f"/api/reservations/{reservation_id}/extend",
        headers=headers,
        json={"extra_minutes": 45},
    )
    assert invalid.status_code == 422
    assert invalid.json()["error"]["code"] == "VALIDATION_ERROR"

    pending = _make_reservation(
        env,
        user_id="S1",
        seat_id="R1-002",
        start=time(14, 0),
        end=time(15, 0),
        status=ReservationStatus.PENDING.value,
    )
    rejected = client.post(
        f"/api/reservations/{pending}/extend",
        headers=headers,
        json={"extra_minutes": 30},
    )
    assert rejected.status_code == 400
    assert rejected.json()["error"]["rule"] == "ST-12"

    missing = client.post(
        "/api/reservations/RS-NOT-EXIST/extend",
        headers=headers,
        json={"extra_minutes": 30},
    )
    assert missing.status_code == 404
    assert set(missing.json()["error"]) >= {"code", "message"}


def test_post_extend_conflict_is_409(client: TestClient, env: Env) -> None:
    reservation_id = _make_reservation(env)
    assert env.create("S2", "R1-001", time(10, 0), time(11, 0), now=NOW).ok
    headers = login(client)
    response = client.post(
        f"/api/reservations/{reservation_id}/extend",
        headers=headers,
        json={"extra_minutes": 30},
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "CONFLICT"
    assert response.json()["error"]["rule"] == "BR-11"
    assert env.reservation(reservation_id).end_slot == 20
