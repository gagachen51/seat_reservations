"""浏览器端到端冒烟：真实 HTTP 服务 + 真实 DOM。

默认跳过；启用方式：

    pip install playwright
    python -m playwright install msedge
    SEAT_E2E=1 python -m pytest tests/test_e2e.py -q

断言的是用户真正会走的路：登录 → 选座 → 预约 → 列表 → 凭证 →
管理员看板，并收集页面 JS 异常。
"""

from __future__ import annotations

import os
import socket
import threading
import time
import uuid
from pathlib import Path

import httpx
import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("SEAT_E2E") != "1",
    reason="端到端测试默认跳过，设置 SEAT_E2E=1 启用",
)

sync_api = pytest.importorskip("playwright.sync_api", reason="未安装 playwright")

from seat_reservation.api.app import create_app  # noqa: E402
from seat_reservation.config import Settings  # noqa: E402
from seat_reservation.service.container import AppContext  # noqa: E402

DB = Path(__file__).resolve().parents[1] / "var" / "e2e" / "e2e.db"


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _launch(chromium):
    for kwargs in ({"channel": "msedge"}, {"channel": "chrome"}, {}):
        try:
            return chromium.launch(**kwargs)
        except Exception:  # noqa: BLE001 - 逐一回退，全部失败才报错
            continue
    raise RuntimeError("未找到可用的 Chromium / Edge / Chrome")


@pytest.fixture(scope="module")
def base_url():
    import uvicorn

    DB.parent.mkdir(parents=True, exist_ok=True)
    db = DB.parent / f"e2e-{uuid.uuid4().hex[:10]}.db"

    settings = Settings.for_tests(db)
    ctx = AppContext(settings)
    ctx.initialize(demo=True)

    app = create_app(settings)
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    url = f"http://127.0.0.1:{port}"
    for _ in range(120):
        try:
            if httpx.get(url + "/api/health", timeout=0.5).status_code == 200:
                break
        except Exception:  # noqa: BLE001 - 服务尚未监听
            time.sleep(0.1)
    else:
        server.should_exit = True
        raise RuntimeError("测试服务未能在 12 秒内就绪")

    yield url

    server.should_exit = True
    thread.join(timeout=5)


def _accounts(base_url: str) -> list[dict]:
    response = httpx.get(base_url + "/api/auth/demo-accounts", timeout=5)
    assert response.status_code == 200
    return response.json()["data"]


def test_student_booking_flow(base_url: str) -> None:
    accounts = _accounts(base_url)
    student = next(item for item in accounts if item["role"] == "student")
    errors: list[str] = []

    with sync_api.sync_playwright() as playwright:
        browser = _launch(playwright.chromium)
        page = browser.new_page(viewport={"width": 1440, "height": 920})
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(base_url, wait_until="networkidle")

        assert page.locator("[data-demo-user]").count() == 4

        page.click(f'[data-demo-user="{student["username"]}"]')
        page.click("#loginBtn")
        page.wait_for_selector("#app:not([hidden])", timeout=10000)

        page.wait_for_selector(".seat", timeout=10000)
        assert page.locator(".seat").count() >= 1
        assert page.locator(".action-bar.is-empty").count() == 1

        seat = page.locator('[data-action="select-seat"][aria-disabled="false"]').first
        seat_id = seat.locator("strong").inner_text()
        seat.click()

        page.wait_for_selector(".action-bar:not(.is-empty)", timeout=5000)
        assert seat_id in page.locator(".action-bar strong").inner_text()

        page.click('[data-action="book"]')
        page.wait_for_selector("#toast.show", timeout=8000)
        assert "预约" in page.locator("#toast").inner_text()

        page.click('[data-view="reservations"]')
        page.wait_for_timeout(800)
        assert page.locator('[data-action="open-reservation"]').count() >= 1

        page.locator('[data-action="open-reservation"]').first.click()
        page.wait_for_selector(".sheet.open", timeout=5000)
        ticket_seat = page.locator(".ticket-seat").inner_text().strip()
        assert ticket_seat, "凭证未显示座位号"

        browser.close()

    assert not errors, f"页面出现 JS 异常：{errors}"


def test_admin_overview_renders(base_url: str) -> None:
    accounts = _accounts(base_url)
    admin = next(item for item in accounts if item["role"] == "room_admin")
    errors: list[str] = []

    with sync_api.sync_playwright() as playwright:
        browser = _launch(playwright.chromium)
        page = browser.new_page(viewport={"width": 1440, "height": 920})
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(base_url, wait_until="networkidle")

        page.click(f'[data-demo-user="{admin["username"]}"]')
        page.click("#loginBtn")
        page.wait_for_selector("#app:not([hidden])", timeout=10000)
        page.wait_for_timeout(1200)

        kpis = page.locator(".kpi-value")
        assert kpis.count() >= 4
        for index in range(kpis.count()):
            text = kpis.nth(index).inner_text().strip()
            assert text and "NaN" not in text and "undefined" not in text, f"KPI 异常：{text!r}"

        page.click('[data-view="floor"]')
        page.wait_for_timeout(800)
        assert page.locator(".seat").count() >= 1

        page.click('[data-view="search"]')
        page.wait_for_timeout(800)
        assert page.locator("table.data").count() == 1

        browser.close()

    assert not errors, f"页面出现 JS 异常：{errors}"


def test_mobile_viewport_switches_to_tab_bar(base_url: str) -> None:
    accounts = _accounts(base_url)
    student = next(item for item in accounts if item["role"] == "student")
    errors: list[str] = []

    with sync_api.sync_playwright() as playwright:
        browser = _launch(playwright.chromium)
        context = browser.new_context(
            viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True
        )
        page = context.new_page()
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(base_url, wait_until="networkidle")

        page.click(f'[data-demo-user="{student["username"]}"]')
        page.click("#loginBtn")
        page.wait_for_selector("#app:not([hidden])", timeout=10000)
        page.wait_for_timeout(900)

        assert page.locator(".seat").count() >= 1

        # 移动端由 .sidebar 变形为底部标签栏（fixed bottom），不是另一套 DOM
        sidebar = page.locator(".sidebar")
        assert sidebar.is_visible()
        box = sidebar.bounding_box()
        assert box is not None and box["y"] > 700, f"侧栏未沉到底部：{box}"
        assert page.locator(".nav-item").count() >= 3

        browser.close()

    assert not errors, f"页面出现 JS 异常：{errors}"
