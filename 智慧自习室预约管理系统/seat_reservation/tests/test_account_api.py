"""账号 HTTP 契约；在夹具中装配 A 路由，正式注册由 B 的 SFCR 完成。"""
import pytest
from fastapi.testclient import TestClient

from seat_reservation.api.app import create_app
from seat_reservation.api.routes.account_routes import router
from seat_reservation.config import Settings
from tests.helpers import PASSWORD


@pytest.fixture
def account_app(ctx):
    app = create_app(Settings.for_tests(ctx.settings.db_path))
    if not any(getattr(route, "path", "") == "/api/me/sessions" for route in app.routes):
        app.include_router(router, prefix="/api")
    return app


@pytest.fixture
def client(account_app):
    with TestClient(account_app) as c:
        yield c


def login(client, username="Y1", password=PASSWORD):
    response = client.post("/api/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200, response.text
    return {"X-CSRF-Token": response.json()["data"]["csrf_token"]}


def new_user(client, headers, **kwargs):
    return client.post("/api/admin/users", headers=headers,
                       json={"id": "NEW1", "name": "新同学", "role": "student", **kwargs})


def test_create_login_change_password_roundtrip(client):
    headers = login(client)
    response = new_user(client, headers)
    assert response.status_code == 201
    assert response.headers["cache-control"] == "no-store"
    data = response.json()["data"]
    assert data["user"]["must_change_password"]
    duplicate = new_user(client, headers)
    assert duplicate.status_code == 409 and duplicate.json()["error"]["code"] == "CONFLICT"
    assert data["initial_password"] not in client.get("/api/admin/users").text
    headers = login(client, "NEW1", data["initial_password"])
    changed = client.post("/api/auth/change-password", headers=headers,
                          json={"old_password": data["initial_password"], "new_password": "Changed-Pass-123"})
    assert changed.status_code == 200
    assert client.get("/api/auth/me").status_code == 401
    assert client.post("/api/auth/login", json={"username": "NEW1", "password": data["initial_password"]}).status_code == 401
    login(client, "NEW1", "Changed-Pass-123")
    assert not client.get("/api/auth/me").json()["data"]["user"]["must_change_password"]


@pytest.mark.parametrize("values", [{"id": "a/b"}, {"id": "a b"}, {"name": " "},
                                    {"role": "root"}, {"password": "short"}, {"credit": 999}])
def test_create_validation(client, values):
    response = new_user(client, login(client), **values)
    assert response.status_code == 422 and response.json()["error"]["code"] == "VALIDATION_ERROR"


@pytest.mark.parametrize("username", ["S1", "T1", "A1"])
def test_all_admin_routes_reject_other_roles(client, username):
    headers = login(client, username)
    calls = [
        ("POST", "/api/admin/users", {"id": "NEW1", "name": "新账号", "role": "student"}),
        ("PUT", "/api/admin/users/S2/status", {"status": "disabled", "reason": "测试"}),
        ("POST", "/api/admin/users/S2/reset-password", {}),
        ("GET", f"/api/admin/users/{username}/sessions", None),
        ("POST", f"/api/admin/users/{username}/sessions/revoke", {}),
    ]
    for method, url, body in calls:
        r = client.request(method, url, headers=headers, **({"json": body} if body is not None else {}))
        assert r.status_code == 403, (method, url, r.text)


@pytest.mark.parametrize("method,url,body", [
    ("POST", "/api/admin/users", {"id": "NEW1", "name": "测试", "role": "student"}),
    ("PUT", "/api/admin/users/S1/status", {"status": "disabled", "reason": "测试"}),
    ("POST", "/api/admin/users/S1/reset-password", {}),
    ("GET", "/api/admin/users/S1/sessions", None),
    ("POST", "/api/admin/users/S1/sessions/revoke", {}),
    ("GET", "/api/me/sessions", None),
    ("POST", "/api/me/sessions/revoke-others", {}),
    ("PUT", "/api/me/profile", {"name": "测试"}),
])
def test_all_routes_require_authentication(client, method, url, body):
    # POST/PUT 先通过 CSRF 中间件，再验证认证依赖。
    client.cookies.set("seat_csrf", "test-csrf")
    response = client.request(method, url, headers={"X-CSRF-Token": "test-csrf"},
                              **({"json": body} if body is not None else {}))
    assert response.status_code == 401


def test_disable_and_reenable_invalidate_old_devices(client, account_app):
    headers = login(client)
    with TestClient(account_app) as student:
        login(student, "S1")
        r = client.put("/api/admin/users/S1/status", headers=headers,
                       json={"status": "disabled", "reason": "暂停使用"})
        assert r.status_code == 200 and r.json()["data"]["user"]["status"] == "disabled"
        assert student.get("/api/auth/me").status_code == 401
        assert student.post("/api/auth/login", json={"username": "S1", "password": PASSWORD}).status_code == 403
        assert client.put("/api/admin/users/S1/status", headers=headers,
                          json={"status": "active", "reason": "恢复使用"}).status_code == 200
        login(student, "S1")


def test_admin_self_disable_rejected(client):
    r = client.put("/api/admin/users/Y1/status", headers=login(client),
                   json={"status": "disabled", "reason": "测试"})
    assert r.status_code == 400 and r.json()["error"]["rule"] == "PR-14"


def test_reset_revokes_session_old_password_and_forces_change(client, account_app):
    headers = login(client)
    with TestClient(account_app) as student:
        login(student, "S1")
        response = client.post("/api/admin/users/S1/reset-password", headers=headers, json={})
        assert response.status_code == 200
        password = response.json()["data"]["initial_password"]
        assert response.headers["cache-control"] == "no-store"
        assert student.get("/api/auth/me").status_code == 401
        assert student.post("/api/auth/login", json={"username": "S1", "password": PASSWORD}).status_code == 401
        login(student, "S1", password)
        assert student.get("/api/auth/me").json()["data"]["user"]["must_change_password"]


@pytest.mark.parametrize("username", ["S1", "T1", "A1", "Y1"])
def test_every_role_can_manage_own_profile_and_sessions(client, username):
    headers = login(client, username)
    r = client.get("/api/me/sessions")
    assert r.status_code == 200 and r.json()["data"]["current"]
    assert sum(s["current"] for s in r.json()["data"]["items"]) == 1
    assert "token" not in r.text and "csrf" not in r.text
    r = client.put("/api/me/profile", headers=headers, json={"name": "新姓名", "department": ""})
    assert r.status_code == 200 and r.json()["data"]["user"]["department"] == ""
    assert client.get("/api/auth/me").json()["data"]["user"]["name"] == "新姓名"
    r = client.post("/api/me/sessions/revoke-others", headers=headers, json={})
    assert r.status_code == 200 and r.json()["data"]["revoked"] == 0


def test_keep_current_then_admin_revoke_all(client, account_app):
    admin_headers = login(client)
    with TestClient(account_app) as first, TestClient(account_app) as second:
        first_headers = login(first, "S1")
        login(second, "S1")
        sessions = client.get("/api/admin/users/S1/sessions")
        assert len(sessions.json()["data"]["items"]) == 2
        r = first.post("/api/me/sessions/revoke-others", json={}, headers=first_headers)
        assert r.json()["data"]["revoked"] == 1
        assert first.get("/api/auth/me").status_code == 200
        assert second.get("/api/auth/me").status_code == 401
        r = client.post("/api/admin/users/S1/sessions/revoke", json={}, headers=admin_headers)
        assert r.status_code == 200 and r.json()["data"]["revoked"] == 1
        assert first.get("/api/auth/me").status_code == 401


@pytest.mark.parametrize("payload", [{"role": "sys_admin"}, {"credit": 999}, {"class_name": "其他班"},
                                     {"id": "S2"}, {"name": None}, {"name": " "}, {}])
def test_profile_rejects_protected_fields_and_empty_values(client, payload):
    response = client.put("/api/me/profile", json=payload, headers=login(client, "S1"))
    assert response.status_code == 422


def test_missing_accounts_are_404(client):
    headers = login(client)
    assert client.get("/api/admin/users/missing/sessions").status_code == 404
    for action in ("reset-password", "sessions/revoke"):
        assert client.post(f"/api/admin/users/missing/{action}", headers=headers, json={}).status_code == 404
    assert client.put("/api/admin/users/missing/status", headers=headers,
                      json={"status": "disabled", "reason": "测试"}).status_code == 404


def test_csrf_required_for_all_new_mutations(client):
    login(client)
    for method, url, body in [
        ("POST", "/api/admin/users", {"id": "NEW1", "name": "测试", "role": "student"}),
        ("PUT", "/api/admin/users/S1/status", {"status": "disabled", "reason": "测试"}),
        ("POST", "/api/admin/users/S1/reset-password", {}),
        ("POST", "/api/admin/users/S1/sessions/revoke", {}),
        ("POST", "/api/me/sessions/revoke-others", {}),
        ("PUT", "/api/me/profile", {"name": "新姓名"}),
    ]:
        response = client.request(method, url, json=body)
        assert response.status_code == 403 and response.json()["error"]["code"] == "CSRF_FAILED"


def test_audits_include_ip_but_never_credentials(client, ctx):
    headers = login(client)
    generated = new_user(client, headers).json()["data"]["initial_password"]
    reset = client.post("/api/admin/users/NEW1/reset-password", headers=headers, json={}).json()["data"]["initial_password"]
    client.put("/api/admin/users/NEW1/status", headers=headers, json={"status": "disabled", "reason": "测试"})
    client.post("/api/admin/users/NEW1/sessions/revoke", headers=headers, json={})
    client.put("/api/me/profile", headers=headers, json={"name": "管理员"})
    with ctx.db.read() as conn:
        rows = [dict(row) for row in conn.execute("SELECT * FROM audit_logs")]
    assert {row["action"] for row in rows} == {"user_create", "user_reset_password", "user_status_change", "session_revoke", "profile_update"}
    assert all(row["ip"] == "testclient" for row in rows)
    assert generated not in str(rows) and reset not in str(rows) and "password_hash" not in str(rows)
