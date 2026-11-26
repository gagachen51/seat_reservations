"""网页交付：干净管理员初始化、首次改密规则和共享座位状态。"""
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from seat_reservation.api.app import create_app
from seat_reservation.config import Settings
from seat_reservation.storage.database import Database
from tests.test_api import login, future_day


def test_fresh_web_start_has_only_admin_and_restart_keeps_users(tmp_path):
    settings = replace(Settings.for_tests(tmp_path / 'delivery.db'), env='development', demo_mode=False)
    app = create_app(settings)
    with TestClient(app) as client:
        headers, user = login(client, 'Y0001', '12345678')
        assert not user['must_change_password']
        assert client.get('/api/admin/users').status_code == 200
        assert client.get('/api/auth/demo-accounts').status_code == 404
        assert client.post('/api/admin/system/demo-data', json={}, headers=headers).status_code == 404
        assert 'demoBox' not in client.get('/').text
        with Database(settings.db_path).read() as conn:
            assert [r['id'] for r in conn.execute('SELECT id FROM users')] == ['Y0001']
        result = client.post('/api/admin/users', headers=headers, json={
            'id': 'NEW', 'name': '新同学', 'role': 'student', 'password': 'Ignored-Pass-123',
        })
        assert result.status_code == 201
        assert result.json()['data']['initial_password'] == '12345678'
    with TestClient(create_app(settings)) as client:
        login(client, 'NEW', '12345678')
        assert client.get('/api/meta').status_code == 403


@pytest.mark.parametrize('role', ['student', 'teacher', 'room_admin', 'sys_admin'])
def test_first_login_policy_by_role(ctx, role):
    with TestClient(create_app(ctx.settings)) as client:
        headers, _ = login(client, 'Y1')
        response = client.post('/api/admin/users', headers=headers, json={
            'id': 'NEW', 'name': '新用户', 'role': role,
        })
        assert response.status_code == 201
        _, user = login(client, 'NEW', '12345678')
        assert user['must_change_password'] == (role != 'sys_admin')
        assert client.get('/api/meta').status_code == (200 if role == 'sys_admin' else 403)


def test_second_session_observes_unavailable_seat_after_booking(ctx):
    app = create_app(ctx.settings)
    with TestClient(app) as first, TestClient(app) as second:
        first_headers, _ = login(first, 'S1')
        login(second, 'S2')
        query = {'date': future_day(), 'start': '09:00', 'end': '10:00', 'only_available': 'true'}
        def available_ids():
            response = second.get('/api/seats', params=query)
            assert response.status_code == 200
            return {item['id'] for item in response.json()['data']['items']}
        assert 'R1-001' in available_ids()
        response = first.post('/api/reservations', headers=first_headers, json={
            'seat_id': 'R1-001', 'date': query['date'], 'start': '09:00', 'end': '10:00',
        })
        assert response.status_code == 201
        assert 'R1-001' not in available_ids()
