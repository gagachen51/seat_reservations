"""申诉审核接口（``POST /api/violations/{violation_id}/review``）的 HTTP 层测试。

与既有测试的分工：

* 引擎层 ``review_appeal`` 由 ``test_acceptance.py`` 的 AC-06 / AC-12 覆盖；
* 这里补 HTTP 接口层与权限矩阵——只有自习室管理员能审，其余角色一律 403
  并带回规则编号（PR-10），避免"路由改坏了权限但测试还是绿的"。
"""

from __future__ import annotations

from datetime import datetime, time

import pytest
from fastapi.testclient import TestClient

from seat_reservation.api.app import create_app
from seat_reservation.config import Settings
from seat_reservation.errors import RuleViolation
from tests.helpers import PASSWORD, Env

REVIEW_NOTE = "核查门禁日志，确认当时设备异常"


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


def appealing_violation(env: Env) -> str:
    """造一条处于「申诉中」的违规记录并返回其编号。

    S4 信用分为 55，低于门槛，创建预约会被 BR-07 拒绝并同时登记一条 no_show 违规，
    随后本人提交申诉。
    """
    with pytest.raises(RuleViolation) as exc:
        env.create("S4", "R1-005", time(16, 0), time(17, 0))
    assert exc.value.rule == "BR-07"

    violation = next(v for v in env.violations("S4") if v.violation_type == "no_show")
    appealed = env.appeal("S4", violation.id, "扫码设备故障", now=datetime(2026, 5, 11, 8, 10))
    assert appealed.ok, appealed.message
    return violation.id


def test_room_admin_review_approve_restores_credit(client: TestClient, env: Env) -> None:
    violation_id = appealing_violation(env)
    headers = login(client, "A1")

    response = client.post(
        f"/api/violations/{violation_id}/review",
        headers=headers,
        json={"decision": "approve", "note": REVIEW_NOTE},
    )
    assert response.status_code == 200, response.text
    assert response.json()["ok"] is True
    # 申诉成立：违规撤销、信用分回退（55 → 65）
    assert env.user("S4").credit == 65
    assert env.user("S4").limit_type == "none"


def test_room_admin_review_reject_keeps_violation(client: TestClient, env: Env) -> None:
    violation_id = appealing_violation(env)
    headers = login(client, "A1")

    response = client.post(
        f"/api/violations/{violation_id}/review",
        headers=headers,
        json={"decision": "reject", "note": "现场照片显示座位空置，申诉不成立"},
    )
    assert response.status_code == 200, response.text
    assert env.user("S4").credit == 55


@pytest.mark.parametrize("username", ["S4", "S1", "T1", "Y1"])
def test_review_requires_room_admin(client: TestClient, env: Env, username: str) -> None:
    """学生、教师、系统管理员都不能审核申诉（PR-10）。"""
    violation_id = appealing_violation(env)
    headers = login(client, username)

    response = client.post(
        f"/api/violations/{violation_id}/review",
        headers=headers,
        json={"decision": "approve", "note": REVIEW_NOTE},
    )
    assert response.status_code == 403, response.text
    body = response.json()
    assert body["error"]["code"] == "FORBIDDEN"
    assert body["error"]["rule"] == "PR-10"
    # 越权不能改变状态
    assert env.user("S4").credit == 55


def test_review_rejects_bad_payload(client: TestClient, env: Env) -> None:
    violation_id = appealing_violation(env)
    headers = login(client, "A1")

    response = client.post(
        f"/api/violations/{violation_id}/review",
        headers=headers,
        json={"decision": "approve", "note": "短"},
    )
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
