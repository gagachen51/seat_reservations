#!/usr/bin/env python3
"""Windows 桌面客户端。

双击后：启动本地服务 → 打开一个独立窗口（内嵌 WebView2，无浏览器地址栏）。
关闭窗口即停止服务。

分发形态为 ``自习室预约.exe``，目标机器无需安装 Python。
数据与日志写在 ``%LOCALAPPDATA%\\SeatReservation``，卸载程序不会删除。
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes
import os
import secrets
import socket
import sys
import threading
import time
import traceback
import urllib.request
from datetime import datetime
from pathlib import Path

APP_NAME = "SeatReservation"
APP_TITLE = "自习室预约"
WINDOW_SIZE = (1440, 900)



def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def app_dir() -> Path:
    return Path(sys.executable).resolve().parent if is_frozen() else Path(__file__).resolve().parent


def data_root() -> Path:
    """业务数据与日志的根目录。

    优先跟随程序所在目录：装在 D 盘，数据也在 D 盘，不会把用户 C 盘塞满。
    该位置不可写（例如装进 Program Files）时，退回 %LOCALAPPDATA%。
    开发模式下写入项目的 var/，避免污染源码目录。
    """
    if is_frozen():
        candidates = [Path(sys.executable).resolve().parent]
    else:
        candidates = [app_dir() / "var"]
    fallback = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / APP_NAME
    candidates.append(fallback)

    for candidate in candidates:
        try:
            (candidate / "data").mkdir(parents=True, exist_ok=True)
            (candidate / "logs").mkdir(parents=True, exist_ok=True)
            probe = candidate / "data" / ".write-probe"
            probe.write_text("", encoding="utf-8")
            probe.unlink()
            return candidate
        except OSError:
            continue
    return fallback


def prepare_runtime() -> Path:
    """设置路径与环境变量，必须在导入应用之前调用。"""
    if not is_frozen():
        src = app_dir() / "src"
        if str(src) not in sys.path:
            sys.path.insert(0, str(src))
    root = data_root()
    os.environ.setdefault("SEAT_ENV", "development")
    os.environ.setdefault("SEAT_DB_PATH", str(root / "data" / "seat_reservation.db"))
    os.environ.setdefault("SEAT_HOST", "127.0.0.1")
    os.environ.setdefault("SEAT_SCHEDULER_ENABLED", "true")
    os.environ.setdefault("SEAT_LOG_LEVEL", "INFO")
    return root


def ensure_streams(log_path: Path) -> None:
    """--noconsole 打包后 sys.stdout/stderr 为 None，会让 uvicorn 等库在
    sys.stdout.isatty() 上崩溃，这里补一个可写的文件流。"""
    if sys.stdout is not None and sys.stderr is not None:
        return
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        stream = open(log_path, "a", encoding="utf-8", buffering=1)
    except OSError:
        import io

        stream = io.StringIO()
    if sys.stdout is None:
        sys.stdout = stream
    if sys.stderr is None:
        sys.stderr = stream


def pick_port(preferred: int = 8010, span: int = 20) -> int:
    for port in range(preferred, preferred + span):
        with socket.socket() as sock:
            try:
                sock.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    raise RuntimeError(f"{preferred}–{preferred + span - 1} 端口均被占用")


def create_accounts(ctx) -> str | None:
    """首次启动写入完整的演示数据，返回统一的初始密码。

    直接用种子数据而不是只建管理员：种子里已经包含学生、教师、
    自习室管理员、系统管理员四种角色，以及房间、座位和若干预约，
    交作业时打开就能演示各角色各自的视图。

    只在库为空时写入，已有数据不会被动。
    """
    from seat_reservation.storage.seed import DEMO_PASSWORD, seed_if_empty

    created = seed_if_empty(ctx.db, datetime.now())
    return DEMO_PASSWORD if created else None


def write_account_card(root: Path, password: str) -> Path:
    """首次启动在程序目录写一份账号清单。

    一份可见、可转发的文本更实用：交作业时可以直接发给老师，
    也知道每个角色对应哪个账号。

    注意：桌面版登录框**不会**自动填入账号（换身份时反而要先删掉），
    所以这里的说明必须与代码行为一致——写"手动输入"。
    """
    lines = [
        "智慧自习室预约管理系统 · 初始账号",
        "",
        "四种角色各一个账号，初始密码相同（演示模式）。",
        "演示模式不强制改密，登录后可在右上角「修改密码」自行修改；",
        "用命令行新建或重置密码的账号会带「首次登录必须改密」标记。",
        "",
        "----------------------------------------",
        "角色              账号        姓名",
        "----------------------------------------",
        "学生              S2023001    张伟",
        "学生              S2023002    李娜",
        "教师              T7001       刘明",
        "自习室管理员      A9001       王丽",
        "系统管理员        Y0001       陈刚",
        "----------------------------------------",
        "",
        f"初始密码：{password}",
        "",
        "使用方法：",
        "  1. 启动后在登录框手动输入上表中的账号与初始密码（不会自动填入）。",
        "  2. 想以其他身份登录，把上表中的对应账号填进「学号 / 工号」框，",
        "     密码仍用上面的初始密码，系统会按账号进入该角色的界面。",
        "  3. 本文件首次启动时自动生成，可以随时删除；",
        "     删掉不影响系统运行，账号与密码也不会变。",
        "",
    ]
    target = Path(root) / "初始账号与密码.txt"
    # 记事本需要 BOM 才能正确识别 UTF-8 中文
    target.write_text("\r\n".join(lines), encoding="utf-8-sig")
    return target


class Service:
    """把 uvicorn 跑在后台线程，并能被窗口关闭事件停止。"""

    def __init__(self, settings, port: int) -> None:
        from seat_reservation.api.app import create_app
        from seat_reservation.service.container import AppContext

        self.settings = settings
        self.port = port
        self.context = AppContext(settings)
        self.context.initialize(demo=False)
        self.app = create_app(settings)
        self.server = None
        self.thread: threading.Thread | None = None

    def start(self) -> None:
        import uvicorn

        config = uvicorn.Config(
            self.app,
            host="127.0.0.1",
            port=self.port,
            log_level="warning",
            access_log=False,
            log_config=None,  # 由应用自身配置日志，避免 uvicorn 在无控制台环境下崩溃
        )
        self.server = uvicorn.Server(config)
        self.thread = threading.Thread(target=self.server.run, daemon=True, name="uvicorn")
        self.thread.start()

    def wait_ready(self, timeout: float = 25.0) -> bool:
        deadline = time.time() + timeout
        url = f"http://127.0.0.1:{self.port}/api/health"
        while time.time() < deadline:
            try:
                with urllib.request.urlopen(url, timeout=0.6) as response:
                    if response.status == 200:
                        return True
            except Exception:  # noqa: BLE001 - 服务尚未监听
                time.sleep(0.15)
        return False

    def stop(self) -> None:
        if self.server is not None:
            self.server.should_exit = True
        if self.thread is not None:
            self.thread.join(timeout=5)


class Bridge:
    """窗口桥：首次启动引导 + 自绘标题栏的窗口控制。"""

    # Win32 常量
    WM_NCLBUTTONDOWN = 0x00A1
    HTCAPTION = 0x0002
    WM_CLOSE = 0x0010
    SW_MINIMIZE = 6
    SW_MAXIMIZE = 3
    SW_RESTORE = 9
    GWL_STYLE = -16
    WS_MAXIMIZE = 0x01000000

    def __init__(self, url: str) -> None:
        # 必须用下划线私有：pywebview 生成 JS 桥时会递归遍历所有公开属性
        # （见 webview/util.py get_functions），而 Window 对象反向引用 js_api，
        # 公开的 window 属性会构成环形引用，导致 UI 线程无限递归卡死。
        self._url = url
        self._window = None
        self._hwnd = None
        self._drag_x = 0
        self._drag_y = 0

    # ---- 首次启动引导 ----
    def enter(self) -> None:
        if self._window is not None:
            self._window.load_url(self._url)
            try:
                self._window.resize(*WINDOW_SIZE)
            except Exception:  # noqa: BLE001 - 部分后端不支持运行期改尺寸
                pass

    # ---- 窗口控制 ----
    def _handle(self):
        if self._hwnd:
            return self._hwnd
        try:
            self._hwnd = ctypes.windll.user32.FindWindowW(None, APP_TITLE)
        except Exception:  # noqa: BLE001 - 非 Windows 或窗口尚未就绪
            self._hwnd = None
        return self._hwnd

    def begin_drag(self) -> None:
        """记录窗口当前坐标，作为拖动的起点。

        原先用 SendMessage(WM_NCLBUTTONDOWN) 让系统接管拖动，但它要求调用方
        与窗口同属 UI 线程；桥接线程调用会静默失效，表现就是标题栏拖不动。
        改为自己算偏移再移动窗口，不依赖线程归属。
        """
        hwnd = self._handle()
        if not hwnd:
            return
        rect = ctypes.wintypes.RECT()
        ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(rect))
        self._drag_x, self._drag_y = rect.left, rect.top

    def drag_move(self, dx: int = 0, dy: int = 0) -> None:
        """按鼠标位移平移窗口。"""
        if self._window is None:
            return
        try:
            self._window.move(int(self._drag_x + dx), int(self._drag_y + dy))
        except Exception:  # noqa: BLE001 - 移动失败不应打断交互
            pass

    def minimize(self) -> None:
        hwnd = self._handle()
        if hwnd:
            ctypes.windll.user32.ShowWindow(hwnd, self.SW_MINIMIZE)

    def toggle_max(self) -> None:
        hwnd = self._handle()
        if not hwnd:
            return
        user32 = ctypes.windll.user32
        style = user32.GetWindowLongW(hwnd, self.GWL_STYLE)
        user32.ShowWindow(hwnd, self.SW_RESTORE if style & self.WS_MAXIMIZE else self.SW_MAXIMIZE)

    def close_window(self) -> None:
        hwnd = self._handle()
        if hwnd:
            ctypes.windll.user32.PostMessageW(hwnd, self.WM_CLOSE, 0, 0)


def start_stack_watchdog(log_path: Path, interval: float = 3.0) -> None:
    """死锁诊断：设置 ``SEAT_STACK_DUMP=1`` 时定期导出所有线程栈。

    窗口卡死时 CPU 为 0，说明线程在相互等待而不是在计算，
    只有调用栈能指出卡在哪一行。用 faulthandler 而非 Python 线程，
    因为它从 C 层 dump，不受 GIL 与线程调度影响。
    """
    if os.environ.get("SEAT_STACK_DUMP") != "1":
        return
    import faulthandler

    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        stream = open(log_path, "a", encoding="utf-8", buffering=1)
        faulthandler.dump_traceback_later(interval, repeat=True, file=stream, exit=False)
    except Exception:  # noqa: BLE001 - 诊断不应影响主流程
        pass


def run_window(url: str, admin_password: str | None, service: Service) -> None:
    """打开主窗口。

    不在登录框预填账号。预填了反而要先删掉才能换身份；
    账号与密码统一写在程序目录的「初始账号与密码.txt」里，
    用户看一眼就能知道自己想用哪个。
    """
    import webview

    bridge = Bridge(url)
    window = webview.create_window(
        APP_TITLE,
        url=f"{url}?shell=1",
        js_api=bridge,
        width=WINDOW_SIZE[0],
        height=WINDOW_SIZE[1],
        frameless=True,
        easy_drag=False,
        min_size=(1024, 680),
    )
    bridge._window = window  # noqa: SLF001 - 有意为私有，见 Bridge.__init__

    try:
        # edgechromium 后端直接驱动 WebView2，不像 winforms 后端那样在
        # frameless 模式下出现 AccessibilityObject 递归，是无边框可用路径。
        webview.start(gui="edgechromium")
    finally:
        service.stop()


def fatal(message: str, log_path: Path) -> None:
    try:
        ctypes.windll.user32.MessageBoxW(
            None, f"{message}\n\n日志：{log_path}", APP_TITLE, 0x10
        )
    except Exception:  # noqa: BLE001 - 无桌面会话时仅写日志
        pass


def missing_dependencies() -> list[str]:
    """检查运行时依赖是否齐全。

    直接双击 .py 时用的是系统 Python，往往没装 fastapi，
    而缺依赖的报错要到很深的地方才抛出，用户只会看到一串英文。
    这里提前检查，给出可操作的提示。
    """
    missing = []
    for module in ("fastapi", "uvicorn", "pydantic", "webview"):
        try:
            __import__(module)
        except ModuleNotFoundError:
            missing.append(module)
    return missing


def main() -> int:
    missing = missing_dependencies()
    if missing:
        # 同目录有 启动.bat 就交给它（它会装依赖再启动），用户不必记该点哪个文件
        launcher = Path(__file__).with_name("启动.bat")
        if launcher.exists() and not is_frozen():
            try:
                subprocess.Popen(
                    ["cmd", "/c", str(launcher)],
                    cwd=str(launcher.parent),
                    creationflags=0x00000008 | 0x08000000,  # DETACHED_PROCESS | CREATE_NO_WINDOW
                )
                return 0
            except OSError:
                pass

        message = (
            "缺少运行依赖：" + "、".join(missing) + "\n\n"
            "任选一种方式：\n"
            "  1. 双击同目录下的「启动.bat」，它会自动装好依赖再启动\n"
            "  2. 在本目录执行：\n"
            "     python -m pip install -r requirements.txt\n"
            "     python desktop_launcher.py\n\n"
            "如果只装了网页版依赖，也可以用浏览器访问：\n"
            "     python run.py --db var/web.db init-db --demo\n"
            "     python run.py --db var/web.db serve --port 8010 --demo"
        )
        try:
            ctypes.windll.user32.MessageBoxW(None, message, APP_TITLE, 0x10)
        except Exception:  # noqa: BLE001 - 无桌面会话时只能静默
            pass
        return 2

    root = prepare_runtime()
    log_path = root / "logs" / "launcher.log"
    ensure_streams(log_path)
    start_stack_watchdog(log_path)
    try:
        try:
            from seat_reservation.config import Settings
        except ModuleNotFoundError as exc:
            raise RuntimeError(
                f"缺少依赖：{exc.name}\n\n"
                f"请先安装依赖再启动：\n"
                f"    python -m pip install -r requirements.txt"
            ) from exc

        port = pick_port()
        settings = Settings.from_env().with_db(root / "data" / "seat_reservation.db")
        service = Service(settings, port)
        create_accounts(service.context)
        service.start()
        if not service.wait_ready():
            raise RuntimeError("服务启动超时，请查看日志")
        run_window(f"http://127.0.0.1:{port}", None, service)
        return 0
    except Exception as exc:  # noqa: BLE001 - 顶层兜底
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(f"\n[{datetime.now():%Y-%m-%d %H:%M:%S}] {exc}\n{traceback.format_exc()}")
        fatal(f"启动失败：{exc}", log_path)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
