"""纯规则与参数单测。"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

import pytest

from seat_reservation.domain.params import ParamSet, check_consistency, validate_value
from seat_reservation.domain.rules import (
    cancel_phase,
    checkin_phase,
    checkin_window,
    clamp_credit,
    credential_valid,
    credit_blocks_booking,
    in_booking_window,
    quota_exceeded,
    reward_eligible,
    slots_overlap,
    validate_slot_range,
    within_open_hours,
)
from seat_reservation.domain.timeutil import parse_datetime, slot_of_time, time_of_slot

NOW = datetime(2026, 5, 11, 8, 0)


def test_slot_time_roundtrip() -> None:
    assert slot_of_time(time(7, 0)) == 14
    assert slot_of_time(time(23, 0)) == 46
    assert time_of_slot(18) == time(9, 0)
    with pytest.raises(ValueError):
        slot_of_time(time(9, 15))


def test_parse_datetime_accepts_iso_and_space() -> None:
    assert parse_datetime("2026-05-11 09:00") == datetime(2026, 5, 11, 9, 0)
    assert parse_datetime("2026-05-11T09:00:00") == datetime(2026, 5, 11, 9, 0)


def test_br01_duration_boundaries() -> None:
    assert validate_slot_range(18, 19, 30, 240)[0]
    assert not validate_slot_range(18, 19, 60, 240)[0]
    assert validate_slot_range(18, 26, 30, 480)[0]
    assert not validate_slot_range(18, 28, 30, 240)[0]
    assert not validate_slot_range(19, 18, 30, 240)[0]


def test_br01_open_hours() -> None:
    assert within_open_hours(14, 20, 14, 46)
    assert not within_open_hours(12, 20, 14, 46)
    assert not within_open_hours(40, 48, 14, 46)


def test_br03_booking_window_inclusive() -> None:
    today = date(2026, 5, 11)
    assert in_booking_window(today, today, 7)
    assert in_booking_window(today + timedelta(days=6), today, 7)
    assert not in_booking_window(today + timedelta(days=7), today, 7)
    assert not in_booking_window(today - timedelta(days=1), today, 7)


def test_br04_checkin_window_closed_interval() -> None:
    start = datetime(2026, 5, 11, 9, 0)
    opens, closes = checkin_window(start, 15, 15)
    assert opens == datetime(2026, 5, 11, 8, 45)
    assert closes == datetime(2026, 5, 11, 9, 15)
    assert checkin_phase(opens, start, 15, 15) == "inside"
    assert checkin_phase(closes, start, 15, 15) == "inside"
    assert checkin_phase(opens - timedelta(seconds=1), start, 15, 15) == "early"
    assert checkin_phase(closes + timedelta(seconds=1), start, 15, 15) == "late"


def test_br10_cancel_phase() -> None:
    start = datetime(2026, 5, 11, 9, 0)
    assert cancel_phase(start - timedelta(minutes=30), start, 30) == "free"
    assert cancel_phase(start - timedelta(minutes=29), start, 30) == "late"
    assert cancel_phase(start, start, 30) == "closed"


def test_br13_credential_one_time_and_ttl() -> None:
    issued = datetime(2026, 5, 11, 9, 0)
    ok, _ = credential_valid(issued, False, 60, issued + timedelta(seconds=60), "ABC", "ABC")
    assert ok
    assert not credential_valid(issued, True, 60, issued, "ABC", "ABC")[0]
    assert not credential_valid(issued, False, 60, issued + timedelta(seconds=61), "ABC", "ABC")[0]
    assert not credential_valid(issued, False, 60, issued, "ABC", "XYZ")[0]


def test_br06_credit_clamp_and_threshold() -> None:
    assert clamp_credit(105, 0, 100) == 100
    assert clamp_credit(-5, 0, 100) == 0
    assert credit_blocks_booking(59, 60)
    assert not credit_blocks_booking(60, 60)


def test_br02_quota() -> None:
    assert quota_exceeded(0, 0, 60, 2, 240) is None
    assert "个数" in (quota_exceeded(2, 0, 30, 2, 240) or "")
    assert "总时长" in (quota_exceeded(0, 200, 60, 2, 240) or "")


def test_br11_overlap_semantics() -> None:
    assert slots_overlap(18, 20, 19, 21)
    assert not slots_overlap(18, 20, 20, 22)


def test_reward_requires_on_time_checkin_and_checkout() -> None:
    start = datetime(2026, 5, 11, 9, 0)
    end = datetime(2026, 5, 11, 10, 0)
    assert reward_eligible(start - timedelta(minutes=5), start, end - timedelta(minutes=1), end)
    assert not reward_eligible(start + timedelta(minutes=16), start, end, end)
    assert not reward_eligible(start, start, end + timedelta(minutes=1), end)


def test_params_defaults_and_validation() -> None:
    params = ParamSet()
    assert params.int("booking.max_minutes") == 240
    assert validate_value("booking.window_days", "7") == "7"
    with pytest.raises(ValueError):
        validate_value("booking.window_days", "0")
    with pytest.raises(ValueError):
        validate_value("checkin.before_minutes", "abc")
    with pytest.raises(ValueError):
        validate_value("unknown.param", "1")


def test_params_cross_field_consistency() -> None:
    values = ParamSet().snapshot()
    values["booking.min_minutes"] = "240"
    values["booking.max_minutes"] = "30"
    assert check_consistency(values)
    values = ParamSet().snapshot()
    values["room.open_time"] = "23:00"
    values["room.close_time"] = "07:00"
    assert check_consistency(values)


def test_param_snapshot_is_isolated() -> None:
    original = ParamSet()
    snapshot = original.snapshot()
    changed = ParamSet({**snapshot, "checkin.before_minutes": "5"})
    assert original.int("checkin.before_minutes") == 15
    assert ParamSet.from_snapshot(snapshot).int("checkin.before_minutes") == 15
    assert changed.int("checkin.before_minutes") == 5
