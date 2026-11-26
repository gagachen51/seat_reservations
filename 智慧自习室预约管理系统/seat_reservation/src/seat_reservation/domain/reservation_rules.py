"""续约（延长时段）与候补取消的纯规则函数。

本模块只做计算与判定，不访问数据库、不抛异常，便于单测与复用。引擎在
``extend_reservation`` / ``cancel_waitlist`` 中按固定顺序调用这些函数，把
“哪条规则不满足”转换成统一的 ``AppError`` / ``RuleViolation``。

v1.2 保守实现范围（见开发计划 6.3）：

* 续约只允许 ``in_use`` / ``away`` 且 ``now < end_at``；``pending`` 续约待 Q-03。
* 续约只使用现有参数 ``booking.max_minutes`` / ``booking.daily_max_minutes``，
  不新增参数、不新增表、不新增迁移。
* 候补取消只允许 ``queued`` / ``notified``；Q-05 确认前只改状态为
  ``cancelled``，不自动顺延下一位。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from .models import ReservationStatus, WaitlistStatus
from .rules import within_open_hours
from .timeutil import SLOT_MINUTES, hhmm, minutes_of_slots

#: 续约步长：与全局时段粒度一致（30 分钟）。
EXTENSION_STEP_MINUTES = SLOT_MINUTES

#: v1.2 允许续约的预约状态；pending 等待 Q-03 确认，确认前一律拒绝。
EXTENDABLE_STATUSES = frozenset(
    {ReservationStatus.IN_USE.value, ReservationStatus.AWAY.value}
)

#: v1.2 允许取消的候补状态。
WAITLIST_CANCELLABLE_STATUSES = frozenset(
    {WaitlistStatus.QUEUED.value, WaitlistStatus.NOTIFIED.value}
)

#: 允许确认的候补状态：只有已经收到补位通知的候补才能确认。
WAITLIST_CONFIRMABLE_STATUSES = frozenset({WaitlistStatus.NOTIFIED.value})


# ---------------------------------------------------------------------------
# 续约
# ---------------------------------------------------------------------------
def extension_state_error(
    status: str, now: datetime, end_at: datetime
) -> tuple[str, str] | None:
    """校验状态与时间窗；返回 ``(规则编号, 提示)``，通过则返回 ``None``。"""
    if status not in EXTENDABLE_STATUSES:
        return ("ST-12", "只有使用中或暂离中的预约可以延长时段")
    if now >= end_at:
        return ("ST-12", "预约已结束，不能延长时段")
    return None


def extension_minutes_error(extra_minutes: Any) -> str | None:
    """校验延长时长：必须是不小于 30 分钟的 30 分钟整数倍。"""
    if isinstance(extra_minutes, bool) or not isinstance(extra_minutes, int):
        return "延长时长必须是 30 分钟的整数倍"
    if extra_minutes < EXTENSION_STEP_MINUTES:
        return "延长时长不得少于 30 分钟"
    if extra_minutes % EXTENSION_STEP_MINUTES:
        return "延长时长必须是 30 分钟的整数倍"
    return None


def extended_end_slot(end_slot: int, extra_minutes: int) -> int:
    """按 30 分钟粒度计算延长后的结束时段（只做算术，不校验）。"""
    return end_slot + extra_minutes // EXTENSION_STEP_MINUTES


def extension_bounds_error(
    start_slot: int,
    new_end_slot: int,
    close_slot: int,
    max_minutes: int,
) -> tuple[str, str] | None:
    """校验延长后的边界：不越过自习室关闭时段，且总时长不超过单次上限。"""
    if new_end_slot > close_slot:
        return (
            "BR-01",
            f"延长后结束时间超过自习室关闭时间 {hhmm(close_slot)}",
        )
    total_minutes = minutes_of_slots(start_slot, new_end_slot)
    if total_minutes > max_minutes:
        return (
            "BR-01",
            f"延长后单次预约不得长于 {max_minutes} 分钟",
        )
    return None


def extension_quota_error(
    used_minutes: int,
    extra_minutes: int,
    daily_max_minutes: int,
) -> tuple[str, str] | None:
    """校验 BR-02：当日已用时长 + 本次延长时长不超过当日上限。"""
    if used_minutes + extra_minutes > daily_max_minutes:
        return (
            "BR-02",
            f"延长后当日预约总时长将超过上限（{daily_max_minutes} 分钟）",
        )
    return None


def max_extension_minutes(
    start_slot: int,
    end_slot: int,
    close_slot: int,
    max_minutes: int,
    daily_used_minutes: int,
    daily_max_minutes: int,
) -> int:
    """基于现有参数计算本次最多可延长的分钟数（向下取整到 30 分钟）。

    只用于展示/辅助判断；真正的写入仍由引擎逐项校验，避免调用方自行
    计算出的上限绕过 BR-01 / BR-02 / 关闭时段。
    """
    candidates = (
        (close_slot - end_slot) * EXTENSION_STEP_MINUTES,
        max_minutes - minutes_of_slots(start_slot, end_slot),
        daily_max_minutes - daily_used_minutes,
    )
    available = min(candidates)
    if available <= 0:
        return 0
    return (available // EXTENSION_STEP_MINUTES) * EXTENSION_STEP_MINUTES


# ---------------------------------------------------------------------------
# 候补取消
# ---------------------------------------------------------------------------
def waitlist_cancel_state_error(status: str) -> tuple[str, str] | None:
    """校验候补是否可取消；只有排队中/已通知可取消。"""
    if status not in WAITLIST_CANCELLABLE_STATUSES:
        return ("ST-12", "该候补当前不可取消")
    return None


def waitlist_confirm_state_error(status: str) -> tuple[str, str] | None:
    """校验候补是否可确认；只有 ``notified`` 可以确认。"""
    if status not in WAITLIST_CONFIRMABLE_STATUSES:
        return ("ST-12", f"当前状态为 {status}，只有已通知的候补可以确认")
    return None


def waitlist_target_error(
    room_status: str,
    seat_status: str,
    blocked: bool,
    start_slot: int,
    end_slot: int,
    open_slot: int,
    close_slot: int,
    start_at: datetime,
    now: datetime,
) -> tuple[str, str] | None:
    """候补加入/确认的资源与时间校验（M-07 的纯规则入口）。

    调用方必须传入**当前最新**的房间/座位/封禁状态；函数本身不读库，便于单测。
    返回 ``(规则编号, 提示)``，通过则 ``None``。
    """
    if room_status != "open":
        return ("FR-21", "自习室已停用，不能加入或确认候补")
    if seat_status != "available":
        label = "维护中" if seat_status == "maintenance" else "已停用"
        return ("BR-09", f"座位当前{label}，不能加入或确认候补")
    if blocked:
        return ("BR-09", "座位在该时段处于维护封禁，不能加入或确认候补")
    if not within_open_hours(start_slot, end_slot, open_slot, close_slot):
        return ("BR-03", "候补时段须在自习室开放时间内")
    if start_at <= now:
        return ("BR-08", "目标时段已开始，不能加入或确认候补")
    return None


# ---------------------------------------------------------------------------
# 账号限制复核（M-06）
# ---------------------------------------------------------------------------
def checkin_account_error(
    is_active: bool, limit: tuple[str, str] | None
) -> tuple[str, str] | None:
    """签到/ACS 通道的账号复核。

    ``limit`` 由引擎的 ``_booking_limit`` 统一计算（黑名单/信用/连续违约），
    这里只额外保证“停用账号即使持有未过期凭证也不能签到”。
    """
    if not is_active:
        return ("PR-02", "账号已停用，无法签到")
    return limit


def extension_account_error(
    is_active: bool, limit: tuple[str, str] | None
) -> tuple[str, str] | None:
    """续约前的账号复核：停用/黑名单/信用/连续违约一律拒绝。"""
    if not is_active:
        return ("PR-02", "账号已停用，无法延长时段")
    return limit
