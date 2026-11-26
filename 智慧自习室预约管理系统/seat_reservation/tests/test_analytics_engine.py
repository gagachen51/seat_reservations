"""D 模块 DomainEngine 测试：权限、日期范围、房间校验与导出委托。"""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest

from seat_reservation.domain.engine import EXPORT_KINDS
from seat_reservation.domain.models import Complaint
from seat_reservation.errors import Forbidden, NotFound, ValidationFailed
from seat_reservation.storage.repository import SqliteRepository
from tests.helpers import D, NOW


def _analytics(env, user_id: str, **kwargs):
    with env.ctx.engine(write=False) as engine:
        return engine.admin_analytics(env.actor(user_id), now=NOW, **kwargs)


def _export(env, user_id: str, kind: str, *, date_from=None, date_to=None):
    with env.ctx.engine(write=False) as engine:
        return engine.export_rows(
            env.actor(user_id), kind, date_from=date_from, date_to=date_to
        )


def test_analytics_permissions_and_room_scope(env) -> None:
    # SRS PR-12：room_admin 与 sys_admin 都是全校范围；房间只做存在性校验。
    room_admin_all = _analytics(env, "A1")
    assert room_admin_all["range"]["room_id"] is None
    assert {room["id"] for room in room_admin_all["rooms"]} == {"R1", "R2"}

    room_admin_r2 = _analytics(env, "A1", room_id="R2")
    assert room_admin_r2["range"]["room_id"] == "R2"
    assert [room["id"] for room in room_admin_r2["rooms"]] == ["R2"]

    sys_admin_r1 = _analytics(env, "Y1", room_id="R1")
    assert sys_admin_r1["range"]["room_id"] == "R1"
    assert [room["id"] for room in sys_admin_r1["rooms"]] == ["R1"]

    with pytest.raises(Forbidden) as student_denied:
        _analytics(env, "S1")
    assert student_denied.value.rule == "PR-12"

    with pytest.raises(Forbidden) as teacher_denied:
        _analytics(env, "T1")
    assert teacher_denied.value.rule == "PR-12"

    with pytest.raises(NotFound):
        _analytics(env, "A1", room_id="R404")


def test_analytics_default_and_partial_date_ranges(env) -> None:
    default = _analytics(env, "Y1")
    assert default["range"] == {
        "date_from": "2026-05-05",
        "date_to": "2026-05-11",
        "days": 7,
        "room_id": None,
    }

    only_from = _analytics(env, "Y1", date_from=date(2026, 5, 1))
    assert only_from["range"]["date_from"] == "2026-05-01"
    assert only_from["range"]["date_to"] == "2026-05-07"
    assert only_from["range"]["days"] == 7

    only_to = _analytics(env, "Y1", date_to=date(2026, 5, 10))
    assert only_to["range"]["date_from"] == "2026-05-04"
    assert only_to["range"]["date_to"] == "2026-05-10"
    assert only_to["range"]["days"] == 7

    custom = _analytics(
        env,
        "Y1",
        date_from=date(2026, 5, 1),
        date_to=date(2026, 5, 3),
    )
    assert custom["range"]["date_from"] == "2026-05-01"
    assert custom["range"]["date_to"] == "2026-05-03"
    assert custom["range"]["days"] == 3


def test_analytics_date_validation(env) -> None:
    with pytest.raises(ValidationFailed, match="开始日期不能晚于结束日期"):
        _analytics(env, "Y1", date_from=date(2026, 5, 11), date_to=date(2026, 5, 10))

    # 2026-01-01 ~ 2026-03-31 含首尾共 90 天，应允许
    max_allowed = _analytics(
        env,
        "Y1",
        date_from=date(2026, 1, 1),
        date_to=date(2026, 3, 31),
    )
    assert max_allowed["range"]["days"] == 90

    with pytest.raises(ValidationFailed, match="不能超过 90 天"):
        _analytics(
            env,
            "Y1",
            date_from=date(2026, 1, 1),
            date_to=date(2026, 4, 1),
        )


def test_analytics_preserves_repository_fields(env) -> None:
    data = _analytics(env, "A1")
    assert {
        "range",
        "kpi",
        "daily",
        "peak_hours",
        "rooms",
        "violations",
        "ratings",
        "waitlist",
        "complaints",
        "metric_notes",
        "generated_at",
    } <= set(data)
    assert data["generated_at"] == "2026-05-11 08:00:00"
    assert "booking_utilization" in data["rooms"][0]
    assert "utilization" not in data["rooms"][0]
    assert "booking_utilization" in data["metric_notes"]
    assert "checkin_rate" in data["metric_notes"]
    assert "checkin_base" in data["kpi"]
    assert "attendance_base" in data["kpi"]


def test_export_all_kinds_and_permissions(env) -> None:
    for kind in EXPORT_KINDS:
        headers, rows = _export(env, "Y1", kind)
        assert isinstance(headers, list) and headers
        assert isinstance(rows, list)

    for user_id in ("S1", "T1", "A1"):
        for kind in EXPORT_KINDS:
            with pytest.raises(Forbidden) as denied:
                _export(env, user_id, kind)
            assert denied.value.rule == "PR-16"

    with pytest.raises(ValidationFailed, match="不支持的导出类型"):
        _export(env, "Y1", "credit")

    # 权限先于类型校验：学生即使传未知类型也只能得到 403，不能借错误信息探测类型。
    with pytest.raises(Forbidden) as denied_unknown:
        _export(env, "S1", "credit")
    assert denied_unknown.value.rule == "PR-16"


def test_export_date_filter_and_inverted_range(env) -> None:
    with env.ctx.db.transaction() as conn:
        repo = SqliteRepository(conn)
        repo.insert_complaint(
            Complaint(
                id="CP-ENGINE-0001",
                user_id="S1",
                complaint_type="facility",
                content="D 当天的测试投诉",
                seat_id="R1-001",
                status="pending",
                created_at=datetime(2026, 5, 11, 6, 0),
            )
        )

    headers, rows = _export(
        env,
        "Y1",
        "reservations",
        date_from=D - timedelta(days=2),
        date_to=D - timedelta(days=2),
    )
    assert headers[0] == "预约号"
    assert len(rows) == 1  # seed_fixture 的 D-2 历史未签到预约

    headers, rows = _export(
        env,
        "Y1",
        "violations",
        date_from=D - timedelta(days=2),
        date_to=D - timedelta(days=2),
    )
    assert headers[0] == "违规号"
    assert len(rows) == 1

    headers, rows = _export(
        env,
        "Y1",
        "complaints",
        date_from=D,
        date_to=D,
    )
    assert headers[0] == "投诉号"
    assert len(rows) == 1

    # 只提供一端日期时，Repository 按开区间方向处理，不抛参数错误
    headers, rows = _export(
        env, "Y1", "reservations", date_from=D - timedelta(days=2), date_to=None
    )
    assert len(rows) == 1
    headers, rows = _export(
        env, "Y1", "reservations", date_from=None, date_to=D - timedelta(days=3)
    )
    assert rows == []

    with pytest.raises(ValidationFailed, match="开始日期不能晚于结束日期"):
        _export(env, "Y1", "reservations", date_from=D, date_to=D - timedelta(days=1))
