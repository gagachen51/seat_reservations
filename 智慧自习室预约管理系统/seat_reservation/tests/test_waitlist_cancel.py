"""候补取消的状态、权限、Q-05 口径与 HTTP 契约测试。"""

from __future__ import annotations

from datetime import datetime, time

import pytest
from fastapi.testclient import TestClient

from seat_reservation.api.app import create_app
from seat_reservation.config import Settings
from seat_reservation.domain.models import WaitlistStatus
from seat_reservation.errors import Forbidden, NotFound, RuleViolation
from tests.helpers import NOW, PASSWORD, Env

EIGHT_TEN = datetime(2026, 5, 11, 8, 10, 0)


# ---------------------------------------------------------------------------
# 辅助
# ---------------------------------------------------------------------------
def _hold_and_join(env: Env, *, user_id: str = "S2") -> str:
    held = env.create("S1", "R1-001", time(9, 0), time(10, 0), now=NOW)
    assert held.ok, held.message
    joined = env.join_waitlist(user_id, "R1-001", time(9, 0), time(10, 0), now=NOW)
    assert joined.ok, joined.message
    return joined.data["id"]


def _cancel(env: Env, entry_id: str, *, user_id: str = "S2", now: datetime = EIGHT_TEN):
    with env.ctx.engine() as engine:
        return engine.cancel_waitlist(env.actor(user_id), entry_id, now)


def _entry_row(env: Env, entry_id: str) -> dict:
    with env.ctx.db.read() as conn:
        row = conn.execute("SELECT * FROM waitlist WHERE id=?", (entry_id,)).fetchone()
    assert row is not None
    return dict(row)


# ---------------------------------------------------------------------------
# 引擎
# ---------------------------------------------------------------------------
def test_pr04_cancel_queued_waitlist(env: Env) -> None:
    entry_id = _hold_and_join(env)
    result = _cancel(env, entry_id)

    assert result.ok, result.message
    assert result.data["entry"]["status"] == WaitlistStatus.CANCELLED.value
    assert _entry_row(env, entry_id)["status"] == WaitlistStatus.CANCELLED.value


def test_pr04_cancel_does_not_affect_credit_or_violations(env: Env) -> None:
    entry_id = _hold_and_join(env)
    credit_before = env.user("S2").credit
    notifications_before = len(env.notifications("S2"))
    assert _cancel(env, entry_id).ok

    assert env.user("S2").credit == credit_before
    assert env.violations("S2") == []
    assert len(env.notifications("S2")) == notifications_before


def test_pr04_cancel_notified_does_not_promote_next(env: Env) -> None:
    """Q-05 确认前：取消已通知候补只改状态，不自动顺延下一位。"""
    assert env.set_param("Y1", "waitlist.notify_count", 1).ok
    held = env.create("S1", "R1-001", time(9, 0), time(10, 0), now=NOW)
    assert held.ok
    s2 = env.join_waitlist("S2", "R1-001", time(9, 0), time(10, 0), now=datetime(2026, 5, 11, 8, 5))
    s5 = env.join_waitlist("S5", "R1-001", time(9, 0), time(10, 0), now=datetime(2026, 5, 11, 8, 6))
    assert s2.ok and s5.ok
    assert env.cancel("S1", held.data["id"], now=datetime(2026, 5, 11, 8, 9)).ok

    assert _entry_row(env, s2.data["id"])["status"] == WaitlistStatus.NOTIFIED.value
    assert _entry_row(env, s5.data["id"])["status"] == WaitlistStatus.QUEUED.value
    notifications_before = len(env.notifications("S5"))

    result = _cancel(env, s2.data["id"], now=datetime(2026, 5, 11, 8, 12))
    assert result.ok, result.message
    assert _entry_row(env, s2.data["id"])["status"] == WaitlistStatus.CANCELLED.value
    # 关键：下一位仍排队，且没有收到新的补位通知。
    assert _entry_row(env, s5.data["id"])["status"] == WaitlistStatus.QUEUED.value
    assert len(env.notifications("S5")) == notifications_before


def test_pr04_cancel_only_changes_status(env: Env) -> None:
    assert env.set_param("Y1", "waitlist.notify_count", 1).ok
    held = env.create("S1", "R1-001", time(9, 0), time(10, 0), now=NOW)
    entry = env.join_waitlist("S2", "R1-001", time(9, 0), time(10, 0), now=NOW)
    assert held.ok and entry.ok
    assert env.cancel("S1", held.data["id"], now=EIGHT_TEN).ok
    before = _entry_row(env, entry.data["id"])

    assert _cancel(env, entry.data["id"], now=datetime(2026, 5, 11, 8, 12)).ok
    after = _entry_row(env, entry.data["id"])

    changed = {key for key, value in before.items() if after[key] != value}
    assert changed == {"status"}
    assert after["status"] == WaitlistStatus.CANCELLED.value


def test_pr04_cannot_cancel_other_users_waitlist(env: Env) -> None:
    entry_id = _hold_and_join(env, user_id="S2")
    with pytest.raises(Forbidden) as exc:
        _cancel(env, entry_id, user_id="S3")
    assert exc.value.rule == "PR-04"
    assert _entry_row(env, entry_id)["status"] == WaitlistStatus.QUEUED.value


@pytest.mark.parametrize("user_id", ["T1", "A1", "Y1"])
def test_pr04_non_student_cannot_cancel_waitlist(env: Env, user_id: str) -> None:
    entry_id = _hold_and_join(env, user_id="S2")
    with pytest.raises(Forbidden) as exc:
        _cancel(env, entry_id, user_id=user_id)
    assert exc.value.rule == "PR-04"


@pytest.mark.parametrize(
    "status",
    [
        WaitlistStatus.CONFIRMED.value,
        WaitlistStatus.EXPIRED.value,
        WaitlistStatus.CANCELLED.value,
    ],
)
def test_st12_waitlist_in_final_state_cannot_cancel(env: Env, status: str) -> None:
    entry_id = _hold_and_join(env)
    with env.ctx.db.transaction() as conn:
        conn.execute("UPDATE waitlist SET status=? WHERE id=?", (status, entry_id))
    with pytest.raises(RuleViolation) as exc:
        _cancel(env, entry_id)
    assert exc.value.rule == "ST-12"
    assert _entry_row(env, entry_id)["status"] == status


def test_pr04_missing_waitlist_is_not_found(env: Env) -> None:
    with pytest.raises(NotFound):
        _cancel(env, "W-NOT-EXIST")


# ---------------------------------------------------------------------------
# HTTP 契约
# ---------------------------------------------------------------------------
@pytest.fixture
def app(ctx):
    return create_app(Settings.for_tests(ctx.settings.db_path))


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


def test_post_cancel_waitlist_returns_entry(client: TestClient, env: Env) -> None:
    entry_id = _hold_and_join(env)
    headers = login(client, "S2")
    response = client.post(f"/api/waitlist/{entry_id}/cancel", headers=headers, json={})

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ok"] is True
    assert body["data"]["entry"]["id"] == entry_id
    assert body["data"]["entry"]["status"] == WaitlistStatus.CANCELLED.value
    assert _entry_row(env, entry_id)["status"] == WaitlistStatus.CANCELLED.value


def test_post_cancel_waitlist_requires_login(client: TestClient, env: Env) -> None:
    entry_id = _hold_and_join(env)
    client.cookies.clear()
    client.cookies.set("seat_csrf", "test-csrf")
    response = client.post(
        f"/api/waitlist/{entry_id}/cancel",
        headers={"X-CSRF-Token": "test-csrf"},
        json={},
    )
    assert response.status_code == 401


def test_post_cancel_waitlist_rejects_other_user(client: TestClient, app, env: Env) -> None:
    entry_id = _hold_and_join(env, user_id="S2")
    with TestClient(app) as other:
        headers = login(other, "S3")
        response = other.post(
            f"/api/waitlist/{entry_id}/cancel", headers=headers, json={}
        )
    assert response.status_code == 403
    assert response.json()["error"]["rule"] == "PR-04"


def test_post_cancel_waitlist_rejects_final_state(client: TestClient, env: Env) -> None:
    entry_id = _hold_and_join(env)
    with env.ctx.db.transaction() as conn:
        conn.execute("UPDATE waitlist SET status='confirmed' WHERE id=?", (entry_id,))
    headers = login(client, "S2")
    response = client.post(f"/api/waitlist/{entry_id}/cancel", headers=headers, json={})
    assert response.status_code == 400
    assert response.json()["error"]["rule"] == "ST-12"
    assert _entry_row(env, entry_id)["status"] == WaitlistStatus.CONFIRMED.value


def test_post_cancel_waitlist_missing_is_404(client: TestClient, env: Env) -> None:
    headers = login(client, "S2")
    response = client.post("/api/waitlist/W-NOT-EXIST/cancel", headers=headers, json={})
    assert response.status_code == 404
    assert set(response.json()["error"]) >= {"code", "message"}
