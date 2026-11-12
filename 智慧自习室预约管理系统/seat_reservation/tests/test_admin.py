"""管理侧闭环：投诉、评价、现场违约、黑名单、导出与审计不可篡改。"""

from __future__ import annotations

import sqlite3
from datetime import datetime, time, timedelta

import pytest

from seat_reservation.domain.timeutil import datetime_at
from seat_reservation.errors import Forbidden, RuleViolation, ValidationFailed
from tests.helpers import D, Env, make_credential


def _complete(env: Env, user_id: str = "S1", seat_id: str = "R1-001", start_hour: int = 9) -> dict:
    item = env.create(user_id, seat_id, time(start_hour, 0), time(start_hour + 1, 0)).data
    start_at = datetime(2026, 5, 11, start_hour, 0)
    code = make_credential(
        env, user_id, item["id"], now=start_at - timedelta(minutes=10, seconds=30)
    )
    assert env.check_in(user_id, item["id"], code, now=start_at - timedelta(minutes=10)).ok
    assert env.check_out(user_id, item["id"], now=start_at + timedelta(minutes=50)).ok
    return item


def test_complaint_requires_feedback_before_closing(env: Env) -> None:
    with env.ctx.engine() as engine:
        submitted = engine.submit_complaint(
            env.actor("S1"), "facility", "插座没有电，无法使用", "R1-001", datetime(2026, 5, 11, 9, 0)
        )
        assert submitted.ok
        complaint_id = submitted.data["id"]
        with pytest.raises(ValidationFailed):
            engine.resolve_complaint(
                env.actor("A1"), complaint_id, "", datetime(2026, 5, 11, 9, 10)
            )
        resolved = engine.resolve_complaint(
            env.actor("A1"), complaint_id, "已更换插座面板", datetime(2026, 5, 11, 9, 10)
        )
        assert resolved.ok
        rated = engine.rate_complaint(
            env.actor("S1"), complaint_id, 5, "处理及时", datetime(2026, 5, 11, 9, 20)
        )
        assert rated.ok
        with pytest.raises(RuleViolation):
            engine.rate_complaint(
                env.actor("S1"), complaint_id, 4, "重复评价", datetime(2026, 5, 11, 9, 21)
            )


def test_complaint_can_trigger_linked_manual_violation(env: Env) -> None:
    item = env.create("S3", "R1-003", time(9, 0), time(10, 0)).data
    with env.ctx.engine() as engine:
        submitted = engine.submit_complaint(
            env.actor("S1"), "occupied_seat", "该座位有人长期占座", "R1-003", datetime(2026, 5, 11, 9, 10)
        )
        result = engine.resolve_complaint(
            env.actor("A1"),
            submitted.data["id"],
            "现场核查属实，已清场",
            datetime(2026, 5, 11, 9, 20),
            linked_reservation_id=item["id"],
            linked_mode="release",
        )
    assert result.ok
    assert env.reservation(item["id"]).status == "released"
    assert any(v.violation_type == "manual_inspection" for v in env.violations("S3"))


def test_review_once_and_admin_reply(env: Env) -> None:
    item = _complete(env)
    with env.ctx.engine() as engine:
        review = engine.submit_review(
            env.actor("S1"), item["id"], 5, ["安静", "插座稳定"], "环境很好", datetime(2026, 5, 11, 10, 5)
        )
        assert review.ok
        with pytest.raises(RuleViolation):
            engine.submit_review(
                env.actor("S1"), item["id"], 4, [], "重复", datetime(2026, 5, 11, 10, 6)
            )
        replied = engine.reply_review(
            env.actor("A1"), review.data["id"], "感谢反馈", datetime(2026, 5, 11, 10, 10)
        )
        assert replied.ok
        assert replied.data["review"]["reply"] == "感谢反馈"


def test_low_rating_can_block_seat(env: Env) -> None:
    item = _complete(env, "S2", "R1-002")
    with env.ctx.engine() as engine:
        review = engine.submit_review(
            env.actor("S2"), item["id"], 2, ["灯光偏暗"], "太暗了", datetime(2026, 5, 11, 10, 5)
        )
        replied = engine.reply_review(
            env.actor("A1"),
            review.data["id"],
            "已安排检修",
            datetime(2026, 5, 11, 10, 10),
            block_seat=True,
        )
    assert replied.ok
    assert env.seat_state("R1-002", time(12, 0), time(13, 0)) == "maintenance"


def test_manual_violation_warn_and_release_modes(env: Env) -> None:
    warn = env.create("S1", "R1-001", time(9, 0), time(10, 0)).data
    release = env.create("S2", "R1-002", time(9, 0), time(10, 0)).data
    with env.ctx.engine() as engine:
        result = engine.manual_violation(
            env.actor("A1"), warn["id"], "warn", "现场核实：桌面留有杂物", datetime(2026, 5, 11, 8, 30)
        )
        assert result.ok
        released = engine.manual_violation(
            env.actor("A1"), release["id"], "release", "现场核实：长时间无人", datetime(2026, 5, 11, 8, 31)
        )
        assert released.ok

    assert env.reservation(warn["id"]).status == "pending"
    assert env.reservation(release["id"]).status == "released"
    assert any(v.source == "manual" for v in env.violations("S1"))
    with env.ctx.engine() as engine:
        with pytest.raises(Forbidden) as denied:
            engine.manual_violation(
                env.actor("Y1"), warn["id"], "warn", "越权登记", datetime(2026, 5, 11, 8, 32)
            )
    assert denied.value.rule == "PR-17"


def test_manual_blacklist_blocks_then_release_restores(env: Env) -> None:
    with env.ctx.engine() as engine:
        blocked = engine.manual_blacklist(
            env.actor("A1"), "S2", "恶意占座，屡劝不改", None, datetime(2026, 5, 11, 9, 0)
        )
    assert blocked.ok
    with pytest.raises(RuleViolation) as denied:
        env.create("S2", "R1-005", time(16, 0), time(17, 0), now=datetime(2026, 5, 11, 9, 5))
    assert denied.value.rule == "BR-20"

    with env.ctx.engine() as engine:
        released = engine.release_blacklist(
            env.actor("A1"), "S2", "申诉复核，解除限制", datetime(2026, 5, 11, 9, 10)
        )
    assert released.ok
    assert env.create("S2", "R1-005", time(16, 0), time(17, 0), now=datetime(2026, 5, 11, 9, 15)).ok


def test_three_violations_trigger_auto_blacklist(env: Env) -> None:
    days = [D.replace(day=D.day + offset) for offset in (1, 2, 3)]
    for day in days:
        result = env.create(
            "S3",
            "R1-004",
            time(9, 0),
            time(9, 30),
            day=day,
            now=datetime.combine(day, time(8, 0)),
        )
        assert result.ok, result.message
        env.sweep(now=datetime.combine(day, time(9, 46)))

    user = env.user("S3")
    assert user.limit_type == "blacklist"
    assert user.credit == 70
    with pytest.raises(RuleViolation) as denied:
        env.create(
            "S3",
            "R1-004",
            time(9, 0),
            time(9, 30),
            day=D.replace(day=D.day + 4),
            now=datetime.combine(D.replace(day=D.day + 4), time(8, 0)),
        )
    assert denied.value.rule == "BR-20"
    with env.ctx.db.read() as conn:
        records = conn.execute("SELECT COUNT(*) AS c FROM blacklist WHERE user_id='S3'").fetchone()["c"]
    assert records == 1


def test_audit_log_is_append_only(env: Env) -> None:
    with env.ctx.db.transaction() as conn:
        conn.execute(
            "INSERT INTO audit_logs(id, actor_id, actor_role, action, target_type, target_id, ip, created_at) "
            "VALUES ('A-TEST', 'X', 'system', 'test', 'db', '1', '', '2026-05-11 08:00:00')"
        )
    with env.ctx.db.transaction() as conn:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("UPDATE audit_logs SET action='tampered' WHERE id='A-TEST'")
    with env.ctx.db.transaction() as conn:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("DELETE FROM audit_logs WHERE id='A-TEST'")


def test_export_permissions_and_content(env: Env) -> None:
    env.create("S1", "R1-001", time(9, 0), time(10, 0))
    with env.ctx.engine(write=False) as engine:
        # PR-16：数据导出只允许系统管理员，自习室管理员也不行。
        with pytest.raises(Forbidden) as denied:
            engine.export_rows(
                env.actor("A1"), "reservations", date_from=None, date_to=None
            )
        assert denied.value.rule == "PR-16"
        headers, rows = engine.export_rows(
            env.actor("Y1"), "reservations", date_from=None, date_to=None
        )
        assert headers[0] == "预约号"
        assert len(rows) == 2  # 种子历史 1 条 + 新建 1 条
        audit_headers, audit_rows = engine.export_rows(
            env.actor("Y1"), "audit", date_from=None, date_to=None
        )
        assert audit_headers[0] == "日志号"
        assert audit_rows == []


def test_room_admin_cannot_change_credit(env: Env) -> None:
    with env.ctx.engine() as engine:
        with pytest.raises(Forbidden) as denied:
            engine.adjust_credit(env.actor("A1"), "S1", -10, datetime(2026, 5, 11, 9, 0))
    assert denied.value.rule == "PR-08"
