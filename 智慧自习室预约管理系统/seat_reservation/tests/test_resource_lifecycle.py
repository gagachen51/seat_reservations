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

from datetime import datetime, time, timedelta

import pytest
from fastapi.testclient import TestClient

from seat_reservation.api.app import create_app
from seat_reservation.config import Settings
from seat_reservation.domain.models import SeatBlock
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
    assert env.ctx.db.schema_version() == 2
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
