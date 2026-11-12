"""API 契约测试：覆盖全部路由的状态码、错误结构与权限矩阵。

与 test_api.py 的分工：那边测登录、CSRF、校验细节；这里保证"每条路由都能通、
不会 500、错误结构统一"。任何未捕获异常都会让用例失败。
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from seat_reservation.api.app import create_app
from seat_reservation.config import Settings
from tests.helpers import PASSWORD


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


def tomorrow(offset: int = 1) -> str:
    return (datetime.now() + timedelta(days=offset)).date().isoformat()


class Calls:
    """记录调用，保证没有 5xx，并累计覆盖的路由数。"""

    def __init__(self) -> None:
        self.routes: set[tuple[str, str]] = set()

    def __call__(
        self,
        client: TestClient,
        headers: dict[str, str],
        method: str,
        path: str,
        *,
        expect: int | None = None,
        params: dict | None = None,
        json: dict | None = None,
    ):
        response = client.request(method, path, headers=headers, params=params, json=json)
        self.routes.add((method, path.split("?")[0]))
        assert response.status_code < 500, (
            f"{method} {path} 返回 {response.status_code}：{response.text[:400]}"
        )
        if expect is not None:
            body = response.text[:400]
            assert response.status_code == expect, f"{method} {path} 期望 {expect}，实得 {response.status_code}：{body}"
        return response


@pytest.fixture
def call() -> Calls:
    return Calls()


def free_seat(
    client: TestClient, headers: dict[str, str], day: str, start: str = "09:00", end: str = "10:00"
) -> str:
    response = client.get(
        "/api/seats",
        params={"date": day, "start": start, "end": end},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    items = response.json()["data"]["items"]
    assert items, "测试夹具未生成座位"
    available = [item["id"] for item in items if item["available"]]
    return available[0] if available else items[0]["id"]


def busy_seat(client: TestClient, headers: dict[str, str], day: str) -> str:
    response = client.get(
        "/api/seats",
        params={"date": day, "start": "09:00", "end": "10:00"},
        headers=headers,
    )
    items = response.json()["data"]["items"]
    occupied = [item["id"] for item in items if not item["available"]]
    return occupied[0] if occupied else items[0]["id"]


def test_common_and_auth_routes(client: TestClient, call: Calls) -> None:
    public = call(client, {}, "GET", "/api/health", expect=200)
    assert public.json()["data"]["db"]["schema_version"] == 1

    headers = login(client, "S1")
    call(client, headers, "GET", "/api/meta", expect=200)
    call(client, headers, "GET", "/api/auth/me", expect=200)
    call(client, headers, "GET", "/api/notifications", expect=200)
    call(client, headers, "GET", "/api/notifications/unread", expect=200)
    call(client, headers, "POST", "/api/notifications/read", expect=200, json={"ids": []})
    call(client, headers, "POST", "/api/auth/logout", expect=200)
    assert client.get("/api/auth/me").status_code == 401


def test_demo_accounts_available_in_demo_mode(client: TestClient, call: Calls) -> None:
    response = call(client, {}, "GET", "/api/auth/demo-accounts", expect=200)
    items = response.json()["data"]
    assert len(items) == 4
    assert {item["role"] for item in items} == {
        "student",
        "teacher",
        "room_admin",
        "sys_admin",
    }
    assert all(item["password"] for item in items)


def test_demo_accounts_unavailable_when_disabled(ctx) -> None:
    import dataclasses

    settings = dataclasses.replace(Settings.for_tests(ctx.settings.db_path), demo_mode=False)
    app = create_app(settings)
    with TestClient(app) as disabled:
        response = disabled.get("/api/auth/demo-accounts")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


def test_student_routes_contract(client: TestClient, call: Calls) -> None:
    headers = login(client, "S1")
    day = tomorrow()

    call(client, headers, "GET", "/api/seats", expect=200,
         params={"date": day, "start": "09:00", "end": "10:00"})
    call(client, headers, "GET", "/api/seats", expect=200,
         params={"date": day, "start": "09:00", "end": "10:00", "only_available": True})
    call(client, headers, "GET", "/api/seats", expect=200,
         params={"date": day, "start": "09:00", "end": "10:00", "has_power": True, "is_window": True})

    listing = call(client, headers, "GET", "/api/reservations", expect=200).json()
    assert set(listing["data"]) >= {"items", "total"}

    call(client, headers, "GET", "/api/me/dashboard", expect=200)
    call(client, headers, "GET", "/api/me/credit-logs", expect=200)
    call(client, headers, "GET", "/api/violations", expect=200)
    call(client, headers, "GET", "/api/complaints", expect=200)
    call(client, headers, "GET", "/api/reviews", expect=200)
    call(client, headers, "GET", "/api/waitlist", expect=200)

    seat = free_seat(client, headers, day)
    created = call(client, headers, "POST", "/api/reservations", expect=201,
                   json={"seat_id": seat, "date": day, "start": "14:00", "end": "15:00"})
    new_id = created.json()["data"]["id"]

    detail = call(client, headers, "GET", f"/api/reservations/{new_id}", expect=200)
    assert detail.json()["data"]["id"] == new_id
    call(client, headers, "POST", f"/api/reservations/{new_id}/credential", expect=200)
    # 未到签到窗口：业务拒绝，但不能是 5xx
    call(client, headers, "POST", f"/api/reservations/{new_id}/check-in", json={"code": "00000000"})
    call(client, headers, "POST", f"/api/reservations/{new_id}/away")
    call(client, headers, "POST", f"/api/reservations/{new_id}/resume")
    call(client, headers, "POST", f"/api/reservations/{new_id}/check-out")
    call(client, headers, "POST", f"/api/reservations/{new_id}/cancel", expect=200)
    # 已取消后不能再签发凭证
    call(client, headers, "POST", f"/api/reservations/{new_id}/credential")

    complaint = call(client, headers, "POST", "/api/complaints", expect=201,
                     json={"type": "noise", "content": "隔壁座位持续大声通话", "seat_id": seat})
    complaint_id = complaint.json()["data"]["id"]
    call(client, headers, "POST", f"/api/complaints/{complaint_id}/rate", json={"rating": 5, "comment": "处理及时"})

    call(client, headers, "POST", "/api/waitlist", json={
        "seat_id": busy_seat(client, headers, day), "date": day, "start": "09:00", "end": "10:00"
    })
    call(client, headers, "GET", "/api/waitlist", expect=200)


def test_reservation_detail_is_scoped_to_owner(client: TestClient, call: Calls) -> None:
    owner = login(client, "S1")
    day = tomorrow(2)
    seat = free_seat(client, owner, day, "20:00", "21:00")
    created = client.post(
        "/api/reservations",
        json={"seat_id": seat, "date": day, "start": "20:00", "end": "21:00"},
        headers=owner,
    )
    assert created.status_code == 201, created.text
    reservation_id = created.json()["data"]["id"]

    other = login(client, "S2")
    response = call(client, other, "GET", f"/api/reservations/{reservation_id}", expect=403)
    assert response.json()["error"]["code"] == "FORBIDDEN"



def test_admin_routes_contract(client: TestClient, call: Calls) -> None:
    headers = login(client, "A1")
    day = tomorrow()

    call(client, headers, "GET", "/api/admin/stats", expect=200)
    call(client, headers, "GET", "/api/admin/reservations", expect=200)
    call(client, headers, "GET", "/api/admin/complaints", expect=200)
    call(client, headers, "GET", "/api/admin/students", expect=200)
    call(client, headers, "GET", "/api/admin/users", expect=200)
    call(client, headers, "GET", "/api/admin/blacklist", expect=200)
    call(client, headers, "GET", "/api/admin/reviews", expect=200)

    seat = free_seat(client, headers, day)
    call(client, headers, "POST", "/api/admin/seat-blocks", expect=201, json={
        "seat_id": seat, "reason": "设备检修",
        "start_at": f"{day}T09:00:00", "end_at": f"{day}T12:00:00",
    })
    call(client, headers, "DELETE", f"/api/admin/seat-blocks/{seat}", expect=200)

    reservations = call(client, headers, "GET", "/api/admin/reservations", params={"page_size": 50}, expect=200)
    admin_items = reservations.json()["data"]["items"]
    if admin_items:
        call(client, headers, "POST", "/api/admin/manual-violation", json={
            "reservation_id": admin_items[0]["id"], "mode": "warn", "note": "现场核实：桌面留物"
        })

    complaints = call(client, headers, "GET", "/api/admin/complaints", params={"page_size": 50}, expect=200)
    pending = [item for item in complaints.json()["data"]["items"] if item["status"] == "pending"]
    if pending:
        call(client, headers, "POST", f"/api/admin/complaints/{pending[0]['id']}/resolve",
             expect=200, json={"feedback": "已现场核实并处理"})

    reviews = call(client, headers, "GET", "/api/admin/reviews", params={"page_size": 50}, expect=200)
    if reviews.json()["data"]["items"]:
        review_id = reviews.json()["data"]["items"][0]["id"]
        call(client, headers, "POST", f"/api/admin/reviews/{review_id}/reply",
             json={"reply": "感谢反馈，已安排巡检"})


def test_export_kinds_and_unknown_kind(client: TestClient, call: Calls) -> None:
    admin = login(client, "A1")
    # PR-16：数据导出只允许系统管理员；自习室管理员对全部导出类型均 403。
    for kind in ("reservations", "violations", "audit"):
        call(client, admin, "GET", f"/api/admin/export/{kind}.csv", expect=403)

    sys_headers = login(client, "Y1")
    for kind in ("reservations", "violations", "audit"):
        response = call(
            client, sys_headers, "GET", f"/api/admin/export/{kind}.csv", expect=200
        )
        assert response.headers["content-type"].startswith("text/csv")
        assert "attachment" in response.headers["content-disposition"]
        assert response.content.startswith("\ufeff".encode("utf-8"))

    unknown = call(
        client, sys_headers, "GET", "/api/admin/export/credit.csv", expect=422
    )
    body = unknown.json()
    assert body["ok"] is False
    assert body["error"]["code"] == "VALIDATION_ERROR"
    assert "reservations" in body["error"]["message"]


def test_sysadmin_and_teacher_routes_contract(client: TestClient, call: Calls) -> None:
    sys_headers = login(client, "Y1")
    call(client, sys_headers, "GET", "/api/admin/params", expect=200)
    call(client, sys_headers, "GET", "/api/admin/audit", expect=200)
    call(client, sys_headers, "GET", "/api/admin/scheduler", expect=200)
    call(client, sys_headers, "PUT", "/api/admin/params/checkin.before_minutes", expect=200, json={"value": "15"})
    call(client, sys_headers, "GET", "/api/admin/users", expect=200)

    # PR-18 / §2.6：投诉、评价、黑名单整体归自习室管理员，系统管理员一律拒绝（读也不行）。
    call(client, sys_headers, "GET", "/api/admin/complaints", expect=403)
    call(client, sys_headers, "GET", "/api/admin/blacklist", expect=403)
    call(client, sys_headers, "GET", "/api/admin/reviews", expect=403)
    call(client, sys_headers, "POST", "/api/admin/blacklist", expect=403, json={
        "user_id": "S2", "reason": "多次占座且拒不改正", "days": 3,
    })
    call(client, sys_headers, "POST", "/api/admin/blacklist/S2/release", expect=403,
         json={"reason": "申诉成立，撤销限制"})

    teacher = login(client, "T1")
    dashboard = call(client, teacher, "GET", "/api/teacher/dashboard", expect=200)
    data = dashboard.json()["data"]
    assert data.get("class_name")
    assert "kpi" in data


@pytest.mark.parametrize(
    ("role", "method", "path", "rule"),
    [
        ("S1", "GET", "/api/admin/stats", "PR-"),
        ("S1", "GET", "/api/admin/params", "PR-"),
        ("S1", "GET", "/api/admin/audit", "PR-"),
        ("S1", "POST", "/api/admin/reservations/X/force-release", "PR-"),
        ("S1", "GET", "/api/admin/reviews", "PR-18"),
        ("S1", "GET", "/api/admin/blacklist", "PR-18"),
        ("Y1", "GET", "/api/admin/reviews", "PR-18"),
        ("T1", "GET", "/api/admin/reservations", "PR-11"),
        ("T1", "GET", "/api/reservations", "PR-11"),
        ("A1", "GET", "/api/admin/audit", "PR-15"),
        ("A1", "GET", "/api/admin/params", "PR-13"),
        ("A1", "GET", "/api/admin/scheduler", "PR-15"),
        ("A1", "GET", "/api/teacher/dashboard", "PR-12"),
    ],
)
def test_permission_matrix(client: TestClient, role: str, method: str, path: str, rule: str) -> None:
    headers = login(client, role)
    payload = {"reason": "越权测试"} if method == "POST" else None
    response = client.request(method, path, headers=headers, json=payload)
    assert response.status_code == 403, f"{role} {method} {path} 应被拒绝，实得 {response.status_code}"
    body = response.json()
    assert body["ok"] is False
    assert body["error"]["code"] == "FORBIDDEN"
    assert str(body["error"]["rule"]).startswith(rule)


def test_error_shape_is_uniform(client: TestClient) -> None:
    client.cookies.clear()
    cases = [client.get("/api/reservations"), client.get("/api/admin/stats")]

    headers = login(client, "S1")
    cases += [
        client.get("/api/seats", headers=headers),
        client.post("/api/reservations", json={"seat_id": "R1-001"}, headers=headers),
        client.get("/api/reservations/NOT-EXIST", headers=headers),
        client.put("/api/admin/params/checkin.before_minutes", json={"value": "15"}, headers=headers),
    ]
    expected = [401, 401, 422, 422, 404, 403]

    for response, status in zip(cases, expected):
        assert response.status_code == status, f"期望 {status}，实得 {response.status_code}：{response.text[:200]}"
        body = response.json()
        assert body["ok"] is False
        error = body["error"]
        assert set(error) >= {"code", "message"}
        assert isinstance(error["code"], str)


@pytest.mark.parametrize(
    ("params", "expect"),
    [
        ({"page": 0}, 422),
        ({"page_size": 0}, 422),
        ({"page_size": 101}, 422),
        ({"page": 1, "page_size": 100}, 200),
    ],
)
def test_pagination_bounds(client: TestClient, params: dict, expect: int) -> None:
    headers = login(client, "A1")
    response = client.get("/api/admin/reservations", params=params, headers=headers)
    assert response.status_code == expect


def test_unknown_route_and_method(client: TestClient) -> None:
    headers = login(client, "S1")
    assert client.get("/api/not-a-route", headers=headers).status_code == 404
    assert client.delete("/api/reservations", headers=headers).status_code == 405
