"""局域网启动配置与两个独立会话共享预约数据。"""

from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from seat_reservation import cli, network
from seat_reservation.api.app import create_app
from tests.test_api import future_day, login


@pytest.mark.parametrize("options,env_host,env_port,expected", [
    ([], "127.0.0.1", "8010", ("127.0.0.1", 8010)),
    ([], "192.168.1.10", "8020", ("192.168.1.10", 8020)),
    (["--lan"], "127.0.0.1", "8020", ("0.0.0.0", 8020)),
    (["--host", "127.0.0.2", "--port", "8030"], "0.0.0.0", "8020", ("127.0.0.2", 8030)),
])
def test_listener_configuration(monkeypatch, options, env_host, env_port, expected):
    monkeypatch.setenv("SEAT_HOST", env_host)
    monkeypatch.setenv("SEAT_PORT", env_port)
    monkeypatch.setenv("SEAT_ENV", "test")
    # cmd_serve writes this variable; keep the test environment isolated.
    monkeypatch.setenv("SEAT_DEMO_MODE", "false")
    calls = []
    monkeypatch.setattr("uvicorn.run", lambda app, **kwargs: calls.append(kwargs))
    monkeypatch.setattr(network, "local_ipv4_addresses", lambda: [])
    args = cli.build_parser().parse_args(["serve", *options])
    assert cli.cmd_serve(args) == 0
    assert (calls[0]["host"], calls[0]["port"]) == expected


@pytest.mark.parametrize("options", [
    ["--lan", "--host", "127.0.0.1"], ["--port", "0"],
    ["--port", "65536"], ["--port", "abc"],
])
def test_invalid_listener_options(options):
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(["serve", *options])


def test_address_candidates(monkeypatch):
    ips = ["127.0.0.1", "0.0.0.0", "169.254.1.2", "192.168.1.10", "192.168.1.10", "10.0.0.2"]
    monkeypatch.setattr(network.socket, "getaddrinfo", lambda *args: [
        (2, 1, 6, "", (ip, 0)) for ip in ips
    ])
    assert network.local_ipv4_addresses() == ["10.0.0.2", "192.168.1.10"]


def test_address_detection_failure_is_nonfatal(monkeypatch, capsys):
    def fail(*args):
        raise OSError("hostname unavailable")
    monkeypatch.setattr(network.socket, "getaddrinfo", fail)
    network.print_access_addresses("0.0.0.0", 8010)
    output = capsys.readouterr().out
    assert "ipconfig" in output
    assert "http://127.0.0.1:8010/" in output
    assert "http://0.0.0.0" not in output


def test_two_sessions_compete_for_one_seat(ctx):
    app = create_app(ctx.settings)
    with TestClient(app) as first, TestClient(app) as second:
        headers1, user1 = login(first, "S1")
        headers2, user2 = login(second, "S2")
        assert user1["id"] != user2["id"]
        payload = {"seat_id": "R1-001", "date": future_day(), "start": "09:00", "end": "10:00"}
        def reserve(pair):
            client, headers = pair
            return client.post("/api/reservations", json=payload, headers=headers)
        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(reserve, [(first, headers1), (second, headers2)]))
        assert sorted(r.status_code for r in responses) == [201, 409], [r.text for r in responses]
        with ctx.db.read() as conn:
            assert conn.execute("SELECT COUNT(*) FROM reservations WHERE seat_id=? AND date=?",
                                (payload["seat_id"], payload["date"])).fetchone()[0] == 1
