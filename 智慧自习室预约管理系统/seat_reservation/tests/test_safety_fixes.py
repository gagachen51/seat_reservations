"""候补/申诉/资源管理修复的回归测试。"""

from __future__ import annotations

from datetime import datetime, time

import pytest

from seat_reservation.errors import Conflict, Forbidden, RuleViolation, ValidationFailed
from seat_reservation.storage.repository import SqliteRepository
from tests.helpers import NOW, Env


def _waitlist_entry(env: Env, user_id: str) -> dict:
    with env.ctx.db.read() as conn:
        row = conn.execute(
            "SELECT * FROM waitlist WHERE user_id = ? ORDER BY created_at DESC LIMIT 1",
            (user_id,),
        ).fetchone()
        return dict(row) if row else {}


def _record_no_show(env: Env, user_id: str, seat_id: str, credit: int) -> str:
    created = env.create(
        user_id, seat_id, time(9, 0), time(10, 0), now=datetime(2026, 5, 11, 8, 0)
    )
    reservation_id = created.data["id"]
    with env.ctx.db.transaction() as conn:
        repo = SqliteRepository(conn)
        user = repo.get_user(user_id)
        user.credit = credit
        user.limit_type = "none"
        user.limit_until = None
        repo.save_user(user)
    with env.ctx.engine() as engine:
        violation = engine._record_violation(
            env.reservation(reservation_id), "no_show", NOW, note="回归测试"
        )
    return violation.id


def test_waitlist_started_slot_is_not_offered(env: Env) -> None:
    created = env.create("S1", "R1-001", time(9, 0), time(10, 0), now=datetime(2026, 5, 11, 8, 0))
    env.join_waitlist("S2", "R1-001", time(9, 0), time(10, 0), now=datetime(2026, 5, 11, 8, 50))
    env.force_release("A1", created.data["id"], "临时释放", now=datetime(2026, 5, 11, 8, 59))

    entry = _waitlist_entry(env, "S2")
    assert entry["status"] == "notified"
    result = env.confirm_waitlist("S2", entry["id"], now=datetime(2026, 5, 11, 9, 1))
    assert result.ok is False
    assert _waitlist_entry(env, "S2")["status"] == "expired"


def test_waitlist_cannot_confirm_blocked_seat(env: Env) -> None:
    created = env.create("S1", "R1-002", time(9, 0), time(10, 0), now=datetime(2026, 5, 11, 8, 0))
    env.join_waitlist("S2", "R1-002", time(9, 0), time(10, 0), now=datetime(2026, 5, 11, 8, 5))
    env.cancel("S1", created.data["id"], now=datetime(2026, 5, 11, 8, 10))

    entry = _waitlist_entry(env, "S2")
    assert entry["status"] == "notified"
    env.block_seat(
        "A1",
        "R1-002",
        "临时维修",
        datetime(2026, 5, 11, 8, 30),
        datetime(2026, 5, 11, 10, 30),
        now=datetime(2026, 5, 11, 8, 11),
    )
    result = env.confirm_waitlist("S2", entry["id"], now=datetime(2026, 5, 11, 8, 12))
    assert result.ok is False
    assert "不可用" in result.message or "维护" in result.message
    assert _waitlist_entry(env, "S2")["status"] == "expired"


def test_waitlist_conflict_expires_and_commits(env: Env) -> None:
    created = env.create("S1", "R1-003", time(9, 0), time(10, 0), now=datetime(2026, 5, 11, 8, 0))
    env.join_waitlist("S2", "R1-003", time(9, 0), time(10, 0), now=datetime(2026, 5, 11, 8, 5))
    env.cancel("S1", created.data["id"], now=datetime(2026, 5, 11, 8, 10))

    entry = _waitlist_entry(env, "S2")
    assert entry["status"] == "notified"
    env.create("S2", "R1-004", time(9, 0), time(10, 0), now=datetime(2026, 5, 11, 8, 15))
    result = env.confirm_waitlist("S2", entry["id"], now=datetime(2026, 5, 11, 8, 16))
    assert result.ok is False
    # 关键：异常不能把 EXPIRED 回滚掉；冲突也必须真正落库。
    assert _waitlist_entry(env, "S2")["status"] == "expired"


def test_appeal_restores_only_actual_deduction(env: Env) -> None:
    violation_id = _record_no_show(env, "S5", "R1-005", credit=5)
    assert env.user("S5").credit == 0  # 5 分只够扣 5 分

    assert env.appeal("S5", violation_id, "扫码设备故障").ok
    assert env.review_appeal("A1", violation_id, "approve").ok
    assert env.user("S5").credit == 5  # 不能恢复到 10


def test_appeal_does_not_inflate_when_credit_was_zero(env: Env) -> None:
    violation_id = _record_no_show(env, "S5", "R1-006", credit=0)
    assert env.user("S5").credit == 0

    assert env.appeal("S5", violation_id, "扫码设备故障").ok
    assert env.review_appeal("A1", violation_id, "approve").ok
    assert env.user("S5").credit == 0


def test_appeal_resets_consecutive_no_show(env: Env) -> None:
    created = env.create("S3", "R1-005", time(9, 0), time(10, 0), now=datetime(2026, 5, 11, 8, 0))
    with env.ctx.db.transaction() as conn:
        repo = SqliteRepository(conn)
        user = repo.get_user("S3")
        user.credit = 100
        user.consecutive_no_show = 2
        user.limit_type = "none"
        user.limit_until = None
        repo.save_user(user)
    with env.ctx.engine() as engine:
        violation = engine._record_violation(
            env.reservation(created.data["id"]), "no_show", NOW, note="回归测试"
        )
    assert env.user("S3").consecutive_no_show == 3

    assert env.appeal("S3", violation.id, "设备故障").ok
    assert env.review_appeal("A1", violation.id, "approve").ok
    user = env.user("S3")
    assert user.consecutive_no_show == 2
    assert user.limit_type == "none"


def test_violation_keyword_search(env: Env) -> None:
    violation_id = _record_no_show(env, "S5", "R1-005", credit=100)
    with env.ctx.engine(write=False) as engine:
        page = engine.list_violations(env.actor("A1"), keyword="S5", page_size=100)
    assert any(item.id == violation_id for item in page.items)


def test_room_and_seat_crud_and_status(env: Env) -> None:
    with env.ctx.engine() as engine:
        room = engine.create_room(
            env.actor("A1"),
            "D401",
            "第四教学楼 D401",
            "四楼",
            time(8, 0),
            time(22, 0),
            "open",
            NOW,
            ip="10.0.0.8",
        )
        assert room.ok
        seat = engine.create_seat(
            env.actor("A1"),
            "D401-001",
            "D401",
            "standard",
            1,
            True,
            False,
            "",
            NOW,
            ip="10.0.0.8",
        )
        assert seat.ok
        with pytest.raises(Conflict):
            engine.create_seat(
                env.actor("A1"), "D401-001", "D401", "standard", 1, False, False, "", NOW
            )
        with pytest.raises(ValidationFailed):
            engine.create_seat(
                env.actor("A1"), "D401-D01", "D401", "discussion", 3, False, False, "", NOW
            )
        updated = engine.update_seat(
            env.actor("A1"),
            "D401-001",
            room_id=None,
            seat_type=None,
            capacity=None,
            admin_status="disabled",
            has_power=None,
            is_window=None,
            note=None,
            now=NOW,
        )
        assert updated.data["admin_status"] == "disabled"
        with pytest.raises(Forbidden) as denied:
            engine.create_room(
                env.actor("S1"), "D402", "无权限", "", time(8, 0), time(22, 0), "open", NOW
            )
        assert denied.value.rule == "PR-05"

    with pytest.raises(RuleViolation) as denied_booking:
        env.create("S1", "D401-001", time(9, 0), time(10, 0), now=datetime(2026, 5, 11, 8, 0))
    assert denied_booking.value.rule == "BR-09"

    audit = next(item for item in env.audit() if item.action == "room_create")
    assert audit.ip == "10.0.0.8"


def test_closed_room_rejects_new_booking(env: Env) -> None:
    with env.ctx.engine() as engine:
        engine.create_room(
            env.actor("A1"), "E501", "E501", "", time(8, 0), time(22, 0), "closed", NOW
        )
        engine.create_seat(
            env.actor("A1"), "E501-001", "E501", "standard", 1, False, False, "", NOW
        )
    with pytest.raises(RuleViolation) as denied:
        env.create("S1", "E501-001", time(9, 0), time(10, 0), now=datetime(2026, 5, 11, 8, 0))
    assert denied.value.rule == "FR-21"
