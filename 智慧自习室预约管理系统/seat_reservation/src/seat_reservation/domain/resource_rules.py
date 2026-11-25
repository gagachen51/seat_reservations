"""资源与座位治理的纯规则函数（无 I/O、不读系统时钟）。

对应规则编号
------------
* BR-09：座位维护/封禁期间不可预约，受影响预约取消且不记违规；
* FR-21：自习室与座位资源管理（新增 / 修改 / 启用 / 停用 / 维护）；
* PR-05：自习室与座位维护权限（含座位跨自习室移动保护）；
* PR-06：座位封禁与解封；
* ST-11：状态变更必须留痕（审计）；
* ST-12：状态不允许时拒绝且无副作用。

设计要点（v1.2 零回填迁移）
--------------------------
``Seat.maintenance_by_block`` 是“维护来源”的唯一依据：

* ``True``  = 维护状态由**封禁流程**设置，封禁到期/释放时可自动恢复为 ``available``；
* ``False`` = 非封禁管理（**手工维护** / 停用 / 正常），任何封禁到期都**不得**自动恢复。

因此本模块所有恢复判断都以该标记为准，绝不根据 ``admin_status`` 反推来源，
从而保证“管理员手工设为维护”的座位不会被一次旧封禁到期误恢复成可预约。
"""

from __future__ import annotations

from datetime import datetime
from typing import Iterable, Sequence

from .models import (
    OCCUPYING_STATUSES,
    Reservation,
    Seat,
    SeatAdminStatus,
    SeatBlock,
)
from .rules import implements

#: 座位管理状态取值（与 ``seats.admin_status`` 的 CHECK 约束一致）
SEAT_ADMIN_STATUSES: tuple[str, ...] = (
    SeatAdminStatus.AVAILABLE.value,
    SeatAdminStatus.MAINTENANCE.value,
    SeatAdminStatus.DISABLED.value,
)

#: 批量建座单次上限（FR-21；超过则整批拒绝，不做部分成功）
BULK_SEAT_LIMIT = 100

#: 阻止座位跨自习室移动的预约状态（PR-05：历史房间归属不可被改写）
MOVING_BLOCKING_STATUSES = frozenset(OCCUPYING_STATUSES)


# ---------------------------------------------------------------------------
# 座位状态与封禁来源
# ---------------------------------------------------------------------------
@implements("FR-21", "PR-05")
def seat_status_label(admin_status: str) -> str:
    """座位管理状态的中文标签（管理端展示口径）。"""
    return {
        SeatAdminStatus.AVAILABLE.value: "启用",
        SeatAdminStatus.MAINTENANCE.value: "维护中",
        SeatAdminStatus.DISABLED.value: "停用",
    }.get(admin_status, admin_status)


@implements("FR-21", "PR-06")
def is_manual_maintenance(seat: Seat) -> bool:
    """座位是否处于“手工维护”：维护中但维护来源不是封禁流程。"""
    return (
        seat.admin_status == SeatAdminStatus.MAINTENANCE.value
        and not seat.maintenance_by_block
    )


@implements("BR-09", "PR-06")
def block_covers(block: SeatBlock, now: datetime) -> bool:
    """封禁窗口是否覆盖给定时刻（左闭右开：``start_at <= now < end_at``）。"""
    return block.start_at <= now < block.end_at


# ---------------------------------------------------------------------------
# 封禁生命周期：状态迁移
# ---------------------------------------------------------------------------
@implements("BR-09", "PR-06", "ST-11")
def seat_state_after_block(
    seat: Seat, block: SeatBlock, now: datetime
) -> tuple[str, bool]:
    """新增封禁后座位应有的 ``(admin_status, maintenance_by_block)``。

    分支顺序就是“不猜测来源”的实现：

    1. ``disabled``：**不改状态**、标记置 0，封禁只通过 ``seat_views`` 的时间窗生效；
       这样封禁到期**绝不可能**把停用座位恢复成可预约。
    2. 手工维护（``maintenance`` 且标记为 0）：**不改状态**、标记保持 0，
       管理员的手工意图优先，封禁到期也不会自动恢复。
    3. 其他（``available``，或由封禁管理的 ``maintenance``）：标记置 1；
       **仅当封禁窗口覆盖当前时刻**才立刻把状态改为 ``maintenance``，
       未来封禁不改当前状态，维护态由 ``seat_views`` 按时间窗派生。
    """
    if seat.admin_status == SeatAdminStatus.DISABLED.value:
        return SeatAdminStatus.DISABLED.value, False
    if is_manual_maintenance(seat):
        return SeatAdminStatus.MAINTENANCE.value, False
    if block_covers(block, now):
        return SeatAdminStatus.MAINTENANCE.value, True
    return seat.admin_status, True


@implements("BR-09", "PR-06", "ST-11")
def seat_state_after_blocks_closed(
    seat: Seat, remaining_blocks: Sequence[SeatBlock]
) -> tuple[str, bool]:
    """封禁记录关闭/到期后座位应有的 ``(admin_status, maintenance_by_block)``。

    * 仍有其他未释放封禁 → 维持现状（多封禁不允许提前恢复）；
    * 标记为 1 且当前是 ``maintenance`` → 恢复 ``available``；
    * ``disabled`` / ``available`` / 手工维护 → 状态不变，但标记归零
      （``disabled`` 永远不满足恢复条件，因此绝不会变 ``available``）。
    """
    if remaining_blocks:
        return seat.admin_status, seat.maintenance_by_block
    if (
        seat.maintenance_by_block
        and seat.admin_status == SeatAdminStatus.MAINTENANCE.value
    ):
        return SeatAdminStatus.AVAILABLE.value, False
    return seat.admin_status, False


@implements("BR-09", "PR-06", "ST-12")
def can_restore_seat(seat: Seat, remaining_blocks: Sequence[SeatBlock]) -> bool:
    """按记录解封时是否允许显式恢复座位（``restore_seat=true`` 的前置条件）。

    只有“由封禁流程设置的维护中座位、且已无其他未释放封禁”才允许恢复；
    ``disabled`` 座位必须走座位管理（``PUT /admin/seats/{id}``）显式启用。
    """
    if remaining_blocks:
        return False
    return (
        seat.maintenance_by_block
        and seat.admin_status == SeatAdminStatus.MAINTENANCE.value
    )


# ---------------------------------------------------------------------------
# 座位移动与停用影响
# ---------------------------------------------------------------------------
@implements("PR-05")
def has_occupying_reservation(reservations: Iterable[Reservation]) -> bool:
    """是否存在会阻止座位跨自习室移动的预约（待签到 / 使用中 / 暂离）。"""
    return any(item.status in MOVING_BLOCKING_STATUSES for item in reservations)


# ---------------------------------------------------------------------------
# 批量建座
# ---------------------------------------------------------------------------
@implements("FR-21", "PR-05")
def bulk_limit_violation(count: int) -> str | None:
    """批量建座条数校验：返回错误信息，``None`` 表示通过。"""
    if count <= 0:
        return "请至少提交一条座位数据"
    if count > BULK_SEAT_LIMIT:
        return f"单次最多批量新增 {BULK_SEAT_LIMIT} 个座位，请分批提交"
    return None


@implements("FR-21", "PR-05")
def duplicate_ids(ids: Sequence[str]) -> list[str]:
    """返回批量提交中重复的座位编号（保持首次出现顺序，便于前端提示）。"""
    seen: set[str] = set()
    repeated: list[str] = []
    for seat_id in ids:
        if seat_id in seen and seat_id not in repeated:
            repeated.append(seat_id)
        seen.add(seat_id)
    return repeated
