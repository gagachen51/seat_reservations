"""定时任务与提醒。"""

from __future__ import annotations

from datetime import datetime, time

from seat_reservation.service.scheduler import Scheduler
from tests.helpers import D, Env, make_credential


def test_reminder_sent_once_per_reservation(env: Env) -> None:
    env.create("S1", "R1-001", time(9, 0), time(10, 0))
    with env.ctx.engine() as engine:
        assert engine.send_reminders(datetime(2026, 5, 11, 8, 44)) == 0
        assert engine.send_reminders(datetime(2026, 5, 11, 8, 45)) == 1
        assert engine.send_reminders(datetime(2026, 5, 11, 8, 46)) == 0
    assert any(item.notification_type == "checkin_reminder" for item in env.notifications("S1"))


def test_scheduler_run_once_is_idempotent(env: Env) -> None:
    item = env.create("S1", "R1-001", time(10, 0), time(10, 30)).data
    scheduler = Scheduler(env.ctx, interval_seconds=60)
    first = scheduler.run_once(datetime(2026, 5, 11, 10, 16))
    assert first["no_show"] == 1
    second = scheduler.run_once(datetime(2026, 5, 11, 10, 17))
    assert second["no_show"] == 0
    assert env.reservation(item["id"]).status == "no_show"
    status = scheduler.status()
    assert status["runs"] == 2
    assert status["last_run_at"] == "2026-05-11 10:17:00"
    assert status["last_error"] is None


def test_late_checkout_is_closed_by_sweep(env: Env) -> None:
    item = env.create("S1", "R1-001", time(9, 0), time(10, 0)).data
    code = make_credential(env, "S1", item["id"], now=datetime(2026, 5, 11, 8, 49, 30))
    assert env.check_in("S1", item["id"], code, now=datetime(2026, 5, 11, 8, 50)).ok
    summary = env.sweep(now=datetime(2026, 5, 11, 10, 1))
    assert summary["late_checkout"] == 1
    saved = env.reservation(item["id"])
    assert saved.status == "completed"
    assert saved.end_reason == "timeout_checkout"
    assert env.user("S1").credit == 95
    assert env.seat_state("R1-001", time(9, 0), time(10, 0)) == "available"


def test_check_out_before_end_grants_daily_capped_reward(env: Env) -> None:
    first = env.create("S1", "R1-001", time(9, 0), time(10, 0)).data
    code = make_credential(env, "S1", first["id"], now=datetime(2026, 5, 11, 8, 49, 30))
    assert env.check_in("S1", first["id"], code, now=datetime(2026, 5, 11, 8, 50)).ok
    assert env.check_out("S1", first["id"], now=datetime(2026, 5, 11, 9, 50)).ok
    assert env.user("S1").credit == 100

    second = env.create("S1", "R1-002", time(11, 0), time(12, 0)).data
    code2 = make_credential(env, "S1", second["id"], now=datetime(2026, 5, 11, 10, 49, 30))
    assert env.check_in("S1", second["id"], code2, now=datetime(2026, 5, 11, 10, 50)).ok
    assert env.check_out("S1", second["id"], now=datetime(2026, 5, 11, 11, 50)).ok
    # 每日加分上限 2，两次各 +1 不会超过 100 上限
    assert env.user("S1").credit == 100


def test_sweep_handles_three_overdue_kinds_independently(env: Env) -> None:
    pending = env.create("S1", "R1-001", time(10, 0), time(10, 30)).data
    away = env.create("S2", "R1-002", time(9, 0), time(11, 0)).data
    code = make_credential(env, "S2", away["id"], now=datetime(2026, 5, 11, 8, 49, 30))
    assert env.check_in("S2", away["id"], code, now=datetime(2026, 5, 11, 8, 50)).ok
    assert env.away("S2", away["id"], now=datetime(2026, 5, 11, 9, 0)).ok
    summary = env.sweep(now=datetime(2026, 5, 11, 10, 16))
    assert summary["no_show"] == 1
    assert summary["away_timeout"] == 1
    assert env.reservation(pending["id"]).status == "no_show"
    assert env.reservation(away["id"]).status == "released"
