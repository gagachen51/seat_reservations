"""纯规则函数与规则编号注册表。

* 所有函数无副作用、不访问数据库，便于单测与复用。
* 用 ``@implements("BR-04", "ST-01")`` 标注实现位置，``tools/dump_rule_map.py``
  可自动生成“规则 → 代码位置”映射，避免文档与代码脱节。
"""

from __future__ import annotations

import functools
from datetime import date, datetime, time, timedelta
from typing import Callable, TypeVar

from .models import ReservationStatus, SeatState
from .timeutil import minutes_of_slots, slot_of_time

F = TypeVar("F", bound=Callable)

#: 规则编号 → [(模块, 函数, 行号)]
IMPLEMENTS: dict[str, list[tuple[str, str, int]]] = {}


def implements(*codes: str) -> Callable[[F], F]:
    def decorator(func: F) -> F:
        for code in codes:
            IMPLEMENTS.setdefault(code, []).append(
                (func.__module__, func.__qualname__, func.__code__.co_firstlineno)
            )
        return func

    return decorator


# ---------------------------------------------------------------------------
# 时间窗
# ---------------------------------------------------------------------------
@implements("BR-03")
def in_booking_window(day: date, today: date, window_days: int) -> bool:
    """BR-03：仅可预约未来 N 天内（含当日）的时段。"""
    return today <= day <= today + timedelta(days=max(window_days - 1, 0))


@implements("BR-01")
def validate_slot_range(
    start_slot: int,
    end_slot: int,
    min_minutes: int,
    max_minutes: int,
    slot_minutes: int = 30,
) -> tuple[bool, str]:
    """BR-01：0.5~4 小时、30 分钟粒度、同日连续时段。"""
    if start_slot < 0 or end_slot > 48:
        return False, "时段超出当日范围"
    if end_slot <= start_slot:
        return False, "结束时间必须晚于开始时间"
    minutes = minutes_of_slots(start_slot, end_slot)
    if minutes % slot_minutes:
        return False, f"预约时长必须是 {slot_minutes} 分钟的整数倍"
    if minutes < min_minutes:
        return False, f"单次预约不得短于 {min_minutes} 分钟"
    if minutes > max_minutes:
        return False, f"单次预约不得长于 {max_minutes} 分钟"
    return True, ""


@implements("BR-01")
def within_open_hours(start_slot: int, end_slot: int, open_slot: int, close_slot: int) -> bool:
    return start_slot >= open_slot and end_slot <= close_slot


@implements("BR-04")
def checkin_window(start_at: datetime, before_minutes: int, after_minutes: int) -> tuple[datetime, datetime]:
    """BR-04：签到窗口为闭区间 [开始-前, 开始+后]。"""
    return start_at - timedelta(minutes=before_minutes), start_at + timedelta(minutes=after_minutes)


@implements("BR-04", "ST-12")
def checkin_phase(now: datetime, start_at: datetime, before_minutes: int, after_minutes: int) -> str:
    opens, closes = checkin_window(start_at, before_minutes, after_minutes)
    if now < opens:
        return "early"
    if now <= closes:
        return "inside"
    return "late"


@implements("BR-10")
def cancel_phase(now: datetime, start_at: datetime, free_minutes: int) -> str:
    """BR-10：距开始 ≥ 免费提前量不扣分；已开始则禁止自助取消。"""
    if now >= start_at:
        return "closed"
    if (start_at - now) >= timedelta(minutes=free_minutes):
        return "free"
    return "late"


@implements("BR-06", "ST-08")
def reward_eligible(
    checked_in_at: datetime | None,
    start_at: datetime,
    checked_out_at: datetime | None,
    end_at: datetime,
    grace_minutes: int = 15,
) -> bool:
    """按时签到且不晚于结束签退，才计入奖励。"""
    if checked_in_at is None or checked_out_at is None:
        return False
    return checked_in_at <= start_at + timedelta(minutes=grace_minutes) and checked_out_at <= end_at


# ---------------------------------------------------------------------------
# 信用与状态
# ---------------------------------------------------------------------------
@implements("BR-06")
def clamp_credit(value: int, minimum: int, maximum: int) -> int:
    return max(minimum, min(maximum, value))


@implements("BR-07")
def credit_blocks_booking(credit: int, threshold: int) -> bool:
    return credit < threshold


@implements("BR-11")
def slots_overlap(a_start: int, a_end: int, b_start: int, b_end: int) -> bool:
    """相邻时段无缝衔接，不算重叠。"""
    return a_start < b_end and b_start < a_end


@implements("SM-02")
def seat_state_from_reservation(status: str) -> SeatState:
    mapping = {
        ReservationStatus.PENDING.value: SeatState.RESERVED,
        ReservationStatus.IN_USE.value: SeatState.OCCUPIED,
        ReservationStatus.AWAY.value: SeatState.HELD,
    }
    return mapping.get(status, SeatState.AVAILABLE)


@implements("BR-13")
def credential_valid(
    issued_at: datetime | None,
    used: bool,
    ttl_seconds: int,
    now: datetime,
    expected_code: str | None,
    provided_code: str | None,
) -> tuple[bool, str]:
    if used:
        return False, "凭证已被使用"
    if not expected_code or not issued_at:
        return False, "尚未签发有效凭证"
    if provided_code is None or provided_code.strip().upper() != expected_code.upper():
        return False, "凭证不匹配"
    if now > issued_at + timedelta(seconds=ttl_seconds):
        return False, "凭证已过期，请重新签发"
    return True, ""


def slot_bounds(day: date, start_slot: int, end_slot: int) -> tuple[datetime, datetime]:
    from .timeutil import datetime_at

    return datetime_at(day, start_slot), datetime_at(day, end_slot)


def time_to_slot(value: time) -> int:
    return slot_of_time(value)


def with_grace(value: datetime, minutes: int) -> datetime:
    return value + timedelta(minutes=minutes)


@implements("BR-02")
def quota_exceeded(count: int, minutes: int, new_minutes: int, max_count: int, max_minutes: int) -> str | None:
    if count + 1 > max_count:
        return f"当日预约个数已达上限（{max_count} 个）"
    if minutes + new_minutes > max_minutes:
        return f"当日预约总时长将超过上限（{max_minutes} 分钟）"
    return None


@implements("BR-15")
def consecutive_limit_active(count: int, threshold: int, until: datetime | None, now: datetime) -> bool:
    return count >= threshold and (until is None or now < until)


@implements("BR-20")
def blacklist_threshold_reached(active_violations: int, threshold: int) -> bool:
    return active_violations >= threshold


def memoize(func: F) -> F:  # pragma: no cover - 预留
    cache: dict[tuple, object] = {}

    @functools.wraps(func)
    def wrapper(*args):  # type: ignore[no-untyped-def]
        key = tuple(args)
        if key not in cache:
            cache[key] = func(*args)
        return cache[key]

    return wrapper  # type: ignore[return-value]
