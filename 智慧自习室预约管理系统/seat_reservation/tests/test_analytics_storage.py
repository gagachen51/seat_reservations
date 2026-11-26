"""D 模块查询层/仓储层测试（不修改共享夹具）。"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from seat_reservation.domain.models import (
    AuditLog,
    BlacklistRecord,
    Complaint,
    Reservation,
    ReservationStatus,
    Review,
    Violation,
    WaitlistEntry,
)
from seat_reservation.domain.params import ParamSet
from seat_reservation.domain.timeutil import datetime_at
from seat_reservation.storage.repository import SqliteRepository
from tests.helpers import D, NOW


def _add_reservation(
    repo: SqliteRepository,
    *,
    reservation_id: str,
    user_id: str,
    seat_id: str,
    day: date,
    start_slot: int,
    end_slot: int,
    status: str,
    checked_in: bool = False,
    checked_out: bool = False,
) -> Reservation:
    start_at = datetime_at(day, start_slot)
    end_at = datetime_at(day, end_slot)
    reservation = Reservation(
        id=reservation_id,
        user_id=user_id,
        seat_id=seat_id,
        day=day,
        start_slot=start_slot,
        end_slot=end_slot,
        start_at=start_at,
        end_at=end_at,
        status=status,
        created_at=start_at - timedelta(days=1),
        updated_at=start_at,
        checked_in_at=start_at + timedelta(minutes=5) if checked_in else None,
        checked_out_at=end_at - timedelta(minutes=5) if checked_out else None,
        policy=ParamSet().snapshot(),
    )
    repo.insert_reservation(reservation)
    if status in {
        ReservationStatus.PENDING.value,
        ReservationStatus.IN_USE.value,
        ReservationStatus.AWAY.value,
    }:
        repo.occupy(reservation)
    return reservation


@pytest.fixture
def analytics_data(ctx):
    """在现有 seed_fixture 上补 3 天跨房间数据；不改 tests/helpers.py。"""
    with ctx.db.transaction() as conn:
        repo = SqliteRepository(conn)

        _add_reservation(
            repo,
            reservation_id="R-D2-0001",
            user_id="S1",
            seat_id="R1-001",
            day=D - timedelta(days=2),
            start_slot=18,
            end_slot=20,
            status=ReservationStatus.COMPLETED.value,
            checked_in=True,
            checked_out=True,
        )
        no_show_reservation = _add_reservation(
            repo,
            reservation_id="R-D1-0001",
            user_id="S2",
            seat_id="R1-002",
            day=D - timedelta(days=1),
            start_slot=20,
            end_slot=22,
            status=ReservationStatus.NO_SHOW.value,
        )
        _add_reservation(
            repo,
            reservation_id="R-D-0001",
            user_id="S3",
            seat_id="R2-001",
            day=D,
            start_slot=18,
            end_slot=20,
            status=ReservationStatus.COMPLETED.value,
            checked_in=True,
            checked_out=True,
        )
        _add_reservation(
            repo,
            reservation_id="R-D-0002",
            user_id="S5",
            seat_id="R1-003",
            day=D,
            start_slot=24,
            end_slot=26,
            status=ReservationStatus.CANCELLED.value,
        )
        _add_reservation(
            repo,
            reservation_id="R-D-0003",
            user_id="S1",
            seat_id="R2-002",
            day=D,
            start_slot=28,
            end_slot=30,
            status=ReservationStatus.PENDING.value,
        )

        repo.insert_violation(
            Violation(
                id="VIO-TEST-0001",
                reservation_id=no_show_reservation.id,
                user_id="S2",
                violation_type="no_show",
                points=10,
                status="active",
                occurred_at=datetime_at(D - timedelta(days=1), 20) + timedelta(minutes=16),
                note="测试未签到",
                created_at=datetime_at(D - timedelta(days=1), 20) + timedelta(minutes=16),
            )
        )

        repo.insert_review(
            Review(
                id="RV-TEST-0001",
                reservation_id="R-D2-0001",
                user_id="S1",
                seat_id="R1-001",
                rating=5,
                tags=["安静"],
                content="很好",
                created_at=datetime_at(D - timedelta(days=2), 22),
            )
        )
        repo.insert_review(
            Review(
                id="RV-TEST-0002",
                reservation_id="R-D-0001",
                user_id="S3",
                seat_id="R2-001",
                rating=3,
                tags=[],
                content="一般",
                created_at=datetime_at(D, 22),
            )
        )

        repo.insert_complaint(
            Complaint(
                id="CP-TEST-0001",
                user_id="S1",
                complaint_type="facility",
                content="R1 插座没电",
                seat_id="R1-005",
                status="pending",
                created_at=datetime_at(D, 20),
            )
        )
        repo.insert_complaint(
            Complaint(
                id="CP-TEST-0002",
                user_id="S2",
                complaint_type="noise",
                content="R2 有人喧哗",
                seat_id="R2-001",
                status="resolved",
                feedback="已现场提醒",
                resolved_by="A1",
                resolved_at=datetime_at(D - timedelta(days=1), 22),
                created_at=datetime_at(D - timedelta(days=1), 18),
            )
        )
        repo.insert_complaint(
            Complaint(
                id="CP-TEST-0003",
                user_id="S3",
                complaint_type="facility",
                content="R2 桌面损坏",
                seat_id="R2-002",
                status="rated",
                feedback="已更换",
                resolved_by="A1",
                resolved_at=datetime_at(D, 24),
                rating=4,
                rating_comment="处理及时",
                created_at=datetime_at(D, 20),
            )
        )

        for index, (user_id, seat_id, status) in enumerate(
            (
                ("S1", "R1-004", "queued"),
                ("S2", "R1-004", "notified"),
                ("S3", "R1-004", "confirmed"),
                ("S5", "R2-001", "queued"),
            ),
            start=1,
        ):
            repo.insert_waitlist(
                WaitlistEntry(
                    id=f"W-TEST-{index:04d}",
                    user_id=user_id,
                    seat_id=seat_id,
                    day=D,
                    start_slot=32,
                    end_slot=34,
                    status=status,
                    seq=index,
                    created_at=datetime_at(D, 16) + timedelta(minutes=index),
                    notified_at=datetime_at(D, 16) + timedelta(minutes=5) if status == "notified" else None,
                    confirm_deadline=datetime_at(D, 16) + timedelta(minutes=10) if status == "notified" else None,
                )
            )

        repo.insert_blacklist(
            BlacklistRecord(
                id="BL-TEST-0001",
                user_id="S3",
                reason="测试拉黑",
                source="manual",
                start_at=datetime_at(D - timedelta(days=1), 16),
                end_at=datetime_at(D + timedelta(days=2), 16),
                created_by="A1",
                created_at=datetime_at(D - timedelta(days=1), 16),
            )
        )
        repo.insert_credit_log(
            log_id="CL-TEST-0001",
            user_id="S1",
            delta=1,
            balance_after=101,
            reason="测试加分",
            violation_id=None,
            operator_id="A1",
            created_at=datetime_at(D, 18),
        )
        repo.insert_audit(
            AuditLog(
                id="A-TEST-0001",
                actor_id="A1",
                actor_role="room_admin",
                action="test_export",
                target_type="system",
                target_id="test",
                ip="127.0.0.1",
                created_at=datetime_at(D, 16),
            )
        )
    return ctx


def test_analytics_range_kpi_daily_and_sections(analytics_data):
    conn = analytics_data.db.connect()
    try:
        repo = SqliteRepository(conn)
        result = repo.analytics_range(
            date_from=D - timedelta(days=2), date_to=D, room_id=None
        )
    finally:
        conn.close()

    assert result["range"]["days"] == 3
    kpi = result["kpi"]
    assert kpi["reservations"] == 6
    assert kpi["valid_reservations"] == 5
    assert kpi["pending"] == 1
    assert kpi["completed"] == 2
    assert kpi["no_show"] == 2
    assert kpi["cancelled"] == 1
    assert kpi["released"] == 0
    assert kpi["checkins"] == 2
    assert kpi["checkin_base"] == 4
    assert kpi["attendance_base"] == 4
    assert kpi["checkin_rate"] == 50.0
    assert kpi["completion_rate"] == 50.0
    assert kpi["no_show_rate"] == 50.0
    assert kpi["cancel_rate"] == 16.7
    assert kpi["avg_minutes"] == 60.0
    assert kpi["total_seats"] == 8
    assert result["metric_notes"]["booking_utilization"].startswith("预约时段利用率")

    daily = {item["date"]: item for item in result["daily"]}
    assert daily[(D - timedelta(days=2)).isoformat()]["reservations"] == 2
    assert daily[(D - timedelta(days=2)).isoformat()]["completed"] == 1
    assert daily[(D - timedelta(days=2)).isoformat()]["no_show"] == 1
    assert daily[(D - timedelta(days=1)).isoformat()]["reservations"] == 1
    assert daily[D.isoformat()]["reservations"] == 3
    assert daily[D.isoformat()]["completed"] == 1
    assert daily[D.isoformat()]["cancelled"] == 1
    assert daily[D.isoformat()]["checkins"] == 1

    assert {item["slot"] for item in result["peak_hours"]} >= {18, 20, 24, 28}
    assert result["violations"]["by_type"] == [{"type": "no_show", "count": 2}]
    assert result["ratings"]["total"] == 2
    assert result["ratings"]["average"] == 4.0
    assert result["ratings"]["distribution"] == [
        {"rating": 1, "count": 0},
        {"rating": 2, "count": 0},
        {"rating": 3, "count": 1},
        {"rating": 4, "count": 0},
        {"rating": 5, "count": 1},
    ]
    assert result["waitlist"]["total"] == 4
    assert result["waitlist"]["waiting"] == 3
    assert result["waitlist"]["confirmed_rate"] == 25.0
    status_counts = {item["status"]: item["count"] for item in result["waitlist"]["by_status"]}
    assert status_counts == {
        "queued": 2,
        "notified": 1,
        "confirmed": 1,
        "expired": 0,
        "cancelled": 0,
    }
    assert result["complaints"]["total"] == 3
    assert result["complaints"]["pending"] == 1
    assert result["complaints"]["completed"] == 2
    assert result["complaints"]["resolution_rate"] == 66.7
    assert result["complaints"]["average_hours"] == 2.0
    assert result["complaints"]["average_rating"] == 4.0

    rooms = {item["id"]: item for item in result["rooms"]}
    assert rooms["R1"]["reservations"] == 4
    assert rooms["R1"]["booked_slots"] == 6
    assert rooms["R1"]["capacity_slots"] == 576
    assert rooms["R1"]["booking_utilization"] == 1.0
    assert rooms["R2"]["booked_slots"] == 4
    assert rooms["R2"]["capacity_slots"] == 192
    assert rooms["R2"]["booking_utilization"] == 2.1


def test_analytics_room_filter_and_admin_stats_room_filter(analytics_data):
    conn = analytics_data.db.connect()
    try:
        repo = SqliteRepository(conn)
        result = repo.analytics_range(
            date_from=D - timedelta(days=2), date_to=D, room_id="R1"
        )
        stats_r1 = repo.admin_stats(NOW, "R1")
        stats_r2 = repo.admin_stats(NOW, "R2")
        stats_all = repo.admin_stats(NOW, None)
    finally:
        conn.close()

    assert result["kpi"]["reservations"] == 4
    assert result["kpi"]["completed"] == 1
    assert result["kpi"]["no_show"] == 2
    assert result["kpi"]["cancelled"] == 1
    assert result["kpi"]["checkins"] == 1
    assert result["kpi"]["valid_reservations"] == 3
    assert result["kpi"]["checkin_base"] == 3
    assert result["kpi"]["attendance_base"] == 3
    assert result["kpi"]["checkin_rate"] == 33.3
    assert result["kpi"]["completion_rate"] == 33.3
    assert result["kpi"]["no_show_rate"] == 66.7
    assert result["kpi"]["cancel_rate"] == 25.0
    assert result["kpi"]["total_seats"] == 6
    assert [room["id"] for room in result["rooms"]] == ["R1"]
    assert result["waitlist"]["total"] == 3
    assert result["waitlist"]["waiting"] == 2
    assert result["complaints"]["total"] == 1
    assert result["complaints"]["pending"] == 1
    assert result["complaints"]["resolution_rate"] == 0.0
    assert result["ratings"]["total"] == 1

    assert stats_r1["kpi"]["open_complaints"] == 1
    assert stats_r2["kpi"]["open_complaints"] == 0
    assert stats_all["kpi"]["open_complaints"] == 1
    assert stats_r1["kpi"]["waitlist"] == 2
    assert stats_r2["kpi"]["waitlist"] == 1
    assert stats_all["kpi"]["waitlist"] == 3


def test_admin_stats_room_filter_uses_reservation_relationship(analytics_data):
    """投诉没有 seat_id、只有 reservation_id 时，也要能按房间归属。"""
    with analytics_data.db.transaction() as conn:
        repo = SqliteRepository(conn)
        repo.insert_complaint(
            Complaint(
                id="CP-TEST-0004",
                user_id="S4",
                complaint_type="facility",
                content="R2 通过预约关联的投诉",
                seat_id=None,
                reservation_id="R-D-0001",
                status="pending",
                created_at=datetime_at(D, 26),
            )
        )

    conn = analytics_data.db.connect()
    try:
        repo = SqliteRepository(conn)
        stats_r1 = repo.admin_stats(NOW, "R1")
        stats_r2 = repo.admin_stats(NOW, "R2")
        stats_all = repo.admin_stats(NOW, None)
    finally:
        conn.close()

    assert stats_r1["kpi"]["open_complaints"] == 1
    assert stats_r2["kpi"]["open_complaints"] == 1
    assert stats_all["kpi"]["open_complaints"] == 2


def test_analytics_empty_range_returns_zeros(analytics_data):
    conn = analytics_data.db.connect()
    try:
        repo = SqliteRepository(conn)
        future = D + timedelta(days=10)
        result = repo.analytics_range(date_from=future, date_to=future + timedelta(days=1))
    finally:
        conn.close()

    assert result["kpi"]["reservations"] == 0
    assert result["kpi"]["valid_reservations"] == 0
    assert result["kpi"]["checkin_base"] == 0
    assert result["kpi"]["attendance_base"] == 0
    assert result["kpi"]["checkin_rate"] == 0.0
    assert result["kpi"]["completion_rate"] == 0.0
    assert result["kpi"]["no_show_rate"] == 0.0
    assert result["kpi"]["cancel_rate"] == 0.0
    assert result["kpi"]["checkins"] == 0
    assert result["kpi"]["average_rating"] is None
    assert len(result["daily"]) == 2
    assert all(item["reservations"] == 0 for item in result["daily"])
    assert result["waitlist"]["total"] == 0
    assert result["complaints"]["total"] == 0
    assert result["ratings"]["total"] == 0
    assert result["violations"]["by_type"] == []


def test_pre_checkin_released_not_in_checkin_rate(analytics_data):
    """签到前被管理员释放的 released 不进入签到率分母。"""
    with analytics_data.db.transaction() as conn:
        repo = SqliteRepository(conn)
        _add_reservation(
            repo,
            reservation_id="R-REL-0001",
            user_id="S4",
            seat_id="R1-005",
            day=D - timedelta(days=1),
            start_slot=28,
            end_slot=30,
            status=ReservationStatus.RELEASED.value,
        )

    conn = analytics_data.db.connect()
    try:
        repo = SqliteRepository(conn)
        result = repo.analytics_range(
            date_from=D - timedelta(days=2), date_to=D, room_id=None
        )
    finally:
        conn.close()

    kpi = result["kpi"]
    assert kpi["reservations"] == 7
    assert kpi["valid_reservations"] == 6
    assert kpi["released"] == 1
    assert kpi["checkins"] == 2
    assert kpi["no_show"] == 2
    # 2 次签到 + 2 次明确未签到；签到前 released 不进入分母
    assert kpi["checkin_base"] == 4
    assert kpi["checkin_rate"] == 50.0
    assert kpi["attendance_base"] == 4
    assert kpi["completion_rate"] == 50.0
    assert kpi["no_show_rate"] == 50.0
    assert kpi["cancel_rate"] == 14.3


def test_post_checkin_released_counts_in_checkin_rate(analytics_data):
    """签到后进入 released 的预约仍计入 checkins 和签到率分母。"""
    with analytics_data.db.transaction() as conn:
        repo = SqliteRepository(conn)
        _add_reservation(
            repo,
            reservation_id="R-REL-0002",
            user_id="S4",
            seat_id="R1-005",
            day=D - timedelta(days=1),
            start_slot=28,
            end_slot=30,
            status=ReservationStatus.RELEASED.value,
            checked_in=True,
        )

    conn = analytics_data.db.connect()
    try:
        repo = SqliteRepository(conn)
        result = repo.analytics_range(
            date_from=D - timedelta(days=2), date_to=D, room_id=None
        )
    finally:
        conn.close()

    kpi = result["kpi"]
    assert kpi["checkins"] == 3
    assert kpi["no_show"] == 2
    assert kpi["checkin_base"] == 5
    assert kpi["checkin_rate"] == 60.0
    # released 不进入完成/违约分母
    assert kpi["attendance_base"] == 4
    assert kpi["completion_rate"] == 50.0
    assert kpi["no_show_rate"] == 50.0
    assert kpi["cancel_rate"] == 14.3


def test_export_rows_existing_three_date_filters(analytics_data):
    conn = analytics_data.db.connect()
    try:
        repo = SqliteRepository(conn)
        headers, rows = repo.export_rows(
            "reservations",
            date_from=D - timedelta(days=1),
            date_to=D,
        )
        assert headers[0] == "预约号"
        assert len(rows) == 4

        headers, rows = repo.export_rows(
            "violations",
            date_from=D - timedelta(days=2),
            date_to=D - timedelta(days=2),
        )
        assert headers[0] == "违规号"
        assert len(rows) == 1

        headers, rows = repo.export_rows("violations", date_from=D - timedelta(days=1), date_to=D)
        assert len(rows) == 1

        headers, rows = repo.export_rows("audit", date_from=D, date_to=D)
        assert headers[0] == "日志号"
        assert len(rows) == 1
    finally:
        conn.close()


def test_export_rows_new_four_kinds_and_empty(analytics_data):
    conn = analytics_data.db.connect()
    try:
        repo = SqliteRepository(conn)
        headers, rows = repo.export_rows(
            "complaints", date_from=D - timedelta(days=1), date_to=D
        )
        assert headers[0] == "投诉号"
        assert len(rows) == 3

        headers, rows = repo.export_rows(
            "complaints", date_from=D, date_to=D
        )
        assert len(rows) == 2

        headers, rows = repo.export_rows(
            "blacklist", date_from=D - timedelta(days=1), date_to=D - timedelta(days=1)
        )
        assert headers[0] == "记录号"
        assert len(rows) == 1

        headers, rows = repo.export_rows("credit_logs", date_from=D, date_to=D)
        assert headers[0] == "流水号"
        assert len(rows) == 1

        headers, rows = repo.export_rows("waitlist", date_from=D, date_to=D)
        assert headers[0] == "候补号"
        assert len(rows) == 4

        headers, rows = repo.export_rows(
            "waitlist", date_from=D + timedelta(days=10), date_to=D + timedelta(days=11)
        )
        assert headers[0] == "候补号"
        assert rows == []

        for kind in ("complaints", "blacklist", "credit_logs", "waitlist"):
            headers, rows = repo.export_rows(
                kind,
                date_from=D + timedelta(days=10),
                date_to=D + timedelta(days=11),
            )
            assert headers
            assert rows == []
    finally:
        conn.close()


def test_export_rows_unknown_kind_raises(analytics_data):
    conn = analytics_data.db.connect()
    try:
        repo = SqliteRepository(conn)
        with pytest.raises(ValueError):
            repo.export_rows("credit", date_from=None, date_to=None)
    finally:
        conn.close()
