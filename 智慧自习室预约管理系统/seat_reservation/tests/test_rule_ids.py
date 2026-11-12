"""规则编号级回归测试。

BR-02 / BR-04 / BR-07 / BR-11 / BR-20 已由 ``test_rules.py``、``test_acceptance.py``
断言过编号；这里补 BR-08、BR-17、BR-18、BR-21 —— 目的是让"校验被绕过或编号被改错"
这类改动能被测试抓到，而不是只在文档里对不上。
"""

from __future__ import annotations

from datetime import datetime, time

import pytest

from seat_reservation.errors import RuleViolation, ValidationFailed
from tests.helpers import NOW, Env


def test_br08_waitlist_rejected_when_seat_is_bookable(env: Env) -> None:
    """该时段座位仍可预约时不允许加入候补（BR-08）。"""
    with pytest.raises(RuleViolation) as exc:
        env.join_waitlist("S1", "R1-002", time(9, 0), time(10, 0))
    assert exc.value.rule == "BR-08"


def test_br17_appeal_has_time_window(env: Env) -> None:
    """超过申诉时限（appeal.window_days）后不能再申诉（BR-17）。"""
    with pytest.raises(RuleViolation):
        env.create("S4", "R1-005", time(16, 0), time(17, 0))
    violation = next(v for v in env.violations("S4") if v.violation_type == "no_show")

    too_late = datetime(2026, 5, 30, 9, 0)
    with pytest.raises(RuleViolation) as exc:
        env.appeal("S4", violation.id, "扫码设备故障", now=too_late)
    assert exc.value.rule == "BR-17"


def test_br18_manual_violation_requires_note(env: Env) -> None:
    """现场巡检与手动违约登记必须填核实说明（BR-18）。"""
    with env.ctx.engine() as engine:
        with pytest.raises(ValidationFailed) as exc:
            engine.manual_violation(env.actor("A1"), "RS-000001", "warn", "太短", NOW)
    assert "核实说明" in str(exc.value)


def test_br21_review_requires_completed_reservation(env: Env) -> None:
    """只有已完成的预约才能评价（BR-21）。"""
    reservation_id = env.create("S1", "R1-002", time(9, 0), time(10, 0)).data["id"]
    with env.ctx.engine() as engine:
        with pytest.raises(RuleViolation) as exc:
            engine.submit_review(env.actor("S1"), reservation_id, 5, [], "环境不错", NOW)
    assert exc.value.rule == "BR-21"


def test_br21_review_cannot_duplicate(env: Env) -> None:
    """同一预约不能重复评价（BR-21）。"""
    reservation_id = env.create("S1", "R1-002", time(9, 0), time(10, 0)).data["id"]
    # 直接把预约置为已完成，跳过签到签退流程
    with env.ctx.db.transaction() as conn:
        conn.execute(
            "UPDATE reservations SET status='completed' WHERE id=?", (reservation_id,)
        )

    with env.ctx.engine() as engine:
        first = engine.submit_review(env.actor("S1"), reservation_id, 5, [], "环境不错", NOW)
        assert first.ok, first.message
        with pytest.raises(RuleViolation) as exc:
            engine.submit_review(env.actor("S1"), reservation_id, 4, [], "再评一次", NOW)
    assert exc.value.rule == "BR-21"
