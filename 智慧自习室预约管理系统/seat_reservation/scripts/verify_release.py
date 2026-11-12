#!/usr/bin/env python3
"""交付前自检：把「能不能用」变成可重复执行的检查，而不是靠人眼看。

检查顺序由浅入深，前一项失败就不再往下跑浪费时间：

  1. 源码完整性    —— 关键文件与包的 __init__.py 是否齐全
  2. 单元测试      —— pytest 全绿
  3. 服务与接口    —— 起服务、健康检查、登录、建预约
  4. 源码版窗口    —— 真的能弹出窗口
  5. exe 版窗口    —— 打包产物真的能弹出窗口
  6. 账号清单      —— 初始账号文件存在且密码与代码一致

用法：
    python scripts/verify_release.py            # 全部检查
    python scripts/verify_release.py --skip-exe # 跳过 exe 检查（未打包时）
"""

from __future__ import annotations

import argparse
import ctypes
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"

GREEN = ""
RED = ""
RESET = ""
if os.name == "nt":
    try:
        ctypes.windll.kernel32.SetConsoleMode(
            ctypes.windll.kernel32.GetStdHandle(-11), 7
        )
        GREEN, RED, RESET = "\033[32m", "\033[31m", "\033[0m"
    except Exception:  # noqa: BLE001
        pass

results: list[tuple[str, bool, str]] = []


def record(name: str, ok: bool, detail: str = "") -> bool:
    results.append((name, ok, detail))
    mark = f"{GREEN}PASS{RESET}" if ok else f"{RED}FAIL{RESET}"
    print(f"  [{mark}] {name}" + (f"  {detail}" if detail else ""))
    return ok


def check_files() -> bool:
    """关键文件与包结构。缺 __init__.py 会让 Python 当成命名空间包，
    表现为 import 时报 unknown location。"""
    print("\n1. 源码完整性")
    required = [
        "run.py",
        "desktop_launcher.py",
        "installer.py",
        "pyproject.toml",
        "requirements.txt",
        "src/seat_reservation/__init__.py",
        "src/seat_reservation/api/app.py",
        "src/seat_reservation/web/index.html",
        "src/seat_reservation/web/app.js",
    ]
    missing = [f for f in required if not (ROOT / f).exists()]
    if not record("关键文件齐全", not missing, f"缺 {missing}" if missing else ""):
        return False

    init_count = len(list((SRC / "seat_reservation").rglob("__init__.py")))
    if not record("包的 __init__.py 完整", init_count >= 5, f"{init_count} 个"):
        return False

    py_count = len([p for p in (SRC / "seat_reservation").rglob("*.py")])
    return record("源码文件数正常", py_count >= 30, f"{py_count} 个 py")


def check_tests() -> bool:
    print("\n2. 单元测试")
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    tail = (proc.stdout or "").strip().splitlines()
    summary = tail[-1] if tail else "无输出"
    ok = proc.returncode == 0 and "passed" in summary
    return record("pytest", ok, summary[:70])


def check_api() -> bool:
    """起一个临时服务，走一遍登录与预约，确认接口层没坏。"""
    print("\n3. 服务与接口")
    import json
    import sqlite3
    import urllib.error
    import urllib.request

    port = 8177
    proc = subprocess.Popen(
        [sys.executable, "run.py", "--db", "var/verify.db", "init-db", "--demo"],
        cwd=ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    proc.wait(timeout=120)

    server = subprocess.Popen(
        [sys.executable, "run.py", "--db", "var/verify.db", "serve", "--port", str(port)],
        cwd=ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    base = f"http://127.0.0.1:{port}"
    try:
        ready = False
        for _ in range(40):
            try:
                with urllib.request.urlopen(base + "/api/health", timeout=1) as resp:
                    ready = resp.status == 200
                    break
            except Exception:  # noqa: BLE001
                time.sleep(0.5)
        if not record("服务启动", ready):
            return False

        sys.path.insert(0, str(SRC))
        from seat_reservation.storage.seed import DEMO_PASSWORD

        body = json.dumps({"username": "Y0001", "password": DEMO_PASSWORD}).encode()
        req = urllib.request.Request(
            base + "/api/auth/login", data=body, method="POST"
        )
        req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                login = json.loads(resp.read().decode())
            ok = login.get("ok") is True
        except urllib.error.HTTPError as exc:
            ok = False
            login = {"message": exc.read().decode()[:60]}
        record("账号 Y0001 可登录", ok, "" if ok else str(login)[:60])

        # 直接查库确认四个角色都在
        conn = sqlite3.connect(ROOT / "var" / "verify.db")
        roles = {r[0] for r in conn.execute("SELECT DISTINCT role FROM users")}
        conn.close()
        want = {"student", "teacher", "room_admin", "sys_admin"}
        return record("四个角色齐全", want <= roles, f"{sorted(roles)}")
    finally:
        server.terminate()
        try:
            server.wait(timeout=8)
        except subprocess.TimeoutExpired:
            server.kill()


def _wait_window(title: str, seconds: int = 25) -> int:
    """等待窗口出现，返回句柄；0 表示没等到。"""
    user32 = ctypes.windll.user32
    deadline = time.time() + seconds
    while time.time() < deadline:
        handle = user32.FindWindowW(None, title)
        if handle:
            time.sleep(2)  # 给页面渲染留时间
            return handle
        time.sleep(0.5)
    return 0


def _kill_window(handle: int) -> None:
    if handle:
        ctypes.windll.user32.PostMessageW(handle, 0x0010, 0, 0)  # WM_CLOSE
        time.sleep(1.5)


def check_source_window() -> bool:
    print("\n4. 源码版窗口")
    env = dict(os.environ)
    env["SEAT_DB_PATH"] = str(ROOT / "var" / "window.db")
    proc = subprocess.Popen(
        [sys.executable, "desktop_launcher.py"],
        cwd=ROOT,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    handle = _wait_window("自习室预约")
    ok = bool(handle)
    _kill_window(handle)
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
    return record("窗口能弹出", ok, "" if ok else "25 秒内未出现窗口")


def check_exe_window() -> bool:
    print("\n5. exe 版窗口")
    exe = ROOT / "dist" / "自习室预约" / "自习室预约.exe"
    if not exe.exists():
        return record("exe 存在", False, str(exe))

    proc = subprocess.Popen([str(exe)], cwd=str(exe.parent))
    handle = _wait_window("自习室预约", seconds=30)
    ok = bool(handle)
    _kill_window(handle)
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
    return record("打包版能弹出窗口", ok, "" if ok else "30 秒内未出现窗口")


def check_account_card() -> bool:
    print("\n6. 账号清单")
    sys.path.insert(0, str(SRC))
    from seat_reservation.storage.seed import DEMO_PASSWORD

    card = ROOT / "初始账号与密码.txt"
    if not record("清单文件存在", card.exists(), str(card)):
        return False
    text = card.read_text(encoding="utf-8-sig")
    has_pwd = DEMO_PASSWORD in text
    record("清单密码与代码一致", has_pwd, DEMO_PASSWORD)
    has_users = all(u in text for u in ("S2023001", "T7001", "A9001", "Y0001"))
    return record("四个角色账号都在清单里", has_users)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-exe", action="store_true", help="跳过 exe 检查")
    args = parser.parse_args()

    print("=" * 56)
    print("交付自检")
    print("=" * 56)

    ok = True
    ok &= check_files()
    if ok:
        ok &= check_tests()
    if ok:
        ok &= check_api()
    if ok:
        ok &= check_source_window()
    if ok and not args.skip_exe:
        ok &= check_exe_window()
    ok &= check_account_card()

    total = len(results)
    passed = sum(1 for _, good, _ in results if good)
    print("\n" + "=" * 56)
    if ok:
        print(f"{GREEN}全部通过{RESET}  {passed}/{total}")
    else:
        print(f"{RED}存在失败项{RESET}  {passed}/{total}")
        for name, good, detail in results:
            if not good:
                print(f"  - {name} {detail}")
    print("=" * 56)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
