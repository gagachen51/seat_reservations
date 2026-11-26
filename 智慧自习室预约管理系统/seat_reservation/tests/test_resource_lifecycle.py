"""成员 B：资源与座位治理的生命周期测试（FR-21 / BR-09 / PR-05 / PR-06 / ST-11）。

覆盖 v1.2 要求的 7 种状态组合与迁移安全：

1. ``available`` + 生效封禁 → 到期自动恢复；
2. ``available`` + 未来封禁 → 当前状态不变，维护态由 ``seat_views`` 派生；
3. 手工 ``maintenance`` + 封禁 → 不误恢复；
4. ``disabled`` + 封禁 → 到期**绝不**变 ``available``；
5. 多封禁 → 释放一条不提前恢复；
6. 管理员在封禁期间显式改状态 → 标记清零、到期不覆盖；
7. 迁移零回填 → 历史维护座位按手工维护、释放旧封禁仍是维护。

另外覆盖：按记录解封（默认不改状态 / 显式恢复 / 停用拒绝）、批量建座全量回滚、
停用影响预览只读、座位跨自习室移动保护、权限矩阵与新路由契约。
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, time, timedelta

import pytest
from fastapi.testclient import TestClient

from seat_reservation.api.app import create_app
from seat_reservation.api.routes import admin_routes as admin_routes_module
from seat_reservation.api.routes import resource_routes as resource_routes_module
from seat_reservation.config import Settings
from seat_reservation.domain import resource_rules
from seat_reservation.domain.models import Seat, SeatBlock, Violation
from seat_reservation.errors import Conflict, Forbidden, NotFound, RuleViolation, ValidationFailed
from seat_reservation.storage.repository import SqliteRepository
from tests.helpers import D, NOW, PASSWORD, Env

# ---------------------------------------------------------------------------
# 本模块专用夹具（公共夹具归 B 统一维护，这里只放资源治理专用的小工具）
# ---------------------------------------------------------------------------


def _seat(env: Env, seat_id: str):
    with env.ctx.db.read() as conn:
        return SqliteRepository(conn).get_seat(seat_id)


def _set_seat(env: Env, seat_id: str, *, admin_status: str | None = None,
              maintenance_by_block: bool | None = None) -> None:
    """直接改库模拟历史数据 / 后台脚本，不经过引擎（用于构造迁移前状态）。"""
    with env.ctx.db.transaction() as conn:
        if admin_status is not None:
            conn.execute("UPDATE seats SET admin_status = ? WHERE id = ?", (admin_status, seat_id))
        if maintenance_by_block is not None:
            conn.execute(
                "UPDATE seats SET maintenance_by_block = ? WHERE id = ?",
                (int(maintenance_by_block), seat_id),
            )


def _active_blocks(env: Env, seat_id: str) -> list[SeatBlock]:
    with env.ctx.db.read() as conn:
        return SqliteRepository(conn).active_seat_blocks(seat_id)


def _all_blocks(env: Env, seat_id: str) -> list[SeatBlock]:
    with env.ctx.db.read() as conn:
        return SqliteRepository(conn).get_seat_blocks(seat_id)


def _release(env: Env, block_id: str, *, restore: bool = False,
             reason: str = "维修完成，提前解封", now: datetime = NOW, user: str = "A1"):
    with env.ctx.engine() as engine:
        return engine.release_seat_block(
            env.actor(user), block_id, reason, now, restore_seat=restore
        )


def _update_seat(env: Env, seat_id: str, *, user: str = "A1", **kwargs):
    payload = {
        "room_id": None,
        "seat_type": None,
        "capacity": None,
        "admin_status": None,
        "has_power": None,
        "is_window": None,
        "note": None,
    }
    payload.update(kwargs)
    with env.ctx.engine() as engine:
        return engine.update_seat(env.actor(user), seat_id, now=NOW, **payload)


def _bulk(env: Env, room_id: str, seats: list[dict], *, user: str = "A1"):
    with env.ctx.engine() as engine:
        return engine.bulk_create_seats(env.actor(user), room_id, seats, NOW)


def _impact(env: Env, room_id: str, status: str = "closed", *, user: str = "A1"):
    with env.ctx.engine(write=False) as engine:
        return engine.room_impact(env.actor(user), room_id, status)


def _list_blocks(env: Env, *, user: str = "A1", **kwargs):
    with env.ctx.engine(write=False) as engine:
        return engine.list_seat_blocks(env.actor(user), **kwargs)


def _reservation_count(env: Env) -> int:
    with env.ctx.db.read() as conn:
        return int(conn.execute("SELECT COUNT(*) AS c FROM reservations").fetchone()["c"])


# ---------------------------------------------------------------------------
# 1. available + 生效封禁 → 到期恢复
# ---------------------------------------------------------------------------
def test_available_seat_block_sets_maintenance_then_expires_to_available(env: Env) -> None:
    result = env.block_seat(
        "A1", "R1-001", "座椅损坏维修",
        NOW - timedelta(hours=1), NOW + timedelta(hours=1), now=NOW,
    )
    assert result.ok, result.message
    assert result.data["seat_status"] == "maintenance"
    assert result.data["maintenance_by_block"] is True
    seat = _seat(env, "R1-001")
    assert seat.admin_status == "maintenance"
    assert seat.maintenance_by_block is True

    summary = env.sweep(now=NOW + timedelta(minutes=90))
    assert summary["seat_blocks_expired"] == 1
    seat = _seat(env, "R1-001")
    assert seat.admin_status == "available"
    assert seat.maintenance_by_block is False
    assert env.seat_state("R1-001", time(9, 0), time(10, 0)) == "available"
    assert any(log.action == "seat_block_expired" for log in env.audit())


# ---------------------------------------------------------------------------
# 2. available + 未来封禁 → 当前不变，时间窗内派生维护
# ---------------------------------------------------------------------------
def test_future_block_does_not_change_status_and_derives_maintenance_state(env: Env) -> None:
    # now=08:00，封禁 09:00-10:00：不允许“未来封禁立即生效”
    result = env.block_seat(
        "A1", "R1-001", "下午安排检修",
        datetime(2026, 5, 11, 9, 0), datetime(2026, 5, 11, 10, 0), now=NOW,
    )
    assert result.ok, result.message
    assert result.data["seat_status"] == "available"
    assert result.data["maintenance_by_block"] is True
    assert _seat(env, "R1-001").admin_status == "available"
    # 时间窗内维护态由 seat_views 派生，不能只看 admin_status
    assert env.seat_state("R1-001", time(9, 0), time(10, 0)) == "maintenance"
    assert env.seat_state("R1-001", time(11, 0), time(12, 0)) == "available"

    summary = env.sweep(now=datetime(2026, 5, 11, 11, 0))
    assert summary["seat_blocks_expired"] == 1
    seat = _seat(env, "R1-001")
    assert seat.admin_status == "available"
    assert seat.maintenance_by_block is False


# ---------------------------------------------------------------------------
# 3. 手工维护 + 封禁 → 不误恢复
# ---------------------------------------------------------------------------
def test_manual_maintenance_is_not_auto_restored_by_block_expiry(env: Env) -> None:
    _set_seat(env, "R1-001", admin_status="maintenance", maintenance_by_block=False)
    result = env.block_seat(
        "A1", "R1-001", "临时封禁测试",
        NOW - timedelta(hours=1), NOW + timedelta(hours=1), now=NOW,
    )
    assert result.ok, result.message
    assert result.data["seat_status"] == "maintenance"
    assert result.data["maintenance_by_block"] is False  # 手工维护不写封禁标记

    env.sweep(now=NOW + timedelta(minutes=90))
    seat = _seat(env, "R1-001")
    assert seat.admin_status == "maintenance"  # 到期不自动恢复
    assert seat.maintenance_by_block is False


# ---------------------------------------------------------------------------
# 4. disabled + 封禁 + 到期 → 绝不变 available
# ---------------------------------------------------------------------------
def test_disabled_seat_never_restored_by_block_expiry(env: Env) -> None:
    _set_seat(env, "R1-003", admin_status="disabled", maintenance_by_block=False)
    result = env.block_seat(
        "A1", "R1-003", "停用座位叠加封禁",
        NOW - timedelta(hours=1), NOW + timedelta(hours=1), now=NOW,
    )
    assert result.ok, result.message
    assert result.data["seat_status"] == "disabled"
    assert result.data["maintenance_by_block"] is False

    env.sweep(now=NOW + timedelta(minutes=90))
    seat = _seat(env, "R1-003")
    assert seat.admin_status == "disabled"
    assert seat.maintenance_by_block is False
    assert env.seat_state("R1-003", time(9, 0), time(10, 0)) == "disabled"


# ---------------------------------------------------------------------------
# 5. 多封禁 → 释放最后一条才恢复
# ---------------------------------------------------------------------------
def test_multiple_blocks_restore_only_after_last_release(env: Env) -> None:
    first = env.block_seat(
        "A1", "R1-002", "第一段维修",
        NOW - timedelta(hours=1), NOW + timedelta(hours=4), now=NOW,
    )
    second = env.block_seat(
        "A1", "R1-002", "第二段维修",
        NOW - timedelta(hours=1), NOW + timedelta(hours=5), now=NOW,
    )
    assert first.ok and second.ok
    assert _seat(env, "R1-002").maintenance_by_block is True
    assert len(_active_blocks(env, "R1-002")) == 2

    released = _release(env, first.data["block"]["id"], restore=True)
    assert released.ok, released.message
    assert released.data["restored"] is False
    assert _seat(env, "R1-002").admin_status == "maintenance"
    assert len(_active_blocks(env, "R1-002")) == 1

    released = _release(env, second.data["block"]["id"], restore=True)
    assert released.data["restored"] is True
    seat = _seat(env, "R1-002")
    assert seat.admin_status == "available"
    assert seat.maintenance_by_block is False


# ---------------------------------------------------------------------------
# 6. 管理员在封禁期间显式改状态 → 标记清零、到期不覆盖
# ---------------------------------------------------------------------------
def test_admin_status_override_clears_block_marker(env: Env) -> None:
    env.block_seat(
        "A1", "R1-004", "检修",
        NOW - timedelta(hours=1), NOW + timedelta(hours=1), now=NOW,
    )
    assert _seat(env, "R1-004").maintenance_by_block is True

    updated = _update_seat(env, "R1-004", admin_status="disabled")
    assert updated.ok, updated.message
    seat = _seat(env, "R1-004")
    assert seat.admin_status == "disabled"
    assert seat.maintenance_by_block is False  # 手工意图优先

    env.sweep(now=NOW + timedelta(minutes=90))
    seat = _seat(env, "R1-004")
    assert seat.admin_status == "disabled"
    assert seat.maintenance_by_block is False

    # 管理员显式恢复后不再受历史封禁影响
    assert _update_seat(env, "R1-004", admin_status="available").ok
    assert _seat(env, "R1-004").admin_status == "available"


# ---------------------------------------------------------------------------
# 7. 迁移零回填 → 历史维护座位保持手工维护
# ---------------------------------------------------------------------------
def test_v1_manual_maintenance_seat_stays_manual_after_v2(env: Env) -> None:
    # 模拟升级前的历史数据：维护中 + 一条早已到期但未释放的旧封禁
    _set_seat(env, "R1-006", admin_status="maintenance", maintenance_by_block=False)
    with env.ctx.db.transaction() as conn:
        repo = SqliteRepository(conn)
        repo.insert_seat_block(
            SeatBlock(
                id="SB-LEGACY",
                seat_id="R1-006",
                reason="历史封禁（升级前）",
                start_at=datetime(2026, 5, 9, 9, 0),
                end_at=datetime(2026, 5, 9, 10, 0),
                created_by="A1",
                created_at=datetime(2026, 5, 9, 8, 30),
            )
        )

    summary = env.sweep(now=NOW)
    assert summary["seat_blocks_expired"] == 1
    seat = _seat(env, "R1-006")
    # 零回填：历史维护一律按手工维护，旧封禁到期不自动恢复
    assert seat.admin_status == "maintenance"
    assert seat.maintenance_by_block is False
    saved = next(item for item in _all_blocks(env, "R1-006") if item.id == "SB-LEGACY")
    assert saved.released_at is not None


# ---------------------------------------------------------------------------
# 8. 按记录解封：默认不改状态 + 写审计
# ---------------------------------------------------------------------------
def test_release_by_record_default_keeps_seat_status_and_writes_audit(env: Env) -> None:
    created = env.block_seat(
        "A1", "R2-001", "检修",
        NOW - timedelta(hours=1), NOW + timedelta(hours=2), now=NOW,
    )
    block_id = created.data["block"]["id"]
    assert _seat(env, "R2-001").admin_status == "maintenance"

    result = _release(env, block_id)
    assert result.ok, result.message
    assert result.data["restored"] is False
    assert result.data["seat_status"] == "maintenance"
    seat = _seat(env, "R2-001")
    assert seat.admin_status == "maintenance"  # 默认不改状态
    assert seat.maintenance_by_block is False  # 标记归零，避免以后被自动恢复

    # 标记已归零：后续扫描不会把这个座位恢复成 available
    env.sweep(now=NOW + timedelta(hours=3))
    assert _seat(env, "R2-001").admin_status == "maintenance"

    logs = [log for log in env.audit() if log.action == "seat_block_release"]
    assert logs
    assert logs[0].after["block_id"] == block_id
    assert logs[0].after["restored"] is False


# ---------------------------------------------------------------------------
# 9. 按记录解封 + 显式恢复
# ---------------------------------------------------------------------------
def test_release_by_record_with_restore_restores_only_block_managed_seat(env: Env) -> None:
    created = env.block_seat(
        "A1", "R2-002", "检修",
        NOW - timedelta(hours=1), NOW + timedelta(hours=2), now=NOW,
    )
    result = _release(env, created.data["block"]["id"], restore=True)
    assert result.ok, result.message
    assert result.data["restored"] is True
    seat = _seat(env, "R2-002")
    assert seat.admin_status == "available"
    assert seat.maintenance_by_block is False


# ---------------------------------------------------------------------------
# 10. 停用座位拒绝“解封恢复”
# ---------------------------------------------------------------------------
def test_release_disabled_seat_refuses_restore(env: Env) -> None:
    _set_seat(env, "R1-005", admin_status="disabled", maintenance_by_block=False)
    created = env.block_seat(
        "A1", "R1-005", "停用座位临时封禁",
        NOW - timedelta(hours=1), NOW + timedelta(hours=2), now=NOW,
    )
    block_id = created.data["block"]["id"]

    with pytest.raises(RuleViolation) as denied:
        _release(env, block_id, restore=True)
    assert denied.value.rule == "PR-06"
    # 拒绝无副作用：记录仍然是未释放
    assert len(_active_blocks(env, "R1-005")) == 1
    assert _seat(env, "R1-005").admin_status == "disabled"

    # 默认口径可以关闭记录，但座位保持停用
    result = _release(env, block_id, restore=False)
    assert result.ok
    assert result.data["seat_status"] == "disabled"
    assert _seat(env, "R1-005").admin_status == "disabled"


# ---------------------------------------------------------------------------
# 11. 按记录解封只关一条；既有 DELETE 关全部
# ---------------------------------------------------------------------------
def test_release_closes_only_target_record_and_seat_delete_closes_all(env: Env) -> None:
    first = env.block_seat(
        "A1", "R1-001", "第一段",
        NOW - timedelta(hours=1), NOW + timedelta(hours=4), now=NOW,
    )
    second = env.block_seat(
        "A1", "R1-001", "第二段",
        NOW - timedelta(hours=1), NOW + timedelta(hours=6), now=NOW,
    )
    _release(env, first.data["block"]["id"])
    remaining = _active_blocks(env, "R1-001")
    assert [item.id for item in remaining] == [second.data["block"]["id"]]

    with env.ctx.engine() as engine:
        result = engine.unblock_seat(env.actor("A1"), "R1-001", NOW)
    assert result.ok, result.message
    assert _active_blocks(env, "R1-001") == []
    assert result.data["restored"] is True
    assert _seat(env, "R1-001").admin_status == "available"

    with env.ctx.engine() as engine:
        with pytest.raises(RuleViolation):
            engine.unblock_seat(env.actor("A1"), "R1-001", NOW)


# ---------------------------------------------------------------------------
# 12. 按记录解封的错误分支
# ---------------------------------------------------------------------------
def test_release_by_record_error_branches(env: Env) -> None:
    with env.ctx.engine() as engine:
        with pytest.raises(NotFound):
            engine.release_seat_block(env.actor("A1"), "SB-404", "不存在", NOW)
        with pytest.raises(ValidationFailed):
            engine.release_seat_block(env.actor("A1"), "SB-404", "", NOW)
        with pytest.raises(Forbidden) as denied:
            engine.release_seat_block(env.actor("S1"), "SB-404", "学生尝试", NOW)
        assert denied.value.rule == "PR-06"

    created = env.block_seat(
        "A1", "R1-002", "检修",
        NOW - timedelta(hours=1), NOW + timedelta(hours=1), now=NOW,
    )
    block_id = created.data["block"]["id"]
    assert _release(env, block_id).ok
    with pytest.raises(RuleViolation) as repeated:
        _release(env, block_id)
    assert repeated.value.rule == "ST-12"


# ---------------------------------------------------------------------------
# 13. 批量建座：全部成功 + 审计
# ---------------------------------------------------------------------------
def test_bulk_create_seats_success_and_audit(env: Env) -> None:
    result = _bulk(
        env,
        "R2",
        [
            {"id": "R2-101", "type": "power", "capacity": 1, "has_power": True},
            {"id": "R2-102", "type": "quiet", "capacity": 1, "is_window": True},
            {"id": "R2-103", "type": "discussion", "capacity": 4},
        ],
    )
    assert result.ok, result.message
    assert result.data["count"] == 3
    assert _seat(env, "R2-101").has_power is True
    assert _seat(env, "R2-103").capacity == 4
    assert any(log.action == "seat_bulk_create" for log in env.audit())


# ---------------------------------------------------------------------------
# 14. 批量建座：任一条非法整批回滚
# ---------------------------------------------------------------------------
def test_bulk_create_seats_rolls_back_on_invalid_item(env: Env) -> None:
    with pytest.raises(ValidationFailed):
        _bulk(
            env,
            "R2",
            [
                {"id": "R2-201", "type": "standard", "capacity": 1},
                {"id": "R2-202", "type": "discussion", "capacity": 3},  # 讨论间容量必须 4~8
            ],
        )
    assert _seat(env, "R2-201") is None  # 整批回滚，不留半截数据
    assert _seat(env, "R2-202") is None


# ---------------------------------------------------------------------------
# 15. 批量建座：重复 / 已存在 / 超上限 / 权限
# ---------------------------------------------------------------------------
def test_bulk_create_seats_rejects_duplicate_existing_and_over_limit(env: Env) -> None:
    with pytest.raises(Conflict) as duplicated:
        _bulk(
            env,
            "R2",
            [
                {"id": "R2-301", "type": "standard", "capacity": 1},
                {"id": "R2-301", "type": "standard", "capacity": 1},
            ],
        )
    assert duplicated.value.rule == "PR-05"

    with pytest.raises(Conflict):
        _bulk(env, "R2", [{"id": "R2-001", "type": "standard", "capacity": 1}])

    # 座位编号是全局主键：跨自习室重复也必须拒绝，绝不能把 R1 的座位“搬”到 R2
    with pytest.raises(Conflict):
        _bulk(env, "R2", [{"id": "R1-001", "type": "standard", "capacity": 1}])
    assert _seat(env, "R1-001").room_id == "R1"

    with pytest.raises(ValidationFailed):
        _bulk(
            env,
            "R2",
            [{"id": f"R2-{index:03d}", "type": "standard", "capacity": 1} for index in range(101)],
        )

    with pytest.raises(NotFound):
        _bulk(env, "R9", [{"id": "R9-001", "type": "standard", "capacity": 1}])

    with pytest.raises(Forbidden) as denied:
        _bulk(env, "R2", [{"id": "R2-401", "type": "standard", "capacity": 1}], user="S1")
    assert denied.value.rule == "PR-05"


# ---------------------------------------------------------------------------
# 16. 停用影响预览：统计正确且只读
# ---------------------------------------------------------------------------
def test_room_impact_preview_counts_and_is_read_only(env: Env) -> None:
    env.create("S1", "R1-001", time(9, 0), time(10, 0), day=D + timedelta(days=1))
    env.create("S2", "R1-002", time(9, 0), time(10, 0), day=D + timedelta(days=1))
    env.create("S3", "R1-003", time(9, 0), time(10, 0), day=D + timedelta(days=1))
    with env.ctx.db.transaction() as conn:
        conn.execute(
            "UPDATE reservations SET status='in_use' WHERE seat_id='R1-002' AND status='pending'"
        )
        conn.execute(
            "UPDATE reservations SET status='away' WHERE seat_id='R1-003' AND status='pending'"
        )

    reservations_before = _reservation_count(env)
    seat_before = _seat(env, "R1-001").admin_status

    result = _impact(env, "R1", "closed")
    assert result.ok, result.message
    assert result.data["affected_count"] == 3
    assert result.data["by_status"] == {"pending": 1, "in_use": 1, "away": 1}
    assert result.data["target_status"] == "closed"
    assert len(result.data["items"]) == 3

    # 预览不写库
    assert _reservation_count(env) == reservations_before
    assert _seat(env, "R1-001").admin_status == seat_before

    with pytest.raises(NotFound):
        _impact(env, "R9")
    with pytest.raises(ValidationFailed):
        _impact(env, "R1", "broken")


# ---------------------------------------------------------------------------
# 17. 座位跨自习室移动保护
# ---------------------------------------------------------------------------
def test_seat_move_blocked_when_active_reservations_exist(env: Env) -> None:
    item = env.create("S1", "R1-001", time(9, 0), time(10, 0)).data

    with pytest.raises(Conflict) as denied:
        _update_seat(env, "R1-001", room_id="R2")
    assert denied.value.rule == "PR-05"
    assert _seat(env, "R1-001").room_id == "R1"

    assert env.cancel("S1", item["id"]).ok
    moved = _update_seat(env, "R1-001", room_id="R2")
    assert moved.ok, moved.message
    assert _seat(env, "R1-001").room_id == "R2"
    assert any(log.action == "seat_update" for log in env.audit())


def test_seat_move_allowed_without_active_reservations(env: Env) -> None:
    result = _update_seat(env, "R1-005", room_id="R2")
    assert result.ok, result.message
    assert _seat(env, "R1-005").room_id == "R2"


# ---------------------------------------------------------------------------
# 18. 权限矩阵（引擎级）
# ---------------------------------------------------------------------------
def test_resource_lifecycle_permissions(env: Env) -> None:
    for actor, rule in (("S1", "PR-06"), ("T1", "PR-06")):
        with pytest.raises(Forbidden) as denied:
            _list_blocks(env, user=actor)
        assert denied.value.rule == rule

    assert _list_blocks(env, user="A1").ok
    assert _list_blocks(env, user="Y1").ok
    assert _impact(env, "R1", user="Y1").ok

    with pytest.raises(Forbidden) as denied:
        env.block_seat("S1", "R1-001", "学生尝试", NOW, NOW + timedelta(hours=1))
    assert denied.value.rule == "PR-06"

    created = env.block_seat(
        "A1", "R1-002", "检修",
        NOW - timedelta(hours=1), NOW + timedelta(hours=1), now=NOW,
    )
    with pytest.raises(Forbidden):
        _release(env, created.data["block"]["id"], user="S1")


# ---------------------------------------------------------------------------
# 19. 封禁列表：字段与过滤
# ---------------------------------------------------------------------------
def test_seat_blocks_listing_fields_and_filters(env: Env) -> None:
    env.block_seat(
        "A1", "R1-001", "检修 A",
        NOW - timedelta(hours=1), NOW + timedelta(hours=1), now=NOW,
    )
    missing = env.block_seat(
        "A1", "R1-002", "检修 B",
        NOW + timedelta(days=1), NOW + timedelta(days=1, hours=2), now=NOW,
    )
    _release(env, missing.data["block"]["id"])

    listing = _list_blocks(env, user="A1")
    assert listing.ok
    assert listing.data["total"] == 2
    item = next(row for row in listing.data["items"] if row["seat_id"] == "R1-001")
    assert item["room_id"] == "R1"
    assert item["room_name"] == "A101"
    assert item["seat_status"] == "maintenance"
    assert item["seat_maintenance_by_block"] is True
    assert item["released_at"] is None
    assert item["reason"] == "检修 A"

    active = _list_blocks(env, user="A1", active_only=True)
    assert active.data["total"] == 1
    by_room = _list_blocks(env, user="A1", room_id="R2")
    assert by_room.data["total"] == 0
    by_seat = _list_blocks(env, user="A1", seat_id="R1-002")
    assert by_seat.data["total"] == 1


# ---------------------------------------------------------------------------
# 20. 迁移幂等与数据保留
# ---------------------------------------------------------------------------
def test_v2_migration_is_idempotent_and_keeps_existing_rows(env: Env) -> None:
    assert env.ctx.db.migrate() == []  # 重复执行不报错、不重复应用
    # v1 初始结构 + v2 封禁生命周期 + v4 资源/预约约束（L-07；v3 保留给共享层审计）
    assert env.ctx.db.schema_version() == 4
    with env.ctx.db.read() as conn:
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(seats)")}
        seats = int(conn.execute("SELECT COUNT(*) AS c FROM seats").fetchone()["c"])
        legacy = conn.execute(
            "SELECT COUNT(*) AS c FROM seats WHERE maintenance_by_block <> 0"
        ).fetchone()["c"]
    assert "maintenance_by_block" in columns
    assert seats == 8
    # 零回填：迁移后不应有任何座位被标记为“封禁维护”
    assert legacy == 0


# ---------------------------------------------------------------------------
# 21. 时长与边界：封禁参数校验
# ---------------------------------------------------------------------------
def test_block_seat_validation(env: Env) -> None:
    with pytest.raises(ValidationFailed):
        env.block_seat("A1", "R1-001", "x", NOW, NOW + timedelta(hours=1))
    with pytest.raises(ValidationFailed):
        env.block_seat("A1", "R1-001", "原因合法", NOW, NOW)
    with pytest.raises(NotFound):
        env.block_seat("A1", "R9-999", "原因合法", NOW, NOW + timedelta(hours=1))


# ---------------------------------------------------------------------------
# 22. 新路由 HTTP 契约与权限（含 CSRF）
# ---------------------------------------------------------------------------
@pytest.fixture
def client(ctx) -> TestClient:
    app = create_app(Settings.for_tests(ctx.settings.db_path))
    with TestClient(app) as test_client:
        yield test_client


def login(client: TestClient, username: str) -> dict[str, str]:
    response = client.post(
        "/api/auth/login", json={"username": username, "password": PASSWORD}
    )
    assert response.status_code == 200, response.text
    return {"X-CSRF-Token": client.cookies.get("seat_csrf") or ""}


def test_seat_block_routes_contract(client: TestClient, ctx) -> None:
    headers = login(client, "A1")

    listing = client.get("/api/admin/seat-blocks", headers=headers)
    assert listing.status_code == 200, listing.text
    assert listing.json()["data"]["total"] == 0

    created = client.post(
        "/api/admin/seat-blocks",
        headers=headers,
        json={
            "seat_id": "R1-001",
            "reason": "设备检修",
            "start_at": "2026-05-11T09:00:00",
            "end_at": "2026-05-11T11:00:00",
        },
    )
    assert created.status_code == 201, created.text
    block_id = created.json()["data"]["block"]["id"]
    assert created.json()["data"]["seat_status"] == "available"  # 未来封禁不立即生效

    listing = client.get(
        "/api/admin/seat-blocks", headers=headers, params={"seat_id": "R1-001"}
    )
    assert listing.status_code == 200
    assert listing.json()["data"]["items"][0]["id"] == block_id

    released = client.post(
        f"/api/admin/seat-blocks/{block_id}/release",
        headers=headers,
        json={"reason": "检修完成"},
    )
    assert released.status_code == 200, released.text
    assert released.json()["data"]["restored"] is False

    repeated = client.post(
        f"/api/admin/seat-blocks/{block_id}/release",
        headers=headers,
        json={"reason": "重复解封"},
    )
    assert repeated.status_code == 400
    assert repeated.json()["error"]["rule"] == "ST-12"

    missing = client.post(
        "/api/admin/seat-blocks/SB-404/release",
        headers=headers,
        json={"reason": "不存在"},
    )
    assert missing.status_code == 404

    bulk = client.post(
        "/api/admin/seats/bulk",
        headers=headers,
        json={
            "room_id": "R2",
            "seats": [{"id": "R2-501", "type": "standard", "capacity": 1}],
        },
    )
    assert bulk.status_code == 201, bulk.text
    assert bulk.json()["data"]["count"] == 1

    duplicate = client.post(
        "/api/admin/seats/bulk",
        headers=headers,
        json={
            "room_id": "R2",
            "seats": [
                {"id": "R2-502", "type": "standard", "capacity": 1},
                {"id": "R2-502", "type": "standard", "capacity": 1},
            ],
        },
    )
    assert duplicate.status_code == 409

    over_limit = client.post(
        "/api/admin/seats/bulk",
        headers=headers,
        json={
            "room_id": "R2",
            "seats": [
                {"id": f"R2-{index:03d}", "type": "standard", "capacity": 1}
                for index in range(101)
            ],
        },
    )
    assert over_limit.status_code == 422  # 请求模型层就拦下

    impact = client.get("/api/admin/rooms/R1/impact", headers=headers)
    assert impact.status_code == 200, impact.text
    assert impact.json()["data"]["room_id"] == "R1"
    assert impact.json()["data"]["target_status"] == "closed"

    impact_missing = client.get("/api/admin/rooms/R9/impact", headers=headers)
    assert impact_missing.status_code == 404

    # 权限矩阵：学生/教师/未登录
    student = login(client, "S1")
    for path in ("/api/admin/seat-blocks", "/api/admin/rooms/R1/impact"):
        denied = client.get(path, headers=student)
        assert denied.status_code == 403
    denied_bulk = client.post(
        "/api/admin/seats/bulk",
        headers=student,
        json={"room_id": "R2", "seats": [{"id": "R2-601", "capacity": 1, "type": "standard"}]},
    )
    assert denied_bulk.status_code == 403
    client.cookies.clear()
    anonymous = client.get("/api/admin/seat-blocks")
    assert anonymous.status_code == 401


# ---------------------------------------------------------------------------
# 安全整改测试用例 B-SEC-001 ～ B-SEC-025
# 对应《07-B模块安全整改测试用例.md》：M-04 / M-05 / L-07 / L-08
# ---------------------------------------------------------------------------


def _seat_obj(status: str = "available", marker: bool = False, seat_id: str = "R1-001") -> Seat:
    return Seat(id=seat_id, room_id="R1", admin_status=status, maintenance_by_block=marker)


class _FrozenNow(datetime):
    """把路由层的 ``datetime.now()`` 固定到夹具时间 ``NOW``，避免真实时钟漂移。"""

    @classmethod
    def now(cls, tz=None):  # type: ignore[override]
        return NOW


@pytest.fixture
def frozen_routes(monkeypatch: pytest.MonkeyPatch):
    """API 测试专用：冻结 admin/resource 路由模块里的 datetime.now()。"""
    monkeypatch.setattr(admin_routes_module, "datetime", _FrozenNow)
    monkeypatch.setattr(resource_routes_module, "datetime", _FrozenNow)
    return _FrozenNow


def _block_obj(start: datetime, end: datetime, *, seat_id: str = "R1-001") -> SeatBlock:
    return SeatBlock(
        id="SB-SEC", seat_id=seat_id, reason="检修",
        start_at=start, end_at=end, created_by="A1",
    )


def _set_reservation_status(env: Env, reservation_id: str, status: str) -> None:
    with env.ctx.db.transaction() as conn:
        conn.execute("UPDATE reservations SET status = ? WHERE id = ?", (status, reservation_id))


def _violation(reservation_id: str, *, status: str = "active", vtype: str = "manual_inspection") -> Violation:
    return Violation(
        id="V-SEC", reservation_id=reservation_id, user_id="S1",
        violation_type=vtype, points=5, status=status,
    )


# ---- B-SEC-001 封禁状态机单元 -------------------------------------------------
def test_b_sec_001_block_state_machine_unit() -> None:
    current = _block_obj(NOW - timedelta(hours=1), NOW + timedelta(hours=1))
    future = _block_obj(NOW + timedelta(hours=1), NOW + timedelta(hours=2))

    assert resource_rules.seat_state_after_block(_seat_obj(), current, NOW) == ("maintenance", True)
    # 未来封禁不立即改状态，只打标记
    assert resource_rules.seat_state_after_block(_seat_obj(), future, NOW) == ("available", True)
    # 手工维护、停用都不改状态
    assert resource_rules.seat_state_after_block(_seat_obj("maintenance"), current, NOW) == ("maintenance", False)
    assert resource_rules.seat_state_after_block(_seat_obj("disabled"), current, NOW) == ("disabled", False)
    # 多封禁未全部关闭不恢复；全部关闭才恢复
    assert resource_rules.seat_state_after_blocks_closed(_seat_obj("maintenance", True), [current]) == ("maintenance", True)
    assert resource_rules.seat_state_after_blocks_closed(_seat_obj("maintenance", True), []) == ("available", False)


# ---- B-SEC-002 批量建座 ID/数量规则单元 ---------------------------------------
def test_b_sec_002_bulk_rules_unit() -> None:
    assert resource_rules.bulk_limit_violation(1) is None
    assert resource_rules.bulk_limit_violation(100) is None
    assert resource_rules.bulk_limit_violation(0) is not None
    assert resource_rules.bulk_limit_violation(101) is not None

    assert resource_rules.duplicate_ids(["A101-001", "A101-001"]) == ["A101-001"]
    assert resource_rules.duplicate_ids(["A101-001", "A101-002"]) == []
    assert resource_rules.duplicate_ids([]) == []


# ---- B-SEC-003 现场违约重复登记判定单元 --------------------------------------
def test_b_sec_003_manual_violation_dedupe_unit() -> None:
    assert resource_rules.is_duplicate_manual_violation([_violation("RS-1")], "RS-1") is True
    assert resource_rules.is_duplicate_manual_violation([_violation("RS-1")], "RS-2") is False
    assert resource_rules.is_duplicate_manual_violation([_violation("RS-1", vtype="no_show")], "RS-1") is False
    assert resource_rules.is_duplicate_manual_violation([_violation("RS-1", status="revoked")], "RS-1") is False
    # 申诉中仍视为生效，不允许重复登记
    assert resource_rules.is_duplicate_manual_violation([_violation("RS-1", status="appealing")], "RS-1") is True


# ---- B-SEC-004 资源停用影响预约的策略单元 ------------------------------------
def test_b_sec_004_resource_release_policy_unit() -> None:
    for status in ("pending", "in_use", "away"):
        assert resource_rules.should_cancel_for_resource(status, "closed") == "resource_unavailable"
        assert resource_rules.should_cancel_for_resource(status, "disabled") == "resource_unavailable"
        assert resource_rules.should_cancel_for_resource(status, "maintenance") == "resource_unavailable"
    for status in ("completed", "cancelled", "no_show", "released"):
        assert resource_rules.should_cancel_for_resource(status, "closed") is None
    # 目标状态不是“不可用”时不动
    assert resource_rules.should_cancel_for_resource("pending", "open") is None


# ---- B-SEC-005 座位视图状态单元 ----------------------------------------------
def test_b_sec_005_seat_views_state_unit(env: Env) -> None:
    _set_seat(env, "R1-001", admin_status="disabled")
    _set_seat(env, "R1-002", admin_status="maintenance", maintenance_by_block=False)
    env.block_seat(
        "A1", "R1-003", "临时检修",
        datetime(2026, 5, 11, 9, 0), datetime(2026, 5, 11, 10, 0), now=NOW,
    )
    env.create("S1", "R1-004", time(9, 0), time(10, 0))
    with env.ctx.db.read() as conn:
        views = {v.seat.id: v for v in SqliteRepository(conn).seat_views(D, 18, 20, room_id="R1")}
    assert views["R1-001"].state == "disabled"
    assert views["R1-002"].state == "maintenance"
    assert views["R1-003"].state == "maintenance"  # 封禁在时段内生效
    assert views["R1-004"].state == "reserved"
    assert views["R1-005"].state == "available"

    # 房间关闭后，任何座位都不得再显示 available
    with env.ctx.db.transaction() as conn:
        conn.execute("UPDATE rooms SET status = 'closed' WHERE id = 'R1'")
    with env.ctx.db.read() as conn:
        closed = SqliteRepository(conn).seat_views(D, 18, 20, room_id="R1")
    assert all(v.state != "available" and not v.available for v in closed)


# ---- B-SEC-006 关闭房间 API 与影响预览 ---------------------------------------
def test_b_sec_006_close_room_api_and_impact(client: TestClient, env: Env, frozen_routes) -> None:
    item = env.create("S1", "R1-001", time(9, 0), time(10, 0)).data
    headers = login(client, "A1")

    impact = client.get("/api/admin/rooms/R1/impact", headers=headers)
    assert impact.status_code == 200, impact.text
    assert impact.json()["data"]["affected_count"] >= 1
    assert env.reservation(item["id"]).status == "pending"  # 预览只读

    closed = client.put("/api/admin/rooms/R1", headers=headers, json={"status": "closed"})
    assert closed.status_code == 200, closed.text
    assert item["id"] in closed.json()["data"]["released_reservations"]
    saved = env.reservation(item["id"])
    assert saved.status == "cancelled"
    assert saved.end_reason == "resource_unavailable"
    assert env.user("S1").credit == 100
    assert env.violations("S1") == []
    assert any(n.notification_type for n in env.notifications("S1"))
    assert any(log.action == "room_close_release" for log in env.audit())


# ---- B-SEC-007 停用座位 API 与预约处理 ---------------------------------------
def test_b_sec_007_disable_seat_api(client: TestClient, env: Env) -> None:
    item = env.create("S2", "R1-002", time(9, 0), time(10, 0)).data
    headers = login(client, "A1")

    disabled = client.put("/api/admin/seats/R1-002", headers=headers, json={"admin_status": "disabled"})
    assert disabled.status_code == 200, disabled.text
    assert item["id"] in disabled.json()["data"]["released_reservations"]
    assert env.reservation(item["id"]).end_reason == "resource_unavailable"
    assert _seat(env, "R1-002").admin_status == "disabled"
    assert env.user("S2").credit == 100
    assert any(log.action == "seat_unavailable_release" for log in env.audit())

    # 恢复可用后可以重新建预约
    again = client.put("/api/admin/seats/R1-002", headers=headers, json={"admin_status": "available"})
    assert again.status_code == 200
    assert env.create("S2", "R1-002", time(11, 0), time(12, 0)).ok


# ---- B-SEC-008 封禁/解封生命周期 API -----------------------------------------
def test_b_sec_008_block_lifecycle_api(client: TestClient, env: Env, frozen_routes) -> None:
    item = env.create("S1", "R1-003", time(9, 0), time(10, 0)).data
    headers = login(client, "A1")

    created = client.post(
        "/api/admin/seat-blocks",
        headers=headers,
        json={
            "seat_id": "R1-003",
            "reason": "座椅损坏",
            "start_at": "2026-05-11T08:00:00",
            "end_at": "2026-05-11T10:00:00",
        },
    )
    assert created.status_code == 201, created.text
    payload = created.json()["data"]
    assert item["id"] in payload["affected_reservations"]
    assert env.reservation(item["id"]).end_reason == "seat_blocked"
    block_id = payload["block"]["id"]

    deleted = client.delete("/api/admin/seat-blocks/R1-003", headers=headers)
    assert deleted.status_code == 200, deleted.text
    assert deleted.json()["data"]["restored"] is True
    repeated = client.post(
        f"/api/admin/seat-blocks/{block_id}/release", headers=headers, json={"reason": "重复解封"}
    )
    assert repeated.status_code == 400
    assert repeated.json()["error"]["rule"] == "ST-12"


# ---- B-SEC-009 批量建座 API 全量成功/回滚 ------------------------------------
def test_b_sec_009_bulk_api_limits(client: TestClient, env: Env) -> None:
    headers = login(client, "A1")
    hundred = client.post(
        "/api/admin/seats/bulk",
        headers=headers,
        json={
            "room_id": "R2",
            "seats": [
                {"id": f"R2-9{index:02d}", "type": "standard", "capacity": 1}
                for index in range(100)
            ],
        },
    )
    assert hundred.status_code == 201, hundred.text
    assert hundred.json()["data"]["count"] == 100

    over = client.post(
        "/api/admin/seats/bulk",
        headers=headers,
        json={
            "room_id": "R2",
            "seats": [
                {"id": f"R2-8{index:02d}", "type": "standard", "capacity": 1}
                for index in range(101)
            ],
        },
    )
    assert over.status_code == 422

    repeated = client.post(
        "/api/admin/seats/bulk",
        headers=headers,
        json={"room_id": "R2", "seats": [
            {"id": "R2-701", "type": "standard", "capacity": 1},
            {"id": "R2-701", "type": "standard", "capacity": 1},
        ]},
    )
    assert repeated.status_code == 409


# ---- B-SEC-010 重复现场违约 API ----------------------------------------------
def test_b_sec_010_duplicate_manual_violation_api(client: TestClient, env: Env) -> None:
    item = env.create("S3", "R1-004", time(9, 0), time(10, 0)).data
    _set_reservation_status(env, item["id"], "in_use")
    headers = login(client, "A1")

    first = client.post(
        "/api/admin/manual-violation",
        headers=headers,
        json={"reservation_id": item["id"], "mode": "warn", "note": "现场核实：桌面留物"},
    )
    assert first.status_code == 200, first.text
    assert env.user("S3").credit == 95

    second = client.post(
        "/api/admin/manual-violation",
        headers=headers,
        json={"reservation_id": item["id"], "mode": "warn", "note": "现场核实：桌面留物"},
    )
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "DUPLICATE_VIOLATION"
    assert env.user("S3").credit == 95
    assert len([v for v in env.violations("S3") if v.violation_type == "manual_inspection"]) == 1
    assert any(log.action == "manual_violation_duplicate" for log in env.audit())


# ---- B-SEC-011 关闭房间对 pending/in_use/away 的处理 -------------------------
def test_b_sec_011_close_room_releases_three_statuses(env: Env) -> None:
    pending = env.create("S1", "R1-001", time(9, 0), time(10, 0)).data
    in_use = env.create("S2", "R1-002", time(9, 0), time(10, 0)).data
    away = env.create("S3", "R1-003", time(9, 0), time(10, 0)).data
    _set_reservation_status(env, in_use["id"], "in_use")
    _set_reservation_status(env, away["id"], "away")

    with env.ctx.engine() as engine:
        engine.update_room(
            env.actor("A1"), "R1", name=None, location=None, status="closed",
            open_time=None, close_time=None, now=NOW,
        )
        affected = engine.release_affected_reservations("R1", None, "自习室停用", env.actor("A1"), NOW)

    assert set(affected) == {pending["id"], in_use["id"], away["id"]}
    for reservation_id in affected:
        saved = env.reservation(reservation_id)
        assert saved.status == "cancelled"
        assert saved.end_reason == "resource_unavailable"
    assert all(env.user(user).credit == 100 for user in ("S1", "S2", "S3"))
    assert env.violations("S1") == [] and env.violations("S2") == [] and env.violations("S3") == []


# ---- B-SEC-012 座位 disabled/maintenance 对预约的处理 ------------------------
def test_b_sec_012_seat_unavailable_releases_and_differs_from_block(env: Env) -> None:
    item = env.create("S1", "R1-005", time(9, 0), time(10, 0)).data
    with env.ctx.engine() as engine:
        engine.update_seat(
            env.actor("A1"), "R1-005", room_id=None, seat_type=None, capacity=None,
            admin_status="maintenance", has_power=None, is_window=None, note=None, now=NOW,
        )
        affected = engine.release_affected_reservations(None, "R1-005", "座位维护", env.actor("A1"), NOW)
    assert affected == [item["id"]]
    assert env.reservation(item["id"]).end_reason == "resource_unavailable"
    seat = _seat(env, "R1-005")
    assert seat.admin_status == "maintenance"
    assert seat.maintenance_by_block is False  # 手工维护：不写封禁标记
    assert env.user("S1").credit == 100

    # 停用座位即便曾被封禁管理，解封后也不会自动恢复
    _set_seat(env, "R1-005", admin_status="disabled", maintenance_by_block=False)
    with env.ctx.engine() as engine:
        state = resource_rules.seat_state_after_blocks_closed(_seat(env, "R1-005"), [])
    assert state == ("disabled", False)


# ---- B-SEC-013 重复关闭/停用幂等 ---------------------------------------------
def test_b_sec_013_repeat_close_is_idempotent(client: TestClient, env: Env, frozen_routes) -> None:
    item = env.create("S1", "R1-001", time(9, 0), time(10, 0)).data
    headers = login(client, "A1")

    first = client.put("/api/admin/rooms/R1", headers=headers, json={"status": "closed"})
    assert first.status_code == 200
    assert first.json()["data"]["released_reservations"] == [item["id"]]
    audit_after_first = len(env.audit())

    second = client.put("/api/admin/rooms/R1", headers=headers, json={"status": "closed"})
    assert second.status_code == 200
    assert second.json()["data"]["released_reservations"] == []
    assert env.reservation(item["id"]).status == "cancelled"
    # 第二次只产生 room_update + room_close_release(noop) 两条审计，不重复取消
    logs = env.audit()
    assert len(logs) == audit_after_first + 2
    release_logs = [log for log in logs if log.action == "room_close_release"]
    assert any(log.after.get("result") == "noop" for log in release_logs)
    assert any(log.after.get("result") == "success" for log in release_logs)


# ---- B-SEC-014 停用影响预览无副作用 -------------------------------------------
def test_b_sec_014_impact_preview_has_no_side_effect(client: TestClient, env: Env) -> None:
    item = env.create("S1", "R1-001", time(9, 0), time(10, 0)).data
    headers = login(client, "A1")
    before = (len(env.audit()), len(env.notifications("S1")), env.reservation(item["id"]).status)

    for _ in range(2):
        response = client.get("/api/admin/rooms/R1/impact", headers=headers, params={"status": "closed"})
        assert response.status_code == 200
        assert response.json()["data"]["affected_count"] >= 1

    after = (len(env.audit()), len(env.notifications("S1")), env.reservation(item["id"]).status)
    assert before == after


# ---- B-SEC-015 封禁到期/手工维护/停用恢复矩阵 --------------------------------
def test_b_sec_015_restore_matrix(env: Env) -> None:
    window = dict(now=NOW)
    env.block_seat("A1", "R1-001", "检修", NOW - timedelta(hours=1), NOW + timedelta(hours=1), **window)
    _set_seat(env, "R1-002", admin_status="maintenance", maintenance_by_block=False)
    env.block_seat("A1", "R1-002", "检修", NOW - timedelta(hours=1), NOW + timedelta(hours=1), **window)
    _set_seat(env, "R1-003", admin_status="disabled", maintenance_by_block=False)
    env.block_seat("A1", "R1-003", "检修", NOW - timedelta(hours=1), NOW + timedelta(hours=1), **window)
    # 多封禁：只关一条不恢复
    first = env.block_seat(
        "A1", "R1-004", "第一段", NOW - timedelta(hours=1), NOW + timedelta(hours=3), **window
    )
    env.block_seat("A1", "R1-004", "第二段", NOW - timedelta(hours=1), NOW + timedelta(hours=4), **window)
    _release(env, first.data["block"]["id"], restore=True)
    assert _seat(env, "R1-004").admin_status == "maintenance"

    env.sweep(now=NOW + timedelta(minutes=90))
    assert _seat(env, "R1-001").admin_status == "available"      # 封禁管理 → 恢复
    assert _seat(env, "R1-002").admin_status == "maintenance"    # 手工维护 → 保持
    assert _seat(env, "R1-003").admin_status == "disabled"       # 停用 → 保持
    assert _seat(env, "R1-004").admin_status == "maintenance"    # 仍有未释放封禁 → 保持


# ---- B-SEC-016 资源操作角色权限 ----------------------------------------------
def test_b_sec_016_resource_permission_matrix(client: TestClient, env: Env) -> None:
    env.create("S1", "R1-001", time(9, 0), time(10, 0))
    for name in ("S1", "T1"):
        headers = login(client, name)
        assert client.put("/api/admin/rooms/R1", headers=headers, json={"status": "closed"}).status_code == 403
        assert client.put("/api/admin/seats/R1-001", headers=headers, json={"admin_status": "disabled"}).status_code == 403
        assert client.post("/api/admin/seats/bulk", headers=headers, json={
            "room_id": "R2", "seats": [{"id": "R2-777", "type": "standard", "capacity": 1}]
        }).status_code == 403
        assert client.get("/api/admin/rooms/R1/impact", headers=headers).status_code == 403
    assert env.reservation(env.create("S2", "R1-002", time(11, 0), time(12, 0)).data["id"]).status == "pending"

    sys_headers = login(client, "Y1")
    assert client.get("/api/admin/rooms/R1/impact", headers=sys_headers).status_code == 200
    room_headers = login(client, "A1")
    assert client.put("/api/admin/rooms/R1", headers=room_headers, json={"status": "closed"}).status_code == 200


# ---- B-SEC-017 资源 mutation CSRF --------------------------------------------
def test_b_sec_017_resource_mutations_require_csrf(client: TestClient, env: Env) -> None:
    login(client, "A1")
    payload = {"room_id": "R2", "seats": [{"id": "R2-888", "type": "standard", "capacity": 1}]}
    missing = client.post("/api/admin/seats/bulk", json=payload)
    assert missing.status_code == 403
    assert missing.json()["error"]["code"] == "CSRF_FAILED"
    wrong = client.post("/api/admin/seats/bulk", headers={"X-CSRF-Token": "wrong"}, json=payload)
    assert wrong.status_code == 403
    assert _seat(env, "R2-888") is None  # 无数据变化

    right = client.post("/api/admin/seats/bulk", headers={"X-CSRF-Token": client.cookies.get("seat_csrf")}, json=payload)
    assert right.status_code == 201
    assert _seat(env, "R2-888") is not None


# ---- B-SEC-018 关闭房间座位显示与 holder ID ----------------------------------
def test_b_sec_018_closed_room_and_holder_minimization(client: TestClient, env: Env, frozen_routes) -> None:
    occupied = env.create("S1", "R2-001", time(9, 0), time(10, 0)).data
    admin = login(client, "A1")
    admin_view = client.get("/api/seats", headers=admin, params={"date": D.isoformat(), "start": "09:00", "end": "10:00"})
    assert admin_view.status_code == 200
    by_id = {item["id"]: item for item in admin_view.json()["data"]["items"]}
    assert by_id["R2-001"]["holder_reservation_id"] == occupied["id"]  # 管理员可用

    student = login(client, "S2")  # 非持有人的学生
    student_view = client.get("/api/seats", headers=student, params={"date": D.isoformat(), "start": "09:00", "end": "10:00"})
    assert student_view.status_code == 200
    for item in student_view.json()["data"]["items"]:
        assert item["holder_reservation_id"] is None  # 学生不下发他人预约 ID

    admin = login(client, "A1")
    client.put("/api/admin/rooms/R2", headers=admin, json={"status": "closed"})
    closed_view = client.get("/api/seats", headers=admin, params={"date": D.isoformat(), "start": "09:00", "end": "10:00"})
    for item in closed_view.json()["data"]["items"]:
        if item["room_id"] == "R2":
            assert item["available"] is False
            assert item["state"] != "available"


# ---- B-SEC-019 批量建座跨房间/重复 ID ----------------------------------------
def test_b_sec_019_bulk_cross_room_id_rejected(client: TestClient, env: Env) -> None:
    headers = login(client, "A1")
    response = client.post(
        "/api/admin/seats/bulk",
        headers=headers,
        json={"room_id": "R2", "seats": [{"id": "R1-001", "type": "standard", "capacity": 1}]},
    )
    assert response.status_code == 409
    assert _seat(env, "R1-001").room_id == "R1"  # 原座位未被“搬走”


# ---- B-SEC-020 非法封禁时间边界 ----------------------------------------------
def test_b_sec_020_invalid_block_window(client: TestClient, env: Env, frozen_routes) -> None:
    headers = login(client, "A1")
    for start_at, end_at in (
        ("2026-05-11T09:00:00", "2026-05-11T09:00:00"),
        ("2026-05-11T10:00:00", "2026-05-11T09:00:00"),
    ):
        response = client.post(
            "/api/admin/seat-blocks",
            headers=headers,
            json={"seat_id": "R1-001", "reason": "非法时间窗", "start_at": start_at, "end_at": end_at},
        )
        assert response.status_code in (400, 422)
        assert "SQL" not in response.text.upper()
    assert _active_blocks(env, "R1-001") == []
    # 合法当前封禁仍然成功
    ok = client.post(
        "/api/admin/seat-blocks",
        headers=headers,
        json={
            "seat_id": "R1-001", "reason": "合法封禁",
            "start_at": "2026-05-11T08:00:00", "end_at": "2026-05-11T09:00:00",
        },
    )
    assert ok.status_code == 201


# ---- B-SEC-021 批量建座异常回滚 ----------------------------------------------
def test_b_sec_021_bulk_rollback(client: TestClient, env: Env) -> None:
    headers = login(client, "A1")
    response = client.post(
        "/api/admin/seats/bulk",
        headers=headers,
        json={"room_id": "R2", "seats": [
            {"id": "R2-611", "type": "standard", "capacity": 1},
            {"id": "R2-612", "type": "discussion", "capacity": 3},  # 讨论间容量必须 4~8
        ]},
    )
    assert response.status_code in (400, 422)
    assert _seat(env, "R2-611") is None
    assert _seat(env, "R2-612") is None


# ---- B-SEC-022 数据库约束与迁移 ----------------------------------------------
def test_b_sec_022_db_constraints_and_migration(env: Env) -> None:
    assert env.ctx.db.schema_version() == 4
    with env.ctx.db.read() as conn:
        seats = int(conn.execute("SELECT COUNT(*) AS c FROM seats").fetchone()["c"])
        reservations = int(conn.execute("SELECT COUNT(*) AS c FROM reservations").fetchone()["c"])
    assert seats == 8 and reservations >= 1  # 旧数据保留

    with env.ctx.db.transaction() as conn:
        # 孤儿 reservation_slots（与父预约不一致）被触发器拒绝
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO reservation_slots(reservation_id, seat_id, user_id, date, slot) "
                "VALUES ('RS-404', 'R1-001', 'S1', ?, 18)",
                (D.isoformat(),),
            )
        # 非法 waitlist 时段被 CHECK 拒绝
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO waitlist(id, user_id, seat_id, date, start_slot, end_slot, status, seq, created_at) "
                "VALUES ('W-1', 'S1', 'R1-001', ?, 60, 61, 'queued', 1, ?)",
                (D.isoformat(), "2026-05-11 08:00:00"),
            )
        # 重复座位编号被主键拒绝
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO seats(id, room_id, seat_type, capacity, admin_status, has_power, is_window, note, created_at) "
                "VALUES ('R1-001', 'R1', 'standard', 1, 'available', 0, 0, '', ?)",
                ("2026-05-11 08:00:00",),
            )


# ---- B-SEC-022b 旧 v2 库升级到 v4（数据保留 + 历史脏数据清理）----------------
def test_b_sec_022b_existing_v2_database_upgrades_to_v4(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    import uuid
    from pathlib import Path

    import seat_reservation.storage.database as database_module
    from seat_reservation.storage.database import Database
    from seat_reservation.storage.migrations import MIGRATIONS

    path = Path(env.ctx.settings.db_path).parent / f"upgrade-{uuid.uuid4().hex[:8]}.db"
    legacy = Database(path)
    # ① 只应用 v1 + v2，模拟整改前的旧库
    monkeypatch.setattr(database_module, "MIGRATIONS", MIGRATIONS[:2])
    assert legacy.migrate() == [1, 2]
    with legacy.transaction() as conn:
        conn.execute(
            "INSERT INTO users(id, name, role, password_hash, created_at, updated_at) "
            "VALUES ('S9', '旧用户', 'student', 'x', '2026-05-11 00:00:00', '2026-05-11 00:00:00')"
        )
        conn.execute("INSERT INTO rooms(id, name, created_at) VALUES ('R9', '旧房间', '2026-05-11 00:00:00')")
        conn.execute("INSERT INTO seats(id, room_id, created_at) VALUES ('R9-001', 'R9', '2026-05-11 00:00:00')")
        # 历史脏数据：越界 waitlist 时段、孤儿投诉外键
        conn.execute(
            "INSERT INTO waitlist(id, user_id, seat_id, date, start_slot, end_slot, status, seq, created_at) "
            "VALUES ('W-9', 'S9', 'R9-001', '2026-05-11', 60, 61, 'queued', 1, '2026-05-11 00:00:00')"
        )
        conn.execute(
            "INSERT INTO complaints(id, user_id, seat_id, type, content, status, created_at) "
            "VALUES ('C-9', 'S9', 'MISSING', 'noise', '历史孤儿投诉', 'pending', '2026-05-11 00:00:00')"
        )

    # ② 恢复完整迁移链，升级到 v4
    monkeypatch.setattr(database_module, "MIGRATIONS", MIGRATIONS)
    assert legacy.migrate() == [4]
    assert legacy.schema_version() == 4
    with legacy.read() as conn:
        slot = conn.execute("SELECT start_slot, end_slot FROM waitlist WHERE id = 'W-9'").fetchone()
        complaint = conn.execute("SELECT seat_id FROM complaints WHERE id = 'C-9'").fetchone()
        seats = int(conn.execute("SELECT COUNT(*) AS c FROM seats").fetchone()["c"])
    assert (slot["start_slot"], slot["end_slot"]) == (47, 48)  # 越界时段被裁剪到合法范围
    assert complaint["seat_id"] is None                        # 孤儿外键被置空
    assert seats == 1                                          # 旧数据保留


# ---- B-SEC-023 B+C 资源关闭不误判 no_show ------------------------------------
def test_b_sec_023_close_room_does_not_become_no_show(client: TestClient, env: Env, frozen_routes) -> None:
    item = env.create("S1", "R1-001", time(9, 0), time(10, 0)).data
    headers = login(client, "A1")
    closed = client.put("/api/admin/rooms/R1", headers=headers, json={"status": "closed"})
    assert closed.status_code == 200
    assert env.reservation(item["id"]).status == "cancelled"

    summary = env.sweep(now=datetime(2026, 5, 11, 9, 16))
    assert summary["no_show"] == 0
    assert summary["resource_unavailable"] == 0  # 已被资源变更处理，扫描不再重复
    saved = env.reservation(item["id"])
    assert saved.status == "cancelled"
    assert saved.end_reason == "resource_unavailable"
    assert env.user("S1").credit == 100
    assert env.violations("S1") == []


# ---- B-SEC-024 B+共享认证：停用账号不能管理资源 ------------------------------
def test_b_sec_024_disabled_admin_cannot_mutate_resources(ctx) -> None:
    app_one = create_app(Settings.for_tests(ctx.settings.db_path))
    app_two = create_app(Settings.for_tests(ctx.settings.db_path))
    with TestClient(app_one) as admin_client, TestClient(app_two) as sys_client:
        login(admin_client, "A1")
        sys_headers = login(sys_client, "Y1")
        disabled = sys_client.put(
            "/api/admin/users/A1/status",
            headers=sys_headers,
            json={"status": "disabled", "reason": "安全整改测试：停用管理员"},
        )
        assert disabled.status_code == 200, disabled.text
        # 带 CSRF 头，确保先走到认证而不是被 CSRF 中间件拦下
        old_session_headers = {"X-CSRF-Token": admin_client.cookies.get("seat_csrf") or ""}
        denied = admin_client.put(
            "/api/admin/rooms/R1", headers=old_session_headers, json={"status": "closed"}
        )
        assert denied.status_code == 401
        assert sys_client.get("/api/admin/rooms", headers=sys_headers).status_code == 200


# ---- B-SEC-025 B+D 资源状态与统计/导出一致 -----------------------------------
def test_b_sec_025_analytics_and_export_after_resource_change(client: TestClient, env: Env, frozen_routes) -> None:
    item = env.create("S1", "R1-001", time(9, 0), time(10, 0)).data
    admin = login(client, "A1")
    client.put("/api/admin/rooms/R1", headers=admin, json={"status": "closed"})
    assert env.reservation(item["id"]).status == "cancelled"

    sys_headers = login(client, "Y1")
    analytics = client.get("/api/admin/analytics", headers=sys_headers)
    assert analytics.status_code == 200, analytics.text
    export = client.get("/api/admin/export/reservations.csv", headers=sys_headers)
    assert export.status_code == 200
    assert "R1-001" in export.text  # 导出包含被取消的预约归属座位


