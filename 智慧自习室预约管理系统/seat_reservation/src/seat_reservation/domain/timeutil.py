"""时间与时段工具。

系统统一使用**本地 naive datetime**（SRS 第 7.2 节约定），时段粒度 30 分钟，
用 ``slot``（0..47）表示一天内的第几个半小时，避免字符串比较的边界错误。
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

SLOT_MINUTES = 30
SLOTS_PER_DAY = 24 * 60 // SLOT_MINUTES
DATE_FMT = "%Y-%m-%d"
TIME_FMT = "%H:%M"
DATETIME_FMT = "%Y-%m-%d %H:%M:%S"


def parse_date(value: str | date) -> date:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    return datetime.strptime(str(value).strip(), DATE_FMT).date()


def parse_time(value: str | time) -> time:
    if isinstance(value, time):
        return value.replace(second=0, microsecond=0)
    text = str(value).strip()
    for fmt in ("%H:%M", "%H:%M:%S"):
        try:
            return datetime.strptime(text, fmt).time().replace(second=0, microsecond=0)
        except ValueError:
            continue
    raise ValueError(f"时间格式应为 HH:MM，收到 {value!r}")


def parse_datetime(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        return value.replace(microsecond=0)
    text = str(value).strip().replace("T", " ")
    for fmt in (DATETIME_FMT, "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    raise ValueError(f"时间格式应为 YYYY-MM-DD HH:MM[:SS]，收到 {value!r}")


def fmt_date(value: date) -> str:
    return value.strftime(DATE_FMT)


def fmt_time(value: time) -> str:
    return value.strftime(TIME_FMT)


def fmt_datetime(value: datetime | None) -> str | None:
    return None if value is None else value.strftime(DATETIME_FMT)


def slot_of_time(value: time) -> int:
    if value.second or value.microsecond:
        raise ValueError("时段必须对齐到整分钟")
    minutes = value.hour * 60 + value.minute
    if minutes % SLOT_MINUTES:
        raise ValueError(f"时段必须对齐到 {SLOT_MINUTES} 分钟粒度")
    return minutes // SLOT_MINUTES


def time_of_slot(slot: int) -> time:
    if not 0 <= slot <= SLOTS_PER_DAY:
        raise ValueError(f"slot 越界：{slot}")
    minutes = slot * SLOT_MINUTES
    return time(hour=minutes // 60 % 24, minute=minutes % 60)


def hhmm(slot: int) -> str:
    return fmt_time(time_of_slot(slot))


def datetime_at(day: date, slot: int) -> datetime:
    return datetime.combine(day, time_of_slot(min(slot, SLOTS_PER_DAY)))


def slot_of_datetime(value: datetime) -> int:
    return slot_of_time(value.time())


def minutes_of_slots(start_slot: int, end_slot: int) -> int:
    return (end_slot - start_slot) * SLOT_MINUTES


def add_minutes(value: datetime, minutes: int) -> datetime:
    return value + timedelta(minutes=minutes)


def overlap(start_a: datetime, end_a: datetime, start_b: datetime, end_b: datetime) -> bool:
    """半开区间相交判定：相邻（end == start）不算重叠。"""
    return start_a < end_b and start_b < end_a
