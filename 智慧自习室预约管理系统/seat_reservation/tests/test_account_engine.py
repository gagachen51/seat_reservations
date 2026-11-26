"""账号规则、事务原子性与会话保密；不修改公共夹具。"""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta, time

import pytest

from seat_reservation.domain.models import Actor, Role
from seat_reservation.errors import Conflict, Forbidden, RuleViolation, Unauthenticated, ValidationFailed
from seat_reservation.security import hash_token, verify_password
from seat_reservation.storage.repository import SqliteRepository
from tests.helpers import NOW, PASSWORD


def create(env, **kwargs):
    values = dict(user_id="NEW1", name="新同学", role="student")
    values.update(kwargs)
    with env.ctx.engine() as engine:
        return engine.create_user(env.actor("Y1"), NOW, **values).data


@pytest.mark.parametrize("role", ["student", "teacher", "room_admin", "sys_admin"])
def test_create_roles_and_secret_only_returned_once(env, role):
    data = create(env, role=role)
    user = env.user("NEW1")
    assert user.role == role and user.must_change_password and user.credit == 100
    assert verify_password(data["initial_password"], user.password_hash)
    with env.ctx.db.read() as conn:
        audit = conn.execute("SELECT after_json FROM audit_logs WHERE action='user_create'").fetchone()[0]
    assert data["initial_password"] not in audit and user.password_hash not in audit
    assert "initial_password" not in user.as_dict() and "password_hash" not in data["user"]


def test_create_with_supplied_password_and_trimmed_fields(env):
    data = create(env, user_id=" NEW1 ", name=" 新同学 ", password=PASSWORD)
    assert "initial_password" not in data
    assert env.user("NEW1").name == "新同学"
    assert verify_password(PASSWORD, env.user("NEW1").password_hash)


@pytest.mark.parametrize("values", [dict(role="root"), dict(user_id=""), dict(user_id="a/b"),
                                    dict(user_id="a b"), dict(name=" "), dict(password="short")])
def test_invalid_account_rejected_without_writes(env, values):
    with pytest.raises(ValidationFailed):
        create(env, **values)
    assert env.user("NEW1") is None


def test_duplicate_does_not_overwrite_existing_user(env):
    before = env.user("S1")
    with pytest.raises(Conflict):
        create(env, user_id="S1")
    assert env.user("S1") == before


def test_concurrent_create_same_id_only_one_success(env):
    actor = env.actor("Y1")
    def attempt(_):
        try:
            with env.ctx.engine() as engine:
                engine.create_user(actor, NOW, user_id="RACE", name="并发账号", role="student")
            return "created"
        except Conflict:
            return "conflict"
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(attempt, range(2))) == ["conflict", "created"]


def test_disable_revokes_sessions_but_preserves_reservations_and_reenable(env):
    reservation = env.create("S1", "R1-001", time(9), time(10)).data
    token, _, _ = env.ctx.auth.login("S1", PASSWORD, now=NOW)
    with env.ctx.engine() as engine:
        engine.set_user_status(env.actor("Y1"), "S1", "disabled", "暂时停用", NOW)
    assert env.ctx.auth.authenticate(token, now=NOW) is None
    assert env.reservation(reservation["id"]).status == "pending"
    with pytest.raises(Forbidden):
        env.ctx.auth.login("S1", PASSWORD, now=NOW)
    with env.ctx.engine() as engine:
        engine.set_user_status(env.actor("Y1"), "S1", "active", "恢复使用", NOW)
    assert env.ctx.auth.login("S1", PASSWORD, now=NOW)[2].is_active


def test_cannot_disable_self_or_last_active_admin(env):
    with env.ctx.engine() as engine:
        with pytest.raises(RuleViolation):
            engine.set_user_status(env.actor("Y1"), "Y1", "disabled", "测试", NOW)
    # 领域级验证最后管理员保护，即使调用方身份来自另一管理通道。
    with env.ctx.engine() as engine:
        with pytest.raises(RuleViolation):
            engine.set_user_status(Actor(id="CLI", name="管理", role=Role.SYS_ADMIN),
                                   "Y1", "disabled", "测试", NOW)
    assert env.user("Y1").is_active


def test_disabled_admin_does_not_count_and_last_admin_cannot_be_demoted(env):
    create(env, user_id="Y2", role="sys_admin")
    with env.ctx.engine() as engine:
        engine.set_user_status(env.actor("Y1"), "Y2", "disabled", "停用", NOW)
    with env.ctx.engine() as engine:
        with pytest.raises(RuleViolation):
            engine.set_user_role(env.actor("Y1"), "Y1", "student", NOW)


def test_can_disable_another_admin_when_one_remains(env):
    create(env, user_id="Y2", role="sys_admin")
    with env.ctx.engine() as engine:
        engine.set_user_status(env.actor("Y1"), "Y2", "disabled", "停用", NOW)
    assert not env.user("Y2").is_active


def test_reset_invalidates_password_sessions_and_records_safe_audit(env):
    token, _, _ = env.ctx.auth.login("S1", PASSWORD, now=NOW)
    with env.ctx.engine() as engine:
        password = engine.reset_user_password(env.actor("Y1"), "S1", NOW).data["initial_password"]
    assert env.ctx.auth.authenticate(token, now=NOW) is None
    with pytest.raises(Unauthenticated):
        env.ctx.auth.login("S1", PASSWORD, now=NOW)
    assert env.ctx.auth.login("S1", password, now=NOW)[2].must_change_password
    with env.ctx.db.read() as conn:
        audit = conn.execute("SELECT after_json FROM audit_logs WHERE action='user_reset_password'").fetchone()[0]
    assert password not in audit and env.user("S1").password_hash not in audit


def test_session_list_hides_all_secrets_and_expired_sessions(env):
    token, csrf, _ = env.ctx.auth.login("S1", PASSWORD, now=NOW)
    env.ctx.auth.login("S1", PASSWORD, now=NOW - timedelta(minutes=31))
    env.ctx.auth.login("S1", PASSWORD, now=NOW - timedelta(hours=9))
    with env.ctx.engine(write=False) as engine:
        data = engine.list_user_sessions(env.actor("S1"), "S1", NOW, current_token_hash=hash_token(token))
    assert len(data["items"]) == 1 and data["items"][0]["current"] and data["current"]
    assert all(secret not in str(data) for secret in (token, csrf, hash_token(token)))
    assert set(data["items"][0]) == {"created_at", "last_seen_at", "expires_at", "ip", "user_agent", "current"}


def test_session_list_uses_configured_idle_timeout(env):
    token, _, _ = env.ctx.auth.login("S1", PASSWORD, now=NOW - timedelta(minutes=10))
    with env.ctx.engine(write=False) as engine:
        data = engine.list_user_sessions(env.actor("S1"), "S1", NOW,
                                        current_token_hash=hash_token(token), idle_minutes=5)
    assert data["items"] == []


def test_revoke_others_preserves_only_current_and_not_other_users(env):
    current, _, _ = env.ctx.auth.login("S1", PASSWORD, now=NOW)
    other, _, _ = env.ctx.auth.login("S1", PASSWORD, now=NOW)
    unrelated, _, _ = env.ctx.auth.login("S2", PASSWORD, now=NOW)
    with env.ctx.engine() as engine:
        result = engine.revoke_user_sessions(env.actor("S1"), "S1", NOW,
                                             except_token_hash=hash_token(current))
    assert result.data == {"revoked": 1}
    assert env.ctx.auth.authenticate(current, now=NOW)
    assert env.ctx.auth.authenticate(other, now=NOW) is None
    assert env.ctx.auth.authenticate(unrelated, now=NOW)


def test_invalid_keep_token_cannot_revoke_all(env):
    token, _, _ = env.ctx.auth.login("S1", PASSWORD, now=NOW)
    with env.ctx.engine() as engine:
        with pytest.raises(RuleViolation):
            engine.revoke_user_sessions(env.actor("S1"), "S1", NOW, except_token_hash="invalid")
    assert env.ctx.auth.authenticate(token, now=NOW)


def test_session_privacy_and_permission_boundary(env):
    with env.ctx.engine() as engine:
        with pytest.raises(Forbidden):
            engine.list_user_sessions(env.actor("S1"), "S2", NOW, current_token_hash="")
        with pytest.raises(Forbidden):
            engine.revoke_user_sessions(env.actor("S1"), "S2", NOW)
        with pytest.raises(Forbidden):
            engine.create_user(env.actor("A1"), NOW, user_id="NEW1", name="越权", role="student")


def test_profile_updates_only_allowed_fields(env):
    before = env.user("S1")
    with env.ctx.engine() as engine:
        engine.update_my_profile(env.actor("S1"), NOW, name=" 新姓名 ", department="")
    user = env.user("S1")
    assert user.name == "新姓名" and user.department == ""
    assert (user.role, user.credit, user.class_name) == (before.role, before.credit, before.class_name)


def test_repository_single_session_delete_is_scoped(env):
    token, _, _ = env.ctx.auth.login("S1", PASSWORD, now=NOW)
    other, _, _ = env.ctx.auth.login("S2", PASSWORD, now=NOW)
    with env.ctx.db.transaction() as conn:
        assert SqliteRepository(conn).delete_session(hash_token(token)) == 1
    assert env.ctx.auth.authenticate(other, now=NOW)
    assert env.ctx.auth.authenticate(token, now=NOW) is None


def test_failure_rolls_back_account_and_audit(env):
    with pytest.raises(RuntimeError):
        with env.ctx.engine() as engine:
            engine.create_user(env.actor("Y1"), NOW, user_id="ROLLBACK", name="回滚", role="student")
            raise RuntimeError("simulate transaction failure")
    assert env.user("ROLLBACK") is None
    with env.ctx.db.read() as conn:
        assert conn.execute("SELECT COUNT(*) FROM audit_logs WHERE target_id='ROLLBACK'").fetchone()[0] == 0
