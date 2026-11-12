"""AC-01 ~ AC-15 验收测试（与 SRS 7.1 对应）。"""

from __future__ import annotations

from datetime import datetime, time

import pytest

from seat_reservation.domain.models import ReservationStatus
from seat_reservation.errors import Conflict, Forbidden, RuleViolation, ValidationFailed
from seat_reservation.domain.timeutil import datetime_at
from tests.helpers import D, NOW, Env, make_credential


def test_ac01_create_and_reject_second_booking_for_same_slot(env: Env) -> None:
    first = env.create("S1", "R1-001", time(9, 0), time(10, 0))
    assert first.ok, first.message
    assert first.data["status"] == ReservationStatus.PENDING.value
    assert env.seat_state("R1-001", time(9, 0), time(10, 0)) == "reserved"

    with pytest.raises(Conflict) as exc:
        env.create("S2", "R1-001", time(9, 0), time(10, 0))
    assert exc.value.rule == "BR-11"
    assert env.count_reservations() == 2  # 种子历史 1 条 + 本次 1 条


def test_ac02_check_in_inside_window(env: Env) -> None:
    item = env.create("S1", "R1-001", time(9, 0), time(10, 0)).data
    code = make_credential(env, "S1", item["id"], now=datetime(2026, 5, 11, 8, 49, 30))
    result = env.check_in("S1", item["id"], code, now=datetime(2026, 5, 11, 8, 50))
    assert result.ok, result.message
    saved = env.reservation(item["id"])
    assert saved.status == ReservationStatus.IN_USE.value
    assert saved.checked_in_at == datetime(2026, 5, 11, 8, 50)
    assert env.seat_state("R1-001", time(9, 0), time(10, 0)) == "occupied"


def test_ac03_no_show_penalty_and_seat_release(env: Env) -> None:
    item = env.create("S1", "R1-001", time(10, 0), time(10, 30)).data
    assert env.sweep(now=datetime(2026, 5, 11, 10, 15))["no_show"] == 0
    summary = env.sweep(now=datetime(2026, 5, 11, 10, 16))
    assert summary["no_show"] == 1
    assert env.reservation(item["id"]).status == ReservationStatus.NO_SHOW.value
    assert env.user("S1").credit == 90
    assert env.seat_state("R1-001", time(10, 0), time(10, 30)) == "available"
    assert any(v.violation_type == "no_show" for v in env.violations("S1"))
    assert any(n.notification_type == "violation" for n in env.notifications("S1"))


def test_ac04_away_resume_and_timeout(env: Env) -> None:
    first = env.create("S1", "R1-001", time(9, 0), time(10, 0)).data
    code = make_credential(env, "S1", first["id"], now=datetime(2026, 5, 11, 8, 49, 30))
    assert env.check_in("S1", first["id"], code, now=datetime(2026, 5, 11, 8, 50)).ok
    assert env.away("S1", first["id"], now=datetime(2026, 5, 11, 9, 10)).ok
    assert env.resume("S1", first["id"], now=datetime(2026, 5, 11, 9, 35)).ok
    assert env.reservation(first["id"]).status == ReservationStatus.IN_USE.value

    second = env.create("S2", "R1-002", time(9, 0), time(10, 0)).data
    code2 = make_credential(env, "S2", second["id"], now=datetime(2026, 5, 11, 8, 49, 30))
    assert env.check_in("S2", second["id"], code2, now=datetime(2026, 5, 11, 8, 50)).ok
    assert env.away("S2", second["id"], now=datetime(2026, 5, 11, 9, 20)).ok
    assert env.user("S2").credit == 100
    assert env.sweep(now=datetime(2026, 5, 11, 9, 51))["away_timeout"] == 1
    assert env.reservation(second["id"]).status == ReservationStatus.RELEASED.value
    assert env.user("S2").credit == 95


def test_ac05_daily_quota_blocks_third(env: Env) -> None:
    assert env.create("S1", "R1-001", time(9, 0), time(10, 0)).ok
    assert env.create("S1", "R1-002", time(11, 0), time(12, 0)).ok
    before = env.count_reservations()
    with pytest.raises(RuleViolation) as exc:
        env.create("S1", "R1-003", time(13, 0), time(14, 0))
    assert exc.value.rule == "BR-02"
    assert env.count_reservations() == before


def test_ac06_credit_limit_and_appeal_restore(env: Env) -> None:
    assert env.user("S4").credit == 55
    with pytest.raises(RuleViolation) as exc:
        env.create("S4", "R1-005", time(16, 0), time(17, 0))
    assert exc.value.rule == "BR-07"

    violation = next(v for v in env.violations("S4") if v.violation_type == "no_show")
    assert env.appeal("S4", violation.id, "扫码设备故障", now=datetime(2026, 5, 11, 8, 10)).ok
    reviewed = env.review_appeal(
        "A1", violation.id, "approve", "核查系统日志，确认设备异常", now=datetime(2026, 5, 11, 8, 20)
    )
    assert reviewed.ok, reviewed.message
    assert env.user("S4").credit == 65
    assert env.user("S4").limit_type == "none"
    assert env.create("S4", "R1-005", time(16, 0), time(17, 0), now=datetime(2026, 5, 11, 8, 30)).ok


def test_ac07_free_cancel_vs_late_cancel(env: Env) -> None:
    free = env.create("S1", "R1-001", time(10, 0), time(10, 30)).data
    late = env.create("S1", "R1-002", time(12, 0), time(12, 30)).data

    assert env.cancel("S1", free["id"], now=datetime(2026, 5, 11, 9, 20)).ok
    assert env.user("S1").credit == 100

    assert env.cancel("S1", late["id"], now=datetime(2026, 5, 11, 11, 50)).ok
    assert env.user("S1").credit == 95
    assert any(v.violation_type == "late_cancel" for v in env.violations("S1"))


def test_ac08_block_seat_cancels_without_violation(env: Env) -> None:
    item = env.create("S1", "R1-001", time(9, 0), time(10, 0)).data
    result = env.block_seat(
        "A1",
        "R1-001",
        "座椅损坏维修",
        datetime_at(D, 18),
        datetime_at(D, 20),
        now=datetime(2026, 5, 11, 8, 30),
    )
    assert result.ok, result.message
    assert item["id"] in result.data["affected_reservations"]
    saved = env.reservation(item["id"])
    assert saved.status == ReservationStatus.CANCELLED.value
    assert saved.end_reason == "seat_blocked"
    assert env.user("S1").credit == 100
    assert env.violations("S1") == []
    assert any(n.notification_type == "seat_blocked" for n in env.notifications("S1"))
    assert env.seat_state("R1-001", time(9, 0), time(10, 0)) == "maintenance"
    assert any(log.action == "seat_block" for log in env.audit())


def test_ac09_student_denied_and_no_side_effects(env: Env) -> None:
    target = env.create("S3", "R1-002", time(9, 0), time(10, 0)).data
    audit_before = len(env.audit())
    credit_before = env.user("S1").credit

    with pytest.raises(Forbidden) as block:
        env.block_seat("S1", "R1-001", "学生尝试", datetime_at(D, 18), datetime_at(D, 20))
    assert block.value.rule == "PR-06"

    with pytest.raises(Forbidden) as release:
        env.force_release("S1", target["id"], "学生尝试")
    assert release.value.rule == "PR-07"

    with env.ctx.engine() as engine:
        with pytest.raises(Forbidden) as credit:
            engine.adjust_credit(env.actor("S1"), "S2", -10, NOW)
        assert credit.value.rule == "PR-08"
        with pytest.raises(Forbidden) as read:
            engine.get_reservation(env.actor("S1"), target["id"])
        assert read.value.rule == "PR-11"

    assert env.reservation(target["id"]).status == ReservationStatus.PENDING.value
    assert env.user("S1").credit == credit_before
    assert len(env.audit()) == audit_before


def test_ac10_waitlist_confirm_within_five_minutes(env: Env) -> None:
    assert env.set_param("Y1", "waitlist.notify_count", 1).ok
    held = env.create("S1", "R1-001", time(10, 0), time(11, 0)).data
    assert env.join_waitlist("S2", "R1-001", time(10, 0), time(11, 0), now=datetime(2026, 5, 11, 8, 10)).ok
    assert env.join_waitlist("S5", "R1-001", time(10, 0), time(11, 0), now=datetime(2026, 5, 11, 8, 11)).ok
    assert env.cancel("S1", held["id"], now=datetime(2026, 5, 11, 8, 20)).ok

    with env.ctx.db.read() as conn:
        rows = conn.execute("SELECT * FROM waitlist WHERE user_id='S2'").fetchall()
    assert len(rows) == 1
    entry = rows[0]
    assert entry["status"] == "notified"
    assert entry["confirm_deadline"] == "2026-05-11 08:25:00"

    confirmed = env.confirm_waitlist("S2", entry["id"], now=datetime(2026, 5, 11, 8, 23))
    assert confirmed.ok, confirmed.message
    assert confirmed.data["source"] == "waitlist"
    assert any(n.notification_type == "waitlist_available" for n in env.notifications("S2"))


def test_ac10_waitlist_timeout_escalates(env: Env) -> None:
    assert env.set_param("Y1", "waitlist.notify_count", 1).ok
    held = env.create("S1", "R1-001", time(10, 0), time(11, 0)).data
    assert env.join_waitlist("S2", "R1-001", time(10, 0), time(11, 0), now=datetime(2026, 5, 11, 8, 10)).ok
    assert env.join_waitlist("S5", "R1-001", time(10, 0), time(11, 0), now=datetime(2026, 5, 11, 8, 11)).ok
    assert env.cancel("S1", held["id"], now=datetime(2026, 5, 11, 8, 20)).ok
    assert env.sweep(now=datetime(2026, 5, 11, 8, 26))["waitlist_expired"] == 1

    with env.ctx.db.read() as conn:
        entries = {
            row["user_id"]: dict(row)
            for row in conn.execute("SELECT * FROM waitlist").fetchall()
        }
    assert entries["S2"]["status"] == "expired"
    assert entries["S5"]["status"] == "notified"
    assert entries["S5"]["confirm_deadline"] == "2026-05-11 08:31:00"


def test_ac11_force_release_requires_reason_and_audits(env: Env) -> None:
    item = env.create("S1", "R1-001", time(9, 0), time(10, 0)).data
    before = len(env.audit())
    with pytest.raises(ValidationFailed):
        env.force_release("A1", item["id"], "")
    assert len(env.audit()) == before
    assert env.reservation(item["id"]).status == ReservationStatus.PENDING.value

    released = env.force_release("A1", item["id"], "现场核查无人使用", now=datetime(2026, 5, 11, 8, 31))
    assert released.ok, released.message
    assert env.reservation(item["id"]).status == ReservationStatus.RELEASED.value
    assert env.violations("S1") == []
    assert any(log.action == "force_release" and log.target_id == item["id"] for log in env.audit())


def test_ac12_appeal_permissions_and_restore(env: Env) -> None:
    violation = next(v for v in env.violations("S4") if v.violation_type == "no_show")
    assert env.appeal("S4", violation.id, "扫码设备故障", now=datetime(2026, 5, 11, 8, 10)).ok
    with pytest.raises(Forbidden) as other:
        env.appeal("S1", violation.id, "越权申诉", now=datetime(2026, 5, 11, 8, 11))
    assert other.value.rule == "PR-09"
    with pytest.raises(Forbidden) as reviewer:
        env.review_appeal("S1", violation.id, "approve", now=datetime(2026, 5, 11, 8, 12))
    assert reviewer.value.rule == "PR-10"

    reviewed = env.review_appeal(
        "A1", violation.id, "approve", "核查日志确认设备异常", now=datetime(2026, 5, 11, 8, 20)
    )
    assert reviewed.ok, reviewed.message
    saved = next(v for v in env.violations("S4") if v.id == violation.id)
    assert saved.status == "revoked"
    assert "设备异常" in (saved.review_note or "")
    assert env.user("S4").credit == 65
    assert any(log.action == "appeal_review" and log.target_id == violation.id for log in env.audit())
    assert any(n.notification_type == "appeal_result" for n in env.notifications("S4"))


def test_ac13_teacher_summary_scope(env: Env) -> None:
    with env.ctx.engine(write=False) as engine:
        teacher = engine.teacher_dashboard(env.actor("T1"), NOW)
        admin = engine.admin_stats(env.actor("A1"), None, NOW)
        with pytest.raises(Forbidden) as denied:
            engine.teacher_dashboard(env.actor("S1"), NOW)
        with pytest.raises(Forbidden) as teacher_list:
            engine.list_reservations(env.actor("T1"))
    assert denied.value.rule == "PR-12"
    assert teacher_list.value.rule == "PR-11"
    assert teacher["kpi"]["students"] == 3
    assert teacher["kpi"]["violations_30"] == 1
    assert "items" not in teacher
    assert admin["kpi"]["total_seats"] == 8


def test_ac14_param_change_only_affects_new_reservations(env: Env) -> None:
    old = env.create("S1", "R1-001", time(9, 0), time(10, 0)).data
    assert env.set_param("Y1", "checkin.before_minutes", 5, now=datetime(2026, 5, 11, 8, 5)).ok
    assert any(log.action == "param_change" and log.target_id == "checkin.before_minutes" for log in env.audit())

    old_code = make_credential(env, "S1", old["id"], now=datetime(2026, 5, 11, 8, 49, 30))
    old_check = env.check_in("S1", old["id"], old_code, now=datetime(2026, 5, 11, 8, 50))
    assert old_check.ok, "已有预约应继续使用创建时的 15 分钟窗口"

    new = env.create(
        "S2", "R1-002", time(10, 0), time(11, 0), now=datetime(2026, 5, 11, 8, 10)
    ).data
    with env.ctx.db.read() as conn:
        policy = conn.execute("SELECT policy_json FROM reservations WHERE id=?", (new["id"],)).fetchone()[0]
    assert '"checkin.before_minutes": "5"' in policy

    new_code = make_credential(env, "S2", new["id"], now=datetime(2026, 5, 11, 9, 49, 30))
    with pytest.raises(RuleViolation) as early:
        env.check_in("S2", new["id"], new_code, now=datetime(2026, 5, 11, 9, 50))
    assert early.value.rule == "BR-04"


def test_ac15_sweep_idempotent_and_boundary(env: Env) -> None:
    item = env.create("S1", "R1-001", time(10, 0), time(10, 30)).data
    assert env.sweep(now=datetime(2026, 5, 11, 10, 15))["no_show"] == 0
    assert env.sweep(now=datetime(2026, 5, 11, 10, 16))["no_show"] == 1
    assert env.reservation(item["id"]).status == ReservationStatus.NO_SHOW.value
    assert env.user("S1").credit == 90

    assert env.sweep(now=datetime(2026, 5, 11, 10, 17))["no_show"] == 0
    assert env.user("S1").credit == 90
    assert len([v for v in env.violations("S1") if v.reservation_id == item["id"]]) == 1


def test_persistence_survives_new_context(ctx) -> None:
    from seat_reservation.service.container import AppContext

    with ctx.engine() as engine:
        result = engine.create_reservation(
            _actor(ctx, "S1"), "R1-001", D, time(9, 0), time(10, 0), NOW
        )
    assert result.ok
    reopened = AppContext(ctx.settings)
    with reopened.engine(write=False) as engine:
        data = engine.get_reservation(_actor(reopened, "S1"), result.data["id"])
    assert data["id"] == result.data["id"]
    assert data["status"] == "pending"


def _actor(ctx, user_id: str):
    from seat_reservation.domain.models import Actor, Role
    from seat_reservation.storage.repository import SqliteRepository

    with ctx.db.read() as conn:
        user = SqliteRepository(conn).get_user(user_id)
    return Actor(id=user.id, name=user.name, role=Role(user.role))
