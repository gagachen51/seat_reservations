"""v1.2 跨模块集成回归（IT-01 ～ IT-14）。

覆盖 A/B/C/D 与共享层的账号、资源、预约、分析、导出、并发和事务边界。
使用 ``ctx`` 的隔离 SQLite 与 ``helpers.seed_fixture`` 的固定数据。
"""

from __future__ import annotations

import csv
import io
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, time, timedelta

import pytest
from fastapi.testclient import TestClient

from seat_reservation.api.app import create_app
from seat_reservation.config import Settings
from seat_reservation.domain.models import Role, User
from seat_reservation.errors import Conflict, Forbidden, SlotConflict
from seat_reservation.security import hash_password
from seat_reservation.service.container import AppContext
from seat_reservation.storage.repository import SqliteRepository
from tests.helpers import D, NOW, PASSWORD, Env, make_credential

EIGHT_FIFTY = datetime(2026, 5, 11, 8, 50, 0)
NINE_THIRTY = datetime(2026, 5, 11, 9, 30, 0)
TEN_SIXTEEN = datetime(2026, 5, 11, 10, 16, 0)
EXPORT_KINDS = (
    "reservations", "violations", "audit", "complaints",
    "blacklist", "credit_logs", "waitlist",
)


@pytest.fixture
def api_app(ctx):
    return create_app(Settings.for_tests(ctx.settings.db_path))


@pytest.fixture
def api(api_app):
    with TestClient(api_app) as client:
        yield client


def login(client: TestClient, username: str = "Y1", password: str = PASSWORD) -> dict[str, str]:
    response = client.post("/api/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200, response.text
    return {"X-CSRF-Token": response.json()["data"]["csrf_token"]}


def create_user_api(client: TestClient, headers: dict[str, str], user_id: str, **overrides):
    body = {
        "id": user_id,
        "name": f"账号{user_id}",
        "role": "student",
        "department": "计算机学院",
        "class_name": "软件 2301",
        "password": "12345678",
    }
    body.update(overrides)
    return client.post("/api/admin/users", headers=headers, json=body)


def set_reservation_status(env: Env, reservation_id: str, status: str) -> None:
    with env.ctx.db.transaction() as conn:
        conn.execute("UPDATE reservations SET status=? WHERE id=?", (status, reservation_id))


def set_user_status(env: Env, user_id: str, status: str = "disabled") -> None:
    with env.ctx.engine() as engine:
        engine.set_user_status(env.actor("Y1"), user_id, status, "集成测试", NOW)


def add_users(ctx, count: int) -> list[str]:
    ids: list[str] = []
    with ctx.db.transaction() as conn:
        repo = SqliteRepository(conn)
        for index in range(count):
            user_id = f"C{index:03d}"
            ids.append(user_id)
            repo.save_user(
                User(
                    id=user_id,
                    name=f"并发{index}",
                    role=Role.STUDENT.value,
                    credit=100,
                    password_hash=hash_password(PASSWORD, iterations=1000),
                    created_at=NOW,
                    updated_at=NOW,
                )
            )
    return ids


def csv_rows(response) -> list[list[str]]:
    text = response.content.decode("utf-8-sig")
    return list(csv.reader(io.StringIO(text)))


# ---------------------------------------------------------------------------
# IT-01 A + C + 共享层：账号停用后预约/签到/续约
# ---------------------------------------------------------------------------
def test_it_01_account_disable_blocks_booking_chain(env: Env) -> None:
    pending = env.create("S1", "R1-001", time(9, 0), time(10, 0)).data
    in_use = env.create("S1", "R1-002", time(10, 0), time(11, 0)).data
    set_reservation_status(env, in_use["id"], "in_use")
    code = make_credential(env, "S1", pending["id"], now=datetime(2026, 5, 11, 8, 49, 30))
    token, _, _ = env.ctx.auth.login("S1", PASSWORD, now=NOW)
    set_user_status(env, "S1")

    assert env.ctx.auth.authenticate(token, now=NOW) is None
    with pytest.raises(Forbidden):
        env.check_in("S1", pending["id"], code, now=EIGHT_FIFTY)
    with env.ctx.engine() as engine:
        with pytest.raises(Forbidden):
            engine.check_in_by_credential(code, EIGHT_FIFTY, method="acs")
        with pytest.raises(Forbidden):
            engine.extend_reservation(env.actor("S1"), in_use["id"], 30, EIGHT_FIFTY)

    assert env.reservation(pending["id"]).status == "pending"
    assert env.reservation(in_use["id"]).status == "in_use"
    assert env.violations("S1") == []
    assert env.user("S1").credit == 100


# ---------------------------------------------------------------------------
# IT-02 A + C + 共享层：强制改密期间接口访问
# ---------------------------------------------------------------------------
def test_it_02_force_password_blocks_business_api(api: TestClient) -> None:
    admin_headers = login(api)
    created = create_user_api(api, admin_headers, "IT-02")
    assert created.status_code == 201, created.text
    login(api, "IT-02", "12345678")
    csrf = {"X-CSRF-Token": api.cookies.get("seat_csrf") or ""}
    for url in ("/api/meta", "/api/reservations", "/api/me/dashboard"):
        response = api.get(url, headers=csrf)
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "PASSWORD_CHANGE_REQUIRED"
    assert api.get("/api/auth/me", headers=csrf).status_code == 200
    changed = api.post(
        "/api/auth/change-password",
        headers=csrf,
        json={"old_password": "12345678", "new_password": "Changed-Pass-123"},
    )
    assert changed.status_code == 200
    assert api.get("/api/auth/me", headers=csrf).status_code == 401
    login(api, "IT-02", "Changed-Pass-123")
    assert api.get("/api/meta").status_code == 200


# ---------------------------------------------------------------------------
# IT-02B A + 共享层：强制改密死锁热修复回归
# ---------------------------------------------------------------------------
def test_it_02b_must_change_login_keeps_session_until_password_updated(api: TestClient) -> None:
    """首次登录的强制改密账号必须先保住会话、再完成改密。

    对应前端修复：``must_change_password=true`` 时跳过 ``/api/meta``（该接口
    仍按既有安全边界返回 403），直接弹改密框。本用例锁定服务端契约：
    登录成功 → 会话可查 → 业务接口仍 403 → 改密 → 新密码登录后恢复。
    """
    admin = login(api)
    created = create_user_api(api, admin, "IT-02B")
    assert created.status_code == 201, created.text

    headers = login(api, "IT-02B", "12345678")
    me = api.get("/api/auth/me", headers=headers)
    assert me.status_code == 200
    assert me.json()["data"]["user"]["must_change_password"] is True

    blocked = api.get("/api/meta", headers=headers)
    assert blocked.status_code == 403
    assert blocked.json()["error"]["code"] == "PASSWORD_CHANGE_REQUIRED"

    changed = api.post(
        "/api/auth/change-password",
        headers=headers,
        json={"old_password": "12345678", "new_password": "Changed-Pass-123"},
    )
    assert changed.status_code == 200

    new_headers = login(api, "IT-02B", "Changed-Pass-123")
    assert api.get("/api/meta", headers=new_headers).status_code == 200
    assert api.get("/api/auth/me", headers=new_headers).json()["data"]["user"]["must_change_password"] is False


# ---------------------------------------------------------------------------
# IT-03 A/B/C/D + 共享层：四角色权限矩阵
# ---------------------------------------------------------------------------
def test_it_03_four_role_matrix(api: TestClient) -> None:
    cases = [
        ("S1", "/api/reservations", 200),
        ("S1", "/api/admin/users", 403),
        ("S1", "/api/admin/analytics", 403),
        ("T1", "/api/teacher/dashboard", 200),
        ("T1", "/api/reservations", 403),
        ("T1", "/api/admin/analytics", 403),
        ("A1", "/api/admin/analytics", 200),
        ("A1", "/api/admin/users", 200),
        ("A1", "/api/admin/params", 403),
        ("Y1", "/api/admin/analytics", 200),
        ("Y1", "/api/admin/params", 200),
        ("Y1", "/api/admin/blacklist", 403),
    ]
    for username, url, expected in cases:
        client = TestClient(api.app)
        try:
            login(client, username)
            assert client.get(url).status_code == expected, (username, url)
        finally:
            client.close()
    # 导出另列，避免每条都生成 CSV
    for username, expected in (("S1", 403), ("T1", 403), ("A1", 403), ("Y1", 200)):
        client = TestClient(api.app)
        try:
            login(client, username)
            assert client.get("/api/admin/export/reservations.csv").status_code == expected, username
        finally:
            client.close()


# ---------------------------------------------------------------------------
# IT-04 B + C：关闭房间
# ---------------------------------------------------------------------------
def test_it_04_close_room_releases_active_reservations(api: TestClient, env: Env) -> None:
    first = env.create("S1", "R1-001", time(9, 0), time(10, 0)).data
    second = env.create("S2", "R1-002", time(9, 0), time(10, 0)).data
    third = env.create("S3", "R1-003", time(9, 0), time(10, 0)).data
    set_reservation_status(env, second["id"], "in_use")
    set_reservation_status(env, third["id"], "away")
    headers = login(api, "A1")
    response = api.put("/api/admin/rooms/R1", headers=headers, json={"status": "closed"})
    assert response.status_code == 200, response.text
    for item in (first, second, third):
        saved = env.reservation(item["id"])
        assert saved.status == "cancelled"
        assert saved.end_reason == "resource_unavailable"
    assert env.sweep(now=TEN_SIXTEEN)["no_show"] == 0
    for user_id in ("S1", "S2", "S3"):
        assert env.user(user_id).credit == 100
        assert env.violations(user_id) == []
    repeated = api.put("/api/admin/rooms/R1", headers=headers, json={"status": "closed"})
    assert repeated.status_code == 200


# ---------------------------------------------------------------------------
# IT-05 B + C：座位停用/维护/封禁
# ---------------------------------------------------------------------------
def test_it_05_seat_state_and_block_lifecycle(env: Env) -> None:
    pending = env.create("S1", "R1-001", time(9, 0), time(10, 0)).data
    in_use = env.create("S2", "R1-002", time(9, 0), time(10, 0)).data
    set_reservation_status(env, in_use["id"], "in_use")
    with env.ctx.engine() as engine:
        disabled = engine.update_seat(
            env.actor("A1"),
            "R1-001",
            room_id=None,
            seat_type=None,
            capacity=None,
            admin_status="disabled",
            has_power=None,
            is_window=None,
            note=None,
            now=NOW,
            cascade_release=True,
        )
    assert disabled.ok
    assert env.reservation(pending["id"]).status == "cancelled"
    assert env.reservation(pending["id"]).end_reason == "resource_unavailable"

    with env.ctx.engine() as engine:
        blocked = engine.block_seat(
            env.actor("A1"),
            "R1-002",
            "集成测试维护",
            NOW,
            NOW + timedelta(hours=2),
            NOW,
        )
    assert blocked.ok
    assert env.reservation(in_use["id"]).status == "cancelled"
    assert env.reservation(in_use["id"]).end_reason == "seat_blocked"
    assert env.user("S2").credit == 100
    with env.ctx.engine() as engine:
        assert engine.unblock_seat(env.actor("A1"), "R1-002", NOW).ok
    assert env.user("S2").credit == 100
    assert env.violations("S2") == []


# ---------------------------------------------------------------------------
# IT-06 B + C：资源恢复后预约
# ---------------------------------------------------------------------------
def test_it_06_reopened_resource_can_book(env: Env) -> None:
    with env.ctx.engine() as engine:
        engine.update_room(
            env.actor("A1"), "R1",
            name=None, location=None, status="closed",
            open_time=None, close_time=None, now=NOW, cascade_release=True,
        )
        engine.update_room(
            env.actor("A1"), "R1",
            name=None, location=None, status="open",
            open_time=None, close_time=None, now=NOW,
        )
    result = env.create("S1", "R1-001", time(9, 0), time(10, 0))
    assert result.ok
    assert env.reservation(result.data["id"]).status == "pending"


# ---------------------------------------------------------------------------
# IT-07 C + D：预约与运营分析
# ---------------------------------------------------------------------------
def test_it_07_analytics_matches_reservation_facts(api: TestClient, env: Env) -> None:
    pending = env.create("S1", "R1-001", time(9, 0), time(10, 0)).data
    completed = env.create("S2", "R1-002", time(9, 0), time(10, 0)).data
    cancelled = env.create("S3", "R1-003", time(9, 0), time(10, 0)).data
    no_show = env.create("S5", "R1-004", time(9, 0), time(10, 0)).data
    set_reservation_status(env, completed["id"], "completed")
    set_reservation_status(env, cancelled["id"], "cancelled")
    set_reservation_status(env, no_show["id"], "no_show")
    headers = login(api)
    response = api.get(
        "/api/admin/analytics",
        headers=headers,
        params={"date_from": D.isoformat(), "date_to": D.isoformat()},
    )
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["kpi"]["reservations"] == 4
    assert data["kpi"]["pending"] == 1
    assert data["kpi"]["completed"] == 1
    assert data["kpi"]["cancelled"] == 1
    assert data["kpi"]["no_show"] == 1
    assert data["daily"][0]["date"] == D.isoformat()
    assert data["daily"][0]["reservations"] == 4


# ---------------------------------------------------------------------------
# IT-08 C + D：预约与导出
# ---------------------------------------------------------------------------
def test_it_08_all_exports_and_reservation_rows(api: TestClient, env: Env) -> None:
    reservation = env.create("S1", "R1-001", time(9, 0), time(10, 0)).data
    set_reservation_status(env, reservation["id"], "cancelled")
    headers = login(api)
    for kind in EXPORT_KINDS:
        response = api.get(
            f"/api/admin/export/{kind}.csv",
            headers=headers,
            params={"date_from": D.isoformat(), "date_to": D.isoformat()},
        )
        assert response.status_code == 200, (kind, response.text)
        assert response.content.startswith(b"\xef\xbb\xbf")
        rows = csv_rows(response)
        assert rows and len({len(row) for row in rows}) == 1
        assert "password_hash" not in response.text
        assert "token_hash" not in response.text
    rows = csv_rows(
        api.get(
            "/api/admin/export/reservations.csv",
            headers=headers,
            params={"date_from": D.isoformat(), "date_to": D.isoformat()},
        )
    )
    assert len(rows) == 2  # header + one reservation
    assert "cancelled" in rows[1]


# ---------------------------------------------------------------------------
# IT-09 共享层 + D + A：权限与导出审计
# ---------------------------------------------------------------------------
def test_it_09_export_permissions_and_audit(api: TestClient, env: Env) -> None:
    admin = login(api)
    assert api.get("/api/admin/analytics", headers=admin).status_code == 200
    assert api.get("/api/admin/export/reservations.csv", headers=admin).status_code == 200
    with env.ctx.db.read() as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM audit_logs WHERE action='export_csv'"
        ).fetchone()[0] >= 1
    for username, expected in (("S1", 403), ("T1", 403), ("A1", 403)):
        client = TestClient(api.app)
        try:
            headers = login(client, username)
            assert client.get("/api/admin/analytics", headers=headers).status_code in (200, 403)
            assert client.get("/api/admin/export/reservations.csv", headers=headers).status_code == expected
        finally:
            client.close()


# ---------------------------------------------------------------------------
# IT-10 B/C/D：资源状态与分析/导出一致
# ---------------------------------------------------------------------------
def test_it_10_resource_close_analytics_export_consistent(api: TestClient, env: Env) -> None:
    reservation = env.create("S1", "R1-001", time(9, 0), time(10, 0)).data
    with env.ctx.engine() as engine:
        engine.update_room(
            env.actor("A1"), "R1",
            name=None, location=None, status="closed",
            open_time=None, close_time=None, now=NOW, cascade_release=True,
        )
    assert env.reservation(reservation["id"]).status == "cancelled"
    assert env.reservation(reservation["id"]).end_reason == "resource_unavailable"
    headers = login(api)
    analytics = api.get(
        "/api/admin/analytics",
        headers=headers,
        params={"date_from": D.isoformat(), "date_to": D.isoformat()},
    ).json()["data"]
    assert analytics["kpi"]["cancelled"] == 1
    assert analytics["kpi"]["no_show"] == 0
    export = api.get(
        "/api/admin/export/reservations.csv",
        headers=headers,
        params={"date_from": D.isoformat(), "date_to": D.isoformat()},
    )
    rows = csv_rows(export)
    assert len(rows) == 2 and "cancelled" in rows[1]


# ---------------------------------------------------------------------------
# IT-11 A + C + 共享层：账号创建 → 预约
# ---------------------------------------------------------------------------
def test_it_11_create_account_change_password_then_book(api: TestClient) -> None:
    admin = login(api)
    created = create_user_api(api, admin, "IT-11", password=None)
    assert created.status_code == 201, created.text
    initial = created.json()["data"]["initial_password"]
    login(api, "IT-11", initial)
    headers = {"X-CSRF-Token": api.cookies.get("seat_csrf") or ""}
    changed = api.post(
        "/api/auth/change-password",
        headers=headers,
        json={"old_password": initial, "new_password": "Changed-Pass-123"},
    )
    assert changed.status_code == 200
    login(api, "IT-11", "Changed-Pass-123")
    future = date.today() + timedelta(days=1)
    booked = api.post(
        "/api/reservations",
        headers={"X-CSRF-Token": api.cookies.get("seat_csrf") or ""},
        json={"seat_id": "R1-001", "date": future.isoformat(), "start": "09:00", "end": "10:00"},
    )
    assert booked.status_code == 201, booked.text
    assert booked.json()["data"]["status"] == "pending"


# ---------------------------------------------------------------------------
# IT-12 C + B：候补完整链
# ---------------------------------------------------------------------------
def test_it_12_waitlist_full_chain(env: Env) -> None:
    reservation = env.create("S1", "R1-001", time(9, 0), time(10, 0)).data
    with env.ctx.engine() as engine:
        joined = engine.join_waitlist(
            env.actor("S2"), "R1-001", D, time(9, 0), time(10, 0), NOW
        )
    assert joined.ok
    entry_id = joined.data["id"]
    assert env.cancel("S1", reservation["id"]).ok
    with env.ctx.engine(write=False) as engine:
        entries = engine.list_waitlist(env.actor("S2"), page_size=20).items
    entry = next(item for item in entries if item["id"] == entry_id)
    assert entry["status"] == "notified"
    confirmed = env.confirm_waitlist("S2", entry_id, now=NOW)
    assert confirmed.ok
    assert confirmed.data["status"] == "pending"


# ---------------------------------------------------------------------------
# IT-13 B/C/共享层：事务失败回滚
# ---------------------------------------------------------------------------
def test_it_13_booking_rollback_on_slot_failure(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    before = env.count_reservations()

    def fail_occupy(self, reservation):  # noqa: ANN001
        raise SlotConflict("forced occupy failure")

    monkeypatch.setattr(SqliteRepository, "occupy", fail_occupy)
    with pytest.raises(Conflict):
        env.create("S1", "R1-001", time(9, 0), time(10, 0))
    assert env.count_reservations() == before
    with env.ctx.db.read() as conn:
        assert conn.execute("SELECT COUNT(*) FROM reservation_slots").fetchone()[0] == 0


# ---------------------------------------------------------------------------
# IT-14 C + 共享层：并发预约/续约/凭证
# ---------------------------------------------------------------------------
def test_it_14_concurrent_booking_extension_and_credential(env: Env) -> None:
    user_ids = add_users(env.ctx, 8)

    def book(user_id: str) -> str:
        context = AppContext(env.ctx.settings)
        try:
            with context.engine() as engine:
                result = engine.create_reservation(
                    env.actor(user_id), "R1-001", D, time(9, 0), time(10, 0), NOW
                )
            return "ok" if result.ok else "conflict"
        except Exception:  # noqa: BLE001 - 并发失败统一记 conflict
            return "conflict"

    with ThreadPoolExecutor(max_workers=8) as pool:
        outcomes = list(pool.map(book, user_ids))
    assert outcomes.count("ok") == 1, outcomes

    extension = env.create("S1", "R1-003", time(9, 0), time(11, 0)).data
    set_reservation_status(env, extension["id"], "in_use")

    def extend_once(_):
        context = AppContext(env.ctx.settings)
        try:
            with context.engine() as engine:
                result = engine.extend_reservation(env.actor("S1"), extension["id"], 120, NINE_THIRTY)
            return "ok" if result.ok else "rejected"
        except Exception:  # noqa: BLE001
            return "rejected"

    with ThreadPoolExecutor(max_workers=2) as pool:
        extension_results = list(pool.map(extend_once, range(2)))
    assert extension_results.count("ok") == 1, extension_results
    assert env.reservation(extension["id"]).end_slot == 26

    credential = env.create("S5", "R1-004", time(13, 0), time(14, 0)).data

    def issue_once(_):
        context = AppContext(env.ctx.settings)
        try:
            with context.engine() as engine:
                result = engine.issue_credential(env.actor("S5"), credential["id"], NOW)
            return result.data.get("code") if result.ok else None
        except Exception:  # noqa: BLE001
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        codes = [code for code in pool.map(issue_once, range(2)) if code]
    assert codes
    stored = env.reservation(credential["id"]).credential_code
    assert stored in codes
    assert len(set(codes)) == 1
