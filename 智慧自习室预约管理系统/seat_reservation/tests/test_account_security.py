"""A 模块安全整改测试（A-SEC-001 ～ A-SEC-025）。

对应：
* ``01-A模块账号管理整改任务书.md``：H-02 / M-03 / L-01 / L-03。
* ``06-A模块安全整改测试用例.md``：25 条用例逐条映射。

所有 Python 用例使用 ``ctx`` 的隔离 SQLite 与 ``helpers.seed_fixture`` 的
``S1~S5/T1/A1/Y1/R1/R2`` 夹具；HTTP 用例通过 ``TestClient`` 走真实
``create_app()``，不手工 mount Router。
"""

from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, time, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

import seat_reservation.api.routes.student_routes as student_routes
import seat_reservation.api.deps as deps
from seat_reservation.api.app import create_app
from seat_reservation.api.routes.account_routes import AccountIn, ProfileIn, StatusIn
from seat_reservation.config import Settings
from seat_reservation.domain.models import Actor, Role
from seat_reservation.errors import (
    Conflict,
    Forbidden,
    RuleViolation,
    Unauthenticated,
    ValidationFailed,
)
from seat_reservation.security import hash_token, verify_password
from seat_reservation.service.auth import AuthSession
from seat_reservation.storage.repository import SqliteRepository
from tests.helpers import NOW, PASSWORD, Env, make_credential

SR = Path(__file__).resolve().parents[1]
PASSWORD_NEW = "Changed-Pass-123"
CREATE_BODY = {
    "id": "TMP-STU",
    "name": "临时学生",
    "role": "student",
    "department": "计算机学院",
    "class_name": "软件 2301",
    "password": "12345678",
}


# ---------------------------------------------------------------------------
# 公共辅助
# ---------------------------------------------------------------------------
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


def create_via_api(client: TestClient, headers: dict[str, str], **overrides):
    body = {**CREATE_BODY, **overrides}
    return client.post("/api/admin/users", headers=headers, json=body)


def create_engine_user(env: Env, user_id: str = "TMP-STU", **overrides):
    values = {
        "user_id": user_id,
        "name": "临时学生",
        "role": "student",
        "password": "12345678",
    }
    values.update(overrides)
    with env.ctx.engine() as engine:
        return engine.create_user(env.actor("Y1"), NOW, **values)


def audit_text(ctx) -> str:
    with ctx.db.read() as conn:
        rows = [dict(row) for row in conn.execute("SELECT * FROM audit_logs")]
    return str(rows)


def user_count(ctx) -> int:
    with ctx.db.read() as conn:
        return int(conn.execute("SELECT COUNT(*) AS c FROM users").fetchone()["c"])


@contextmanager
def acs_client(env: Env, monkeypatch: pytest.MonkeyPatch, *, acs_token: str):
    """真实 app + 冻结学生路由时间 + 已登录 S1，用于 A-SEC-025。"""
    settings = replace(Settings.for_tests(env.ctx.settings.db_path), acs_token=acs_token)
    app = create_app(settings)

    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):  # noqa: ANN001
            return datetime(2026, 5, 11, 8, 50, 0)

    monkeypatch.setattr(student_routes, "datetime", FrozenDatetime)
    with TestClient(app) as client:
        login_response = client.post(
            "/api/auth/login", json={"username": "S1", "password": PASSWORD}
        )
        assert login_response.status_code == 200, login_response.text
        yield client


# ---------------------------------------------------------------------------
# A-SEC-001 账号创建参数校验
# ---------------------------------------------------------------------------
def test_a_sec_001_create_validation_engine(env: Env) -> None:
    before = user_count(env.ctx)
    cases = [
        dict(user_id=""),
        dict(user_id="A/B"),
        dict(user_id="A\\B"),
        dict(user_id="A B"),
        dict(user_id="x" * 65),
        dict(name=" "),
        dict(role="super_admin"),
        dict(password="1234567"),
    ]
    for values in cases:
        with pytest.raises(ValidationFailed):
            create_engine_user(env, **values)
        assert user_count(env.ctx) == before
        assert env.user("TMP-STU") is None
    assert "users" not in str(ValidationFailed)


# ---------------------------------------------------------------------------
# A-SEC-002 最后管理员与自停用保护
# ---------------------------------------------------------------------------
def test_a_sec_002_last_admin_and_self_disable(env: Env) -> None:
    with env.ctx.engine() as engine:
        with pytest.raises(RuleViolation):
            engine.set_user_role(env.actor("Y1"), "Y1", "student", NOW)
        with pytest.raises(RuleViolation):
            engine.set_user_status(env.actor("Y1"), "Y1", "disabled", "测试", NOW)
    create_engine_user(env, "Y2", role="sys_admin")
    with env.ctx.engine() as engine:
        result = engine.set_user_status(env.actor("Y1"), "Y2", "disabled", "第二管理员", NOW)
    assert result.ok and not env.user("Y2").is_active


# ---------------------------------------------------------------------------
# A-SEC-003 重置密码生成一次性密码并撤销会话
# ---------------------------------------------------------------------------
def test_a_sec_003_reset_password_is_one_time_and_revokes_sessions(env: Env) -> None:
    token1, _, _ = env.ctx.auth.login("S1", PASSWORD, now=NOW)
    token2, _, _ = env.ctx.auth.login("S1", PASSWORD, now=NOW)
    with env.ctx.engine() as engine:
        data = engine.reset_user_password(env.actor("Y1"), "S1", NOW).data
    initial = data["initial_password"]
    user = env.user("S1")
    assert initial and verify_password(initial, user.password_hash)
    assert user.must_change_password is True
    assert env.ctx.auth.authenticate(token1, now=NOW) is None
    assert env.ctx.auth.authenticate(token2, now=NOW) is None
    with env.ctx.db.read() as conn:
        assert conn.execute("SELECT COUNT(*) FROM sessions WHERE user_id='S1'").fetchone()[0] == 0
    assert initial not in audit_text(env.ctx)
    assert user.password_hash not in audit_text(env.ctx)


# ---------------------------------------------------------------------------
# A-SEC-004 强制改密依赖的单元行为
# ---------------------------------------------------------------------------
def test_a_sec_004_force_password_gate_unit(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    assert deps._PASSWORD_CHANGE_ALLOWED == {
        "/api/auth/me",
        "/api/auth/change-password",
        "/api/auth/logout",
    }
    user = env.user("S1")
    assert user is not None
    user.must_change_password = True
    session = AuthSession(
        user=user,
        token_hash="token-hash",
        csrf_token="csrf",
        expires_at=NOW + timedelta(hours=1),
        last_seen_at=NOW,
    )
    monkeypatch.setattr(env.ctx.auth, "authenticate", lambda token, now=None: session)

    class Request:
        cookies = {env.ctx.settings.cookie_name: "session-token"}
        def __init__(self, path: str) -> None:
            self.url = SimpleNamespace(path=path)

    for path in deps._PASSWORD_CHANGE_ALLOWED:
        assert deps.get_session(Request(path), env.ctx).user.id == "S1"
    for path in ("/api/meta", "/api/reservations", "/api/admin/users"):
        with pytest.raises(Forbidden) as exc:
            deps.get_session(Request(path), env.ctx)
        assert exc.value.code == "PASSWORD_CHANGE_REQUIRED"


# ---------------------------------------------------------------------------
# A-SEC-005 个人资料模型只允许姓名/院系
# ---------------------------------------------------------------------------
def test_a_sec_005_profile_model_rejects_extra_fields() -> None:
    assert ProfileIn(name="新姓名", department="新院系").name == "新姓名"
    for payload in (
        {"name": "x", "role": "sys_admin"},
        {"name": "x", "credit": 999},
        {"name": "x", "class_name": "其他班"},
        {"name": "x", "status": "disabled"},
        {"name": "x", "user_id": "S2"},
    ):
        with pytest.raises(ValidationError):
            ProfileIn(**payload)
    with pytest.raises(ValidationError):
        ProfileIn(name=None)


# ---------------------------------------------------------------------------
# A-SEC-006 sysadmin 创建账号成功
# ---------------------------------------------------------------------------
def test_a_sec_006_sysadmin_create_account_api(api: TestClient, ctx) -> None:
    headers = login(api)
    response = api.post(
        "/api/admin/users",
        headers=headers,
        json={k: v for k, v in CREATE_BODY.items() if k != "password"},
    )
    assert response.status_code == 201, response.text
    data = response.json()["data"]
    assert data["user"]["must_change_password"] is True
    assert data["initial_password"] and "password_hash" not in data["user"]
    assert api.get("/api/admin/users", headers=headers).status_code == 200
    with ctx.db.read() as conn:
        assert conn.execute("SELECT COUNT(*) FROM users WHERE id='TMP-STU'").fetchone()[0] == 1
        assert conn.execute(
            "SELECT COUNT(*) FROM audit_logs WHERE action='user_create' AND target_id='TMP-STU'"
        ).fetchone()[0] == 1


# ---------------------------------------------------------------------------
# A-SEC-007 room_admin/student 创建账号被拒绝
# ---------------------------------------------------------------------------
def test_a_sec_007_non_sysadmin_cannot_create(api: TestClient, ctx) -> None:
    before = user_count(ctx)
    for username in ("A1", "S1"):
        response = api.post("/api/admin/users", headers=login(api, username), json=CREATE_BODY)
        assert response.status_code == 403, (username, response.text)
        assert response.json()["error"]["rule"] == "PR-14"
    assert user_count(ctx) == before


# ---------------------------------------------------------------------------
# A-SEC-008 停用/启用账号与自停用
# ---------------------------------------------------------------------------
def test_a_sec_008_disable_enable_and_self_disable(api: TestClient, ctx) -> None:
    admin_headers = login(api)
    student = TestClient(api.app)
    login(student, "S1")
    disabled = api.put(
        "/api/admin/users/S1/status",
        headers=admin_headers,
        json={"status": "disabled", "reason": "安全测试"},
    )
    assert disabled.status_code == 200
    assert student.get("/api/meta").status_code == 401
    enabled = api.put(
        "/api/admin/users/S1/status",
        headers=admin_headers,
        json={"status": "active", "reason": "恢复"},
    )
    assert enabled.status_code == 200
    self_disable = api.put(
        "/api/admin/users/Y1/status",
        headers=admin_headers,
        json={"status": "disabled", "reason": "自停用"},
    )
    assert self_disable.status_code == 400
    assert self_disable.json()["error"]["rule"] == "PR-14"
    with ctx.db.read() as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM audit_logs WHERE action='user_status_change'"
        ).fetchone()[0] >= 2


# ---------------------------------------------------------------------------
# A-SEC-009 重置密码接口
# ---------------------------------------------------------------------------
def test_a_sec_009_reset_password_api(api: TestClient) -> None:
    admin_headers = login(api)
    student = TestClient(api.app)
    login(student, "S1")
    response = api.post("/api/admin/users/S1/reset-password", headers=admin_headers, json={})
    assert response.status_code == 200, response.text
    initial = response.json()["data"]["initial_password"]
    assert "password_hash" not in response.text and "token_hash" not in response.text
    assert student.get("/api/meta").status_code == 401
    assert api.post("/api/auth/login", json={"username": "S1", "password": PASSWORD}).status_code == 401
    assert api.post("/api/auth/login", json={"username": "S1", "password": initial}).status_code == 200
    assert api.get("/api/auth/me").json()["data"]["user"]["must_change_password"] is True


# ---------------------------------------------------------------------------
# A-SEC-010 会话列表与退出其他设备
# ---------------------------------------------------------------------------
def test_a_sec_010_session_list_and_revoke_others(api: TestClient) -> None:
    first = TestClient(api.app)
    second = TestClient(api.app)
    first_headers = login(first, "S1")
    login(second, "S1")
    other = TestClient(api.app)
    login(other, "S2")
    listing = first.get("/api/me/sessions")
    assert listing.status_code == 200
    assert listing.json()["data"]["current"] is True
    assert "token_hash" not in listing.text and "csrf" not in listing.text
    revoked = first.post("/api/me/sessions/revoke-others", headers=first_headers, json={})
    assert revoked.status_code == 200 and revoked.json()["data"]["revoked"] == 1
    assert first.get("/api/meta").status_code == 200
    assert second.get("/api/meta").status_code == 401
    assert other.get("/api/meta").status_code == 200


# ---------------------------------------------------------------------------
# A-SEC-011 最后 active sysadmin 不能降级/停用
# ---------------------------------------------------------------------------
def test_a_sec_011_last_sysadmin_api_guard(api: TestClient) -> None:
    headers = login(api)
    demote = api.put("/api/admin/users/Y1/role", headers=headers, json={"role": "student"})
    assert demote.status_code == 400 and demote.json()["error"]["rule"] == "PR-14"
    disable = api.put(
        "/api/admin/users/Y1/status",
        headers=headers,
        json={"status": "disabled", "reason": "自停用"},
    )
    assert disable.status_code == 400 and disable.json()["error"]["rule"] == "PR-14"
    created = create_via_api(api, headers, id="TMP-ADMIN", name="第二管理员", role="sys_admin", password=None)
    assert created.status_code == 201
    demote_after = api.put("/api/admin/users/Y1/role", headers=headers, json={"role": "student"})
    assert demote_after.status_code == 200, demote_after.text


# ---------------------------------------------------------------------------
# A-SEC-012 角色变更对现有会话的权限立即生效
# ---------------------------------------------------------------------------
def test_a_sec_012_role_change_is_immediate(api: TestClient, env: Env) -> None:
    room_admin = TestClient(api.app)
    login(room_admin, "A1")
    with env.ctx.engine() as engine:
        engine.set_user_role(env.actor("Y1"), "A1", "student", NOW)
    assert room_admin.get("/api/admin/analytics").status_code == 403
    assert room_admin.get("/api/admin/users").status_code == 403
    assert room_admin.get("/api/meta").status_code == 200


# ---------------------------------------------------------------------------
# A-SEC-013 个人资料不能越权修改
# ---------------------------------------------------------------------------
def test_a_sec_013_profile_cannot_escalate(api: TestClient, env: Env) -> None:
    headers = login(api, "S1")
    changed = api.put("/api/me/profile", headers=headers, json={"name": "新姓名", "department": "新院系"})
    assert changed.status_code == 200
    assert env.user("S1").name == "新姓名"
    before = env.user("S1")
    for payload in (
        {"name": "x", "role": "sys_admin"},
        {"name": "x", "credit": 100},
        {"name": "x", "class_name": "其他班"},
        {"name": "x", "status": "disabled"},
    ):
        response = api.put("/api/me/profile", headers=headers, json=payload)
        assert response.status_code == 422
    after = env.user("S1")
    assert (after.role, after.credit, after.class_name, after.status) == (
        before.role, before.credit, before.class_name, before.status
    )


# ---------------------------------------------------------------------------
# A-SEC-014 并发创建同 ID 账号
# ---------------------------------------------------------------------------
def test_a_sec_014_concurrent_create_same_id(env: Env) -> None:
    actor = env.actor("Y1")

    def attempt(_):
        try:
            with env.ctx.engine() as engine:
                engine.create_user(actor, NOW, user_id="TMP-RACE", name="并发账号", role="student")
            return "created"
        except Conflict:
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = sorted(pool.map(attempt, range(2)))
    assert results == ["conflict", "created"]
    assert user_count(env.ctx) == 9  # 8 个 seed 用户 + 1 个 TMP-RACE


# ---------------------------------------------------------------------------
# A-SEC-015 强制改密完整流程
# ---------------------------------------------------------------------------
def test_a_sec_015_force_password_full_flow(api: TestClient) -> None:
    admin_headers = login(api)
    created = create_via_api(api, admin_headers, password="12345678")
    assert created.status_code == 201, created.text
    login(api, "TMP-STU", "12345678")
    headers = {"X-CSRF-Token": api.cookies.get("seat_csrf") or ""}
    for method, url in (("GET", "/api/meta"), ("GET", "/api/reservations"), ("GET", "/api/me/dashboard")):
        response = api.request(method, url, headers=headers)
        assert response.status_code == 403, (url, response.text)
        assert response.json()["error"]["code"] == "PASSWORD_CHANGE_REQUIRED"
    assert api.get("/api/auth/me").status_code == 200
    changed = api.post(
        "/api/auth/change-password",
        headers=headers,
        json={"old_password": "12345678", "new_password": PASSWORD_NEW},
    )
    assert changed.status_code == 200
    assert api.get("/api/auth/me").status_code == 401
    login(api, "TMP-STU", PASSWORD_NEW)
    assert api.get("/api/meta").status_code == 200
    assert api.get("/api/reservations").status_code == 200


# ---------------------------------------------------------------------------
# A-SEC-016 room_admin 账号列表范围
# ---------------------------------------------------------------------------
def test_a_sec_016_room_admin_scope_and_fields(api: TestClient) -> None:
    room_headers = login(api, "A1")
    response = api.get("/api/admin/users", params={"page_size": 100}, headers=room_headers)
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["total"] == 5
    assert {item["role"] for item in data["items"]} == {"student"}
    expected = {"id", "name", "role", "role_label", "department", "class_name", "status"}
    assert all(set(item) == expected for item in data["items"])
    assert "must_change_password" not in response.text
    assert "credit" not in response.text
    assert "password_hash" not in response.text and "token_hash" not in response.text
    for role in ("sys_admin", "room_admin", "teacher"):
        denied = api.get("/api/admin/users", params={"role": role}, headers=room_headers)
        assert denied.status_code == 403 and denied.json()["error"]["rule"] == "PR-18"
    student_only = api.get("/api/admin/students", headers=room_headers)
    assert student_only.status_code == 200
    assert {item["role"] for item in student_only.json()["data"]["items"]} == {"student"}

    sys_headers = login(api, "Y1")
    all_users = api.get("/api/admin/users", params={"page_size": 100}, headers=sys_headers)
    assert all_users.status_code == 200
    assert {"Y1", "A1", "T1"} <= {item["id"] for item in all_users.json()["data"]["items"]}
    assert any("must_change_password" in item for item in all_users.json()["data"]["items"])


# ---------------------------------------------------------------------------
# A-SEC-017 student/teacher 账号管理接口拒绝
# ---------------------------------------------------------------------------
def test_a_sec_017_student_teacher_account_admin_denied(api: TestClient, ctx) -> None:
    before = user_count(ctx)
    calls = [
        ("POST", "/api/admin/users", CREATE_BODY),
        ("PUT", "/api/admin/users/S2/status", {"status": "disabled", "reason": "测试"}),
        ("POST", "/api/admin/users/S2/reset-password", {}),
        ("GET", "/api/admin/users/S1/sessions", None),
        ("POST", "/api/admin/users/S1/sessions/revoke", {}),
    ]
    for username in ("S1", "T1"):
        headers = login(api, username)
        for method, url, body in calls:
            response = api.request(method, url, headers=headers, json=body)
            assert response.status_code == 403, (username, method, url, response.text)
    assert user_count(ctx) == before


# ---------------------------------------------------------------------------
# A-SEC-018 账号接口 CSRF 保护
# ---------------------------------------------------------------------------
def test_a_sec_018_account_mutations_require_csrf(api: TestClient, ctx) -> None:
    headers = login(api)
    before = user_count(ctx)
    no_token = api.post("/api/admin/users", json=CREATE_BODY)
    assert no_token.status_code == 403 and no_token.json()["error"]["code"] == "CSRF_FAILED"
    bad_token = api.post(
        "/api/admin/users", headers={"X-CSRF-Token": "wrong"}, json=CREATE_BODY
    )
    assert bad_token.status_code == 403 and bad_token.json()["error"]["code"] == "CSRF_FAILED"
    assert user_count(ctx) == before
    good = api.post("/api/admin/users", headers=headers, json=CREATE_BODY)
    assert good.status_code == 201


# ---------------------------------------------------------------------------
# A-SEC-019 停用/重置不泄露敏感信息
# ---------------------------------------------------------------------------
def test_a_sec_019_responses_and_audit_have_no_credentials(api: TestClient, ctx) -> None:
    headers = login(api)
    created = create_via_api(api, headers, password=None)
    initial = created.json()["data"]["initial_password"]
    reset = api.post("/api/admin/users/TMP-STU/reset-password", headers=headers, json={})
    reset_initial = reset.json()["data"]["initial_password"]
    status = api.put(
        "/api/admin/users/TMP-STU/status",
        headers=headers,
        json={"status": "disabled", "reason": "安全检查"},
    )
    assert status.status_code == 200
    combined = created.text + reset.text + status.text
    assert "password_hash" not in combined
    assert "token_hash" not in combined
    assert "seat_session" not in combined and "seat_csrf" not in combined
    stored = audit_text(ctx)
    assert initial not in stored and reset_initial not in stored
    assert "password_hash" not in stored and "token_hash" not in stored


# ---------------------------------------------------------------------------
# A-SEC-020 非法账号输入边界
# ---------------------------------------------------------------------------
def test_a_sec_020_invalid_account_input_boundaries(api: TestClient, ctx) -> None:
    headers = login(api)
    before = user_count(ctx)
    invalid = [
        {"id": ""},
        {"id": "A/B"},
        {"id": "A B"},
        {"id": "x" * 65},
        {"name": ""},
        {"name": "x" * 101},
        {"role": "super_admin"},
        {"password": "1234567"},
        {"password": "x" * 129},
    ]
    for values in invalid:
        response = create_via_api(api, headers, **values)
        assert response.status_code == 422, (values, response.text)
        assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    assert user_count(ctx) == before
    valid = create_via_api(
        api,
        headers,
        id="A",
        name="合" * 100,
        password="12345678",
        role="student",
    )
    assert valid.status_code == 201, valid.text


# ---------------------------------------------------------------------------
# A-SEC-021 不存在用户/非法状态
# ---------------------------------------------------------------------------
def test_a_sec_021_missing_user_and_invalid_status(api: TestClient, ctx) -> None:
    headers = login(api)
    before_audit = len(audit_text(ctx))
    assert api.get("/api/admin/users/NO-SUCH/sessions", headers=headers).status_code == 404
    for action in ("reset-password", "sessions/revoke"):
        response = api.post(f"/api/admin/users/NO-SUCH/{action}", headers=headers, json={})
        assert response.status_code == 404
    invalid = api.put(
        "/api/admin/users/S1/status",
        headers=headers,
        json={"status": "deleted", "reason": "测试"},
    )
    assert invalid.status_code == 422 and invalid.json()["error"]["code"] == "VALIDATION_ERROR"
    assert len(audit_text(ctx)) == before_audit
    assert "sqlite" not in invalid.text.lower()


# ---------------------------------------------------------------------------
# A-SEC-022 前端登录/登出密码清理
# ---------------------------------------------------------------------------
def test_a_sec_022_frontend_password_cleanup_static() -> None:
    source = (SR / "src/seat_reservation/web/app.js").read_text(encoding="utf-8")
    assert "lastLoginPassword" not in source
    assert "clearLoginSecrets" in source
    assert "resetClientState" in source
    assert "input[type=\"password\"]" in source
    # 改密表单不得再把旧密码写入 value 属性
    start = source.index("function changePasswordSheet")
    end = source.index("async function openNotifications", start)
    assert 'value="' not in source[start:end]
    # 登录请求结束后必须清空密码输入框
    login_block = source[source.index("async function submitLogin"):source.index("async function logout")]
    assert "passwordField.value = ''" in login_block


# ---------------------------------------------------------------------------
# A-SEC-023 真实应用路由表包含账号接口
# ---------------------------------------------------------------------------
def test_a_sec_023_real_openapi_has_eight_account_routes(api: TestClient) -> None:
    response = api.get("/openapi.json")
    assert response.status_code == 200
    spec = response.json()
    expected = {
        "/api/admin/users": {"post"},
        "/api/admin/users/{user_id}/status": {"put"},
        "/api/admin/users/{user_id}/reset-password": {"post"},
        "/api/admin/users/{user_id}/sessions": {"get"},
        "/api/admin/users/{user_id}/sessions/revoke": {"post"},
        "/api/me/sessions": {"get"},
        "/api/me/sessions/revoke-others": {"post"},
        "/api/me/profile": {"put"},
    }
    for path, methods in expected.items():
        assert path in spec["paths"], path
        assert methods <= set(spec["paths"][path]), path
    operations = [
        operation
        for path in spec["paths"].values()
        for operation in path.values()
        if isinstance(operation, dict)
    ]
    ids = [item.get("operationId") for item in operations if item.get("operationId")]
    assert len(ids) == len(set(ids))
    assert api.get("/api/me/sessions").status_code == 401
    login(api)
    assert api.get("/api/me/sessions").status_code == 200


# ---------------------------------------------------------------------------
# A-SEC-024 A 与共享认证集成
# ---------------------------------------------------------------------------
def test_a_sec_024_force_password_and_csrf_integration(api: TestClient) -> None:
    admin_headers = login(api)
    created = create_via_api(api, admin_headers, password="12345678")
    assert created.status_code == 201
    login(api, "TMP-STU", "12345678")
    no_csrf = api.post("/api/reservations", json={})
    assert no_csrf.status_code == 403 and no_csrf.json()["error"]["code"] == "CSRF_FAILED"
    csrf_headers = {"X-CSRF-Token": api.cookies.get("seat_csrf") or ""}
    forced = api.get("/api/meta", headers=csrf_headers)
    assert forced.status_code == 403 and forced.json()["error"]["code"] == "PASSWORD_CHANGE_REQUIRED"
    changed = api.post(
        "/api/auth/change-password",
        headers=csrf_headers,
        json={"old_password": "12345678", "new_password": PASSWORD_NEW},
    )
    assert changed.status_code == 200
    assert api.get("/api/auth/me").status_code == 401
    login(api, "TMP-STU", PASSWORD_NEW)
    assert api.get("/api/meta", headers={"X-CSRF-Token": api.cookies.get("seat_csrf") or ""}).status_code == 200


# ---------------------------------------------------------------------------
# A-SEC-025 A 与 C 账号状态集成
# ---------------------------------------------------------------------------
def test_a_sec_025_disabled_account_blocks_acs_and_session(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    created = env.create("S1", "R1-001", start=time(9, 0), end=time(10, 0))
    assert created.ok, created.message
    reservation_id = created.data["id"]
    code = make_credential(env, "S1", reservation_id, now=datetime(2026, 5, 11, 8, 49, 30))
    with acs_client(env, monkeypatch, acs_token="acs-secret") as student:
        with env.ctx.engine() as engine:
            engine.set_user_status(env.actor("Y1"), "S1", "disabled", "安全测试", NOW)
        assert student.get("/api/meta").status_code == 401
        acs = student.post(
            "/api/acs/check-in",
            json={"code": code},
            headers={"X-ACS-Token": "acs-secret"},
        )
        assert acs.status_code == 403, acs.text
        assert env.reservation(reservation_id).status == "pending"
        assert env.violations("S1") == []
