"""网页端全流程走查发现的 7 项缺陷：后端接口回归测试。

覆盖：
* 自习室物理启用座位数 total_seats（分母）；
* USER_TIME_CONFLICT / SEAT_OCCUPIED / SEAT_DISABLED / SEAT_UNAVAILABLE 区分；
* 演示数据初始化保留现有系统管理员密码与个人状态。
"""

from __future__ import annotations

import sys
import types
from datetime import date, datetime, time, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from seat_reservation.api.app import create_app
from seat_reservation.config import Settings
from seat_reservation.domain.models import User
from seat_reservation.security import hash_password, verify_password
from seat_reservation.service.container import AppContext
from seat_reservation.storage.repository import SqliteRepository
from seat_reservation.storage.seed import seed_demo
from tests.helpers import PASSWORD


def _client(ctx: AppContext) -> TestClient:
    return TestClient(create_app(ctx.settings))


@pytest.fixture
def client(ctx):  # noqa: ANN001 - pytest fixture
    with _client(ctx) as test_client:
        yield test_client


def _login(client: TestClient, username: str) -> dict[str, str]:
    response = client.post(
        "/api/auth/login", json={"username": username, "password": PASSWORD}
    )
    assert response.status_code == 200, response.text
    return {"X-CSRF-Token": client.cookies.get("seat_csrf") or ""}


def _tomorrow() -> date:
    return (datetime.now() + timedelta(days=1)).date()


def _book(
    env,  # noqa: ANN001
    user_id: str,
    seat_id: str,
    day: date,
    *,
    start: time = time(9, 0),
    end: time = time(10, 0),
):
    return env.create(user_id, seat_id, start, end, now=datetime.now(), day=day)


def test_seats_returns_physical_total_seats_not_filtered_length(env, client) -> None:  # noqa: ANN001
    day = _tomorrow()
    assert _book(env, "S1", "R1-001", day).ok
    headers = _login(client, "S1")
    response = client.get(
        "/api/seats",
        params={
            "date": day.isoformat(),
            "start": "09:00",
            "end": "10:00",
            "only_available": "true",
        },
        headers=headers,
    )
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    rooms = {room["id"]: room["total_seats"] for room in data["rooms"]}
    # R1 有 6 个物理座位，被预约 1 个后只返回 5 个可用项，但分母仍是 6。
    assert rooms == {"R1": 6, "R2": 2}
    r1_items = [item for item in data["items"] if item["room_id"] == "R1"]
    assert len(r1_items) == 5
    assert data["total"] == 7


def test_room_total_seats_excludes_disabled_but_keeps_maintenance(ctx) -> None:  # noqa: ANN001
    with ctx.db.transaction() as conn:
        repo = SqliteRepository(conn)
        disabled = repo.get_seat("R1-002")
        maintenance = repo.get_seat("R1-003")
        assert disabled is not None and maintenance is not None
        disabled.admin_status = "disabled"
        maintenance.admin_status = "maintenance"
        repo.save_seat(disabled)
        repo.save_seat(maintenance)
    with ctx.engine(write=False) as engine:
        totals = {
            item["id"]: item["total_seats"]
            for item in engine.seat_room_totals(_actor(ctx, "Y1"))
        }
    assert totals["R1"] == 5  # 6 个座位，排除 1 个 disabled；maintenance 仍计入
    assert totals["R2"] == 2


def _actor(ctx: AppContext, user_id: str):  # noqa: ANN202
    from seat_reservation.domain.models import Actor, Role

    with ctx.db.read() as conn:
        user = SqliteRepository(conn).get_user(user_id)
    assert user is not None
    return Actor(id=user.id, name=user.name, role=Role(user.role))


def test_user_time_conflict_is_distinct_from_seat_occupied(env, client) -> None:  # noqa: ANN001
    day = _tomorrow()
    assert _book(env, "S1", "R1-001", day).ok
    s1 = _login(client, "S1")
    payload = {"date": day.isoformat(), "start": "09:00", "end": "10:00"}

    own = client.post(
        "/api/reservations", json={**payload, "seat_id": "R1-002"}, headers=s1
    )
    assert own.status_code == 409, own.text
    body = own.json()["error"]
    assert body["code"] == "USER_TIME_CONFLICT"
    assert "当前时段已有预约" in body["message"]
    assert body["rule"] == "BR-11"

    # 切换账号会替换 CSRF Cookie，必须重新登录取得匹配的 token。
    s2 = _login(client, "S2")
    occupied = client.post(
        "/api/reservations", json={**payload, "seat_id": "R1-001"}, headers=s2
    )
    assert occupied.status_code == 409, occupied.text
    occupied_body = occupied.json()["error"]
    assert occupied_body["code"] == "SEAT_OCCUPIED"
    assert "已被占用" in occupied_body["message"]
    assert occupied_body["rule"] == "BR-11"


def test_disabled_maintenance_and_blocked_seats_have_explicit_codes(env, client) -> None:  # noqa: ANN001
    day = _tomorrow()
    with env.ctx.db.transaction() as conn:
        repo = SqliteRepository(conn)
        disabled = repo.get_seat("R1-004")
        maintenance = repo.get_seat("R1-005")
        assert disabled is not None and maintenance is not None
        disabled.admin_status = "disabled"
        maintenance.admin_status = "maintenance"
        repo.save_seat(disabled)
        repo.save_seat(maintenance)
    assert env.block_seat(
        "A1",
        "R1-006",
        "临时检修",
        datetime.combine(day, time(9, 0)),
        datetime.combine(day, time(11, 0)),
        now=datetime.now(),
    ).ok

    headers = _login(client, "S2")
    payload = {"date": day.isoformat(), "start": "09:00", "end": "10:00"}
    for seat_id, code, status in (
        ("R1-004", "SEAT_DISABLED", 400),
        ("R1-005", "SEAT_UNAVAILABLE", 400),
        ("R1-006", "SEAT_UNAVAILABLE", 400),
    ):
        response = client.post(
            "/api/reservations", json={**payload, "seat_id": seat_id}, headers=headers
        )
        assert response.status_code == status, response.text
        error = response.json()["error"]
        assert error["code"] == code, (seat_id, error)
        assert "停用维护" in error["message"] or "停用" in error["message"]
        assert error["rule"] == "BR-09"


def test_seed_demo_preserves_existing_active_admin(tmp_path: Path) -> None:
    ctx = AppContext(Settings.for_tests(tmp_path / "preserve.db"))
    ctx.initialize(demo=False)
    admin = User(
        id="Y0001",
        name="保留管理员",
        role="sys_admin",
        department="自定义院系",
        class_name="",
        credit=88,
        status="active",
        limit_type="none",
        password_hash=hash_password("My-Admin-Pass-1", iterations=1000),
        must_change_password=True,
        created_at=datetime(2026, 1, 1, 8, 0),
        updated_at=datetime(2026, 1, 2, 8, 0),
    )
    with ctx.db.transaction() as conn:
        SqliteRepository(conn).save_user(admin)

    counts = seed_demo(
        ctx.db, datetime.now(), password="Demo-Pass-1234", hash_iterations=1000
    )
    assert counts["admin_preserved"] == 1
    assert counts["preserved_admins"][0]["username"] == "Y0001"
    assert counts["users"] == 9  # 8 个非管理员演示账号 + 保留的管理员

    with ctx.db.read() as conn:
        repo = SqliteRepository(conn)
        kept = repo.get_user("Y0001")
        assert kept is not None
        assert kept.name == "保留管理员"
        assert kept.department == "自定义院系"
        assert kept.credit == 88
        assert kept.must_change_password is True
        assert verify_password("My-Admin-Pass-1", kept.password_hash)
        assert not verify_password("Demo-Pass-1234", kept.password_hash)
        student = repo.get_user("S2023001")
        assert student is not None
        assert verify_password("Demo-Pass-1234", student.password_hash)


def test_seed_demo_creates_default_admin_when_none_active(tmp_path: Path) -> None:
    ctx = AppContext(Settings.for_tests(tmp_path / "no-admin.db"))
    ctx.initialize(demo=False)
    counts = seed_demo(
        ctx.db, datetime.now(), password="Demo-Pass-5678", hash_iterations=1000
    )
    assert counts["admin_preserved"] == 0
    with ctx.db.read() as conn:
        admin = SqliteRepository(conn).get_user("Y0001")
    assert admin is not None
    assert verify_password("Demo-Pass-5678", admin.password_hash)
