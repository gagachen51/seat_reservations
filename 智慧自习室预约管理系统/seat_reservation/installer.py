#!/usr/bin/env python3
"""自习室预约 · 安装程序。

用户级安装：装到 %LOCALAPPDATA%\\Programs\\SeatReservation，不需要管理员权限。
创建开始菜单与桌面快捷方式、写入"应用和功能"卸载项。
卸载时保留 %LOCALAPPDATA%\\SeatReservation 下的业务数据。

打包：PyInstaller 把 dist/自习室预约 作为 app 数据一起打进来。
"""

from __future__ import annotations

import ast
import ctypes
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import winreg
from datetime import datetime
from pathlib import Path

APP_NAME = "SeatReservation"
APP_TITLE = "自习室预约"
APP_VERSION = "2.0.0"
PUBLISHER = "校内信息化"
UNINSTALL_KEY = rf"Software\Microsoft\Windows\CurrentVersion\Uninstall\{APP_NAME}"

# 安装进度：同时供界面轮询与 evaluate_js 推送，二者取其一即可工作
STATE: dict = {"type": "idle", "value": 0, "text": "", "target": "", "error": ""}


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def resource_dir() -> Path:
    if is_frozen():
        return Path(sys._MEIPASS) / "app"  # type: ignore[attr-defined]
    return Path(__file__).resolve().parent / "dist" / "自习室预约"


def temp_leftovers() -> list[Path]:
    r"""%TEMP% 下属于本程序的历史解压残留。

    onefile 模式每次运行都会把整个程序解压到 TEMP 下的 _MEIxxxxxx 目录，
    正常退出会自清，但被强杀（任务管理器、杀毒、断电）就会留下来。
    """
    root = Path(tempfile.gettempdir())
    found: list[Path] = []
    for item in root.glob("_MEI*"):
        if not item.is_dir():
            continue
        try:
            names = {p.name for p in item.iterdir()}
        except OSError:
            continue
        if "app" in names or "seat_reservation" in names:
            found.append(item)
    return found


def purge_temp_leftovers(keep_current: bool = True) -> int:
    """删除历史残留，返回删除个数。keep_current 保护正在使用的解压目录。"""
    raw = getattr(sys, "_MEIPASS", None)
    current = Path(raw) if raw else None
    removed = 0
    for item in temp_leftovers():
        if keep_current and current is not None and item == current:
            continue
        try:
            shutil.rmtree(item)
            removed += 1
        except OSError:
            continue
    return removed


def schedule_self_cleanup() -> None:
    """退出后兜底删除自己的解压目录。

    引导程序正常退出会自清，但被杀掉时不会；这里挂一个独立进程，
    等本进程结束后再删，避免把垃圾留在用户 C 盘。
    """
    raw = getattr(sys, "_MEIPASS", None)
    if not raw:
        return
    target = Path(raw)
    if not target.exists():
        return
    script = f'ping -n 3 127.0.0.1 >nul & rd /s /q "{target}"'
    DETACHED_PROCESS = 0x00000008
    CREATE_NO_WINDOW = 0x08000000
    try:
        subprocess.Popen(
            ["cmd", "/c", script],
            creationflags=DETACHED_PROCESS | CREATE_NO_WINDOW,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
        )
    except OSError:
        pass


def install_dir() -> Path:
    base = Path(os.environ.get("LOCALAPPDATA") or Path.home())
    return base / "Programs" / APP_NAME


def data_dir(target: Path | None = None) -> Path:
    """业务数据目录：跟随安装位置，装在 D 盘数据也在 D 盘。"""
    if target is None:
        target = installed_path()
    return Path(target) / "data"


def remove_program_files(target: Path) -> None:
    """删除程序本体，保留 data 与 logs。

    业务数据默认就放在安装目录下（跟随安装位置），整目录删会把预约记录一起清掉，
    所以这里按条目筛，只清程序文件。
    """
    if not target.exists():
        return
    keep = {"data", "logs"}
    for child in target.iterdir():
        if child.name.lower() in keep:
            continue
        try:
            if child.is_dir():
                shutil.rmtree(child, ignore_errors=True)
            else:
                child.unlink()
        except OSError:
            continue


def exe_path(target: Path) -> Path:
    return target / f"{APP_TITLE}.exe"


def installed_path() -> Path:
    """卸载时以注册表登记的路径为准，用户可能装在自定义目录。"""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY) as key:
            recorded = winreg.QueryValueEx(key, "InstallLocation")[0]
            if recorded:
                return Path(recorded)
    except OSError:
        pass
    return install_dir()


def start_menu_dir() -> Path:
    base = Path(os.environ.get("APPDATA") or Path.home())
    return base / "Microsoft" / "Windows" / "Start Menu" / "Programs"


def desktop_dir() -> Path:
    return Path(os.environ.get("USERPROFILE") or Path.home()) / "Desktop"


def make_shortcut(link: Path, target: Path) -> bool:
    """用 WScript.Shell 创建 .lnk，避免引入第三方依赖。"""
    script = (
        "$ws = New-Object -ComObject WScript.Shell; "
        f"$sc = $ws.CreateShortcut('{link}'); "
        f"$sc.TargetPath = '{target}'; "
        f"$sc.WorkingDirectory = '{target.parent}'; "
        f"$sc.IconLocation = '{target},0'; "
        "$sc.Description = '自习室预约与现场管理'; "
        "$sc.Save()"
    )
    try:
        subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            check=True,
            capture_output=True,
            timeout=30,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return True
    except Exception:  # noqa: BLE001 - 创建失败不影响主流程
        return False


def write_uninstall_entry(target: Path) -> None:
    size_kb = sum(f.stat().st_size for f in target.rglob("*") if f.is_file()) // 1024
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY) as key:
        winreg.SetValueEx(key, "DisplayName", 0, winreg.REG_SZ, APP_TITLE)
        winreg.SetValueEx(key, "DisplayVersion", 0, winreg.REG_SZ, APP_VERSION)
        winreg.SetValueEx(key, "Publisher", 0, winreg.REG_SZ, PUBLISHER)
        winreg.SetValueEx(key, "InstallLocation", 0, winreg.REG_SZ, str(target))
        winreg.SetValueEx(key, "DisplayIcon", 0, winreg.REG_SZ, f"{exe_path(target)},0")
        winreg.SetValueEx(key, "UninstallString", 0, winreg.REG_SZ, f'"{sys.executable}" --uninstall')
        winreg.SetValueEx(key, "NoModify", 0, winreg.REG_DWORD, 1)
        winreg.SetValueEx(key, "NoRepair", 0, winreg.REG_DWORD, 1)
        winreg.SetValueEx(key, "EstimatedSize", 0, winreg.REG_DWORD, max(1, size_kb))


def remove_uninstall_entry() -> None:
    try:
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY)
    except FileNotFoundError:
        pass


class InstallerApi:
    """供安装界面调用的操作。"""

    def __init__(self) -> None:
        # 必须下划线私有：pywebview 生成 JS 桥时会递归遍历所有公开属性，
        # Window 反向引用 js_api，公开的 window 属性会构成环形引用导致 UI 线程死锁。
        self._window = None
        self._target: Path = install_dir()

    # ---- 基础 ----
    def _emit(self, payload: str) -> None:
        try:
            parsed = ast.literal_eval(payload)
            if isinstance(parsed, dict):
                STATE.update(parsed)
        except (ValueError, SyntaxError):
            STATE["text"] = str(payload)
        if self._window is not None:
            try:
                self._window.evaluate_js(f"window.onInstallEvent({payload})")
            except Exception:  # noqa: BLE001 - 界面可能已关闭
                pass

    def close_window(self) -> None:
        if self._window is not None:
            self._window.destroy()

    def paths(self) -> dict:
        target = installed_path()
        return {
            "default": str(install_dir()),
            "target": str(target),
            "version": APP_VERSION,
            "exists": exe_path(target).exists(),
            "source_missing": not resource_dir().is_dir(),
        }

    def choose_dir(self) -> str:
        """弹出系统目录选择框；取消时返回空串。"""
        import webview

        if self._window is None:
            return ""
        current = self._target if str(self._target) else install_dir()
        try:
            picked = self._window.create_file_dialog(
                webview.FOLDER_DIALOG, directory=str(current)
            )
        except Exception:  # noqa: BLE001 - 选择框不可用时保持原路径
            return ""
        if picked:
            chosen = Path(picked[0])
            self._target = chosen / APP_NAME if chosen.name != APP_NAME else chosen
            return str(self._target)
        return ""

    def validate(self, target: str) -> dict:
        """安装前检查路径可用性。"""
        path = Path(target) if target else install_dir()
        result = {"ok": True, "target": str(path), "message": ""}
        if path.exists() and not path.is_dir():
            return {**result, "ok": False, "message": "该路径已被同名文件占用"}
        probe = path
        while not probe.exists() and probe.parent != probe:
            probe = probe.parent
        if not os.access(probe, os.W_OK):
            return {
                **result,
                "ok": False,
                "message": "没有写入权限。系统目录（如 Program Files）需要管理员权限，请换一个位置。",
            }
        return result

    # ---- 安装 ----
    def install(self, target: str = "") -> None:
        chosen = Path(target) if target else install_dir()
        check = self.validate(str(chosen))
        if not check["ok"]:
            self._emit(f"{{'type':'error','text':'{check['message']}'}}")
            return
        self._target = chosen
        threading.Thread(target=self._install_worker, daemon=True).start()

    def _install_worker(self) -> None:
        try:
            src = resource_dir()
            target = self._target
            self._emit("{'type':'progress','value':4,'text':'准备安装目录'}")

            if not src.is_dir():
                raise RuntimeError(f"安装源缺失：{src}")
            target.mkdir(parents=True, exist_ok=True)

            # 排除运行期生成的 data/logs：那是本机跑出来的数据库与日志，
            # 绝不能打进安装包带给别人
            skip = {"data", "logs"}
            files = [
                f
                for f in src.rglob("*")
                if f.is_file() and not (set(f.relative_to(src).parts[:1]) & skip)
            ]
            total = max(1, len(files))
            done = 0
            for item in files:
                relative = item.relative_to(src)
                destination = target / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(item, destination)
                done += 1
                if done % 25 == 0 or done == total:
                    percent = 6 + int(done / total * 78)
                    self._emit(f"{{'type':'progress','value':{percent},'text':'正在复制文件 {done}/{total}'}}")

            self._emit("{'type':'progress','value':86,'text':'创建快捷方式'}")
            make_shortcut(start_menu_dir() / f"{APP_TITLE}.lnk", exe_path(target))
            make_shortcut(desktop_dir() / f"{APP_TITLE}.lnk", exe_path(target))

            self._emit("{'type':'progress','value':94,'text':'登记卸载信息'}")
            write_uninstall_entry(target)

            self._emit("{'type':'progress','value':100,'text':'安装完成'}")
            self._emit(f"{{'type':'done','target':'{str(target).replace(chr(92), '/')}'}}")
        except Exception as exc:  # noqa: BLE001 - 界面需要看到失败原因
            message = str(exc).replace("'", " ").replace("{", "(").replace("}", ")")
            self._emit(f"{{'type':'error','text':'{message}'}}")

    def launch(self) -> None:
        target = exe_path(install_dir())
        if target.exists():
            subprocess.Popen([str(target)], close_fds=True)
        if self._window is not None:
            self._window.destroy()

    # ---- 卸载 ----
    def uninstall(self) -> None:
        threading.Thread(target=self._uninstall_worker, daemon=True).start()

    def _uninstall_worker(self) -> None:
        try:
            target = installed_path()
            keep_data = True
            self._emit("{'type':'progress','value':10,'text':'关闭运行中的程序'}")
            subprocess.run(
                ["taskkill", "/F", "/IM", f"{APP_TITLE}.exe"],
                capture_output=True,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            time.sleep(1.0)

            self._emit("{'type':'progress','value':35,'text':'移除快捷方式'}")
            for link in (start_menu_dir() / f"{APP_TITLE}.lnk", desktop_dir() / f"{APP_TITLE}.lnk"):
                try:
                    link.unlink()
                except FileNotFoundError:
                    pass

            self._emit("{'type':'progress','value':55,'text':'删除程序文件'}")
            remove_program_files(target)

            self._emit("{'type':'progress','value':78,'text':'清理临时文件'}")
            purge_temp_leftovers(keep_current=True)

            self._emit("{'type':'progress','value':85,'text':'清理注册信息'}")
            remove_uninstall_entry()

            data = data_dir(target)
            if keep_data and data.exists():
                self._emit(
                    f"{{'type':'progress','value':92,'text':'业务数据已保留：{str(data).replace(chr(92), '/')}'}}"
                )
            self._emit("{'type':'progress','value':100,'text':'卸载完成'}")
            self._emit("{'type':'uninstalled'}")
        except Exception as exc:  # noqa: BLE001
            message = str(exc).replace("'", " ")
            self._emit(f"{{'type':'error','text':'{message}'}}")

    # ---- 无边框窗口的关闭键 ----
    def minimize(self) -> None:
        if self._window is not None:
            try:
                self._window.minimize()
            except Exception:  # noqa: BLE001
                pass

    def start_drag(self) -> None:
        try:
            user32 = ctypes.windll.user32
            hwnd = user32.FindWindowW(None, f"{APP_TITLE} 安装程序")
            if hwnd:
                user32.ReleaseCapture()
                user32.SendMessageW(hwnd, 0x00A1, 0x0002, 0)
        except Exception:  # noqa: BLE001
            pass


PAGE_HTML = """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><style>
* { box-sizing: border-box; margin: 0; }
html, body { height: 100%; }
body {
  font-family: "Microsoft YaHei", "Segoe UI", sans-serif; color: #1c1c1e;
  background: radial-gradient(700px 320px at 6% -12%, rgba(0,122,255,.14), transparent 60%), #f2f2f7;
  display: flex; flex-direction: column; user-select: none; overflow: hidden;
}
.titlebar {
  height: 44px; flex: 0 0 44px; display: flex; align-items: center; gap: 10px;
  padding: 0 10px 0 14px; background: #fff; border-bottom: 1px solid rgba(16,24,40,.07);
}
.logo {
  width: 20px; height: 20px; border-radius: 6px; display: grid; place-items: center; color: #fff;
  background: linear-gradient(180deg, #3d9cff, #0064d8); box-shadow: inset 0 1px 0 rgba(255,255,255,.45);
}
.logo svg { width: 12px; height: 12px; }
.titlebar b { font-size: 12.5px; font-weight: 600; }
.drag { flex: 1; height: 100%; }
.winbtns { display: flex; align-items: center; gap: 2px; height: 100%; }
.winbtns button {
  width: 42px; height: 30px; border: 0; background: transparent; border-radius: 6px; color: #4a4a4f;
  display: flex; align-items: center; justify-content: center; cursor: default; padding: 0;
}
.winbtns button:hover { background: rgba(16,24,40,.06); }
.winbtns button.close:hover { background: #e81123; color: #fff; }
.winbtns svg { width: 12px; height: 12px; display: block; }

main { flex: 1; display: flex; flex-direction: column; padding: 24px 30px 20px; }
h1 { font-size: 20px; letter-spacing: -.01em; margin-bottom: 6px; }
.sub { font-size: 12.5px; color: #6c6c70; line-height: 1.7; }
.card {
  margin-top: 18px; padding: 14px 16px; background: #fff; border-radius: 12px;
  border: 1px solid rgba(16,24,40,.06); box-shadow: 0 1px 2px rgba(16,24,40,.04);
}
.line { display: flex; justify-content: space-between; align-items: baseline; gap: 18px; padding: 6px 0; }
.line + .line { border-top: 1px solid rgba(16, 24, 40, 0.045); }
.line .label { color: #6c6c70; font-size: 12.5px; white-space: nowrap; flex: 0 0 auto; }
.line .value { text-align: right; font-size: 12.5px; line-height: 1.55; word-break: break-all; min-width: 0; }
.line .value .pick { margin-left: 8px; white-space: nowrap; }
.row { display: flex; justify-content: space-between; gap: 12px; font-size: 12.5px; padding: 5px 0; }
.row span:first-child { color: #6c6c70; flex: 0 0 auto; }
.row span:last-child { text-align: right; word-break: break-all; }
.row.col { flex-direction: column; align-items: flex-start; gap: 3px; }
.path-cell { display: flex; align-items: baseline; gap: 8px; max-width: 100%; }
#targetPath { font-size: 11.5px; line-height: 1.55; word-break: break-all; }
.pick { color: #007aff; text-decoration: none; font-size: 12.5px; white-space: nowrap; flex: 0 0 auto; }
.pick:hover { text-decoration: underline; }
.spacer { flex: 1; }
.bar { height: 6px; border-radius: 999px; background: rgba(16,24,40,.08); overflow: hidden; margin-top: 16px; }
.bar > i { display: block; height: 100%; width: 0; border-radius: 999px; background: linear-gradient(90deg,#3d9cff,#007aff); transition: width .28s ease; }
.status { margin-top: 9px; font-size: 12px; color: #6c6c70; min-height: 18px; }
.actions { display: flex; align-items: center; justify-content: space-between; gap: 14px; margin-top: 20px; }
.actions .btns { display: flex; gap: 10px; flex: 0 0 auto; }
button.primary, button.ghost {
  height: 40px; padding: 0 20px; border-radius: 10px; font-size: 13.5px; font-weight: 600;
  font-family: inherit; cursor: pointer; border: 0;
}
button.primary {
  color: #fff; background: linear-gradient(180deg,#3d9cff,#007aff 56%,#0064d8);
  box-shadow: inset 0 1px 0 rgba(255,255,255,.4), 0 8px 16px -8px rgba(0,122,255,.6);
}
button.ghost { background: #fff; color: #1c1c1e; border: 1px solid rgba(16,24,40,.1); }
button:active { transform: scale(.98); }
button[disabled] { opacity: .45; cursor: default; }
.hidden { display: none !important; }
.foot { margin: 0; font-size: 11px; color: #8e8e93; line-height: 1.5; }
.err { margin-top: 12px; font-size: 12px; color: #b3261e; line-height: 1.5; }
</style></head><body>
  <div class="titlebar">
    <span class="logo"><svg viewBox="0 0 64 64" fill="none" stroke="currentColor" stroke-width="5" stroke-linecap="round" stroke-linejoin="round"><path d="M18 29v-5a6 6 0 0 1 6-6h16a6 6 0 0 1 6 6v5"/><path d="M15 34a4 4 0 0 1 4-4h26a4 4 0 0 1 4 4v9H15z"/><path d="M21 43v6M43 43v6"/></svg></span>
    <b id="barTitle">自习室预约 安装程序</b>
    <div class="drag" id="drag"></div>
    <div class="winbtns">
      <button onclick="pywebview.api.minimize()" aria-label="最小化"><svg viewBox="0 0 12 12" fill="none" stroke="currentColor" stroke-width="1.2"><path d="M2 6h8"/></svg></button>
      <button class="close" onclick="pywebview.api.close_window()" aria-label="关闭"><svg viewBox="0 0 12 12" fill="none" stroke="currentColor" stroke-width="1.2"><path d="M3 3l6 6M9 3l-6 6"/></svg></button>
    </div>
  </div>

  <main>
    <div id="stepWelcome">
      <h1>安装自习室预约</h1>
      <p class="sub">本程序安装到当前用户目录，不需要管理员权限。安装后可从开始菜单或桌面启动。</p>
      <div class="card">
        <div class="line"><span class="label">版本</span><span class="value">__VERSION__</span></div>
        <div class="line"><span class="label">安装位置</span><span class="value"><span id="targetPath">__TARGET__</span><a href="#" class="pick" onclick="chooseDir();return false;">更改</a></span></div>
        <div class="line"><span class="label">业务数据</span><span class="value" id="dataPath">—</span></div>
      </div>
      <p class="err hidden" id="welcomeError"></p>
      <div class="spacer"></div>
      <div class="actions">
        <p class="foot">卸载时保留业务数据，不会误删预约记录</p>
        <div class="btns">
          <button class="ghost" onclick="pywebview.api.close_window()">取消</button>
          <button class="primary" id="btnInstall" onclick="startInstall()">开始安装</button>
        </div>
      </div>
    </div>

    <div id="stepProgress" class="hidden">
      <h1 id="progressTitle">正在安装</h1>
      <p class="sub">请稍候，不要关闭窗口。</p>
      <div class="bar"><i id="barFill"></i></div>
      <div class="status" id="statusText">准备中…</div>
      <div class="spacer"></div>
    </div>

    <div id="stepDone" class="hidden">
      <h1 id="doneTitle">安装完成</h1>
      <p class="sub" id="doneText">已创建开始菜单与桌面快捷方式。</p>
      <div class="card">
        <div class="line"><span class="label">安装位置</span><span class="value" id="donePath">—</span></div>
      </div>
      <div class="spacer"></div>
      <div class="actions">
        <p class="foot">可直接双击桌面图标启动</p>
        <div class="btns">
          <button class="ghost" onclick="pywebview.api.close_window()">关闭</button>
          <button class="primary" id="btnLaunch" onclick="pywebview.api.launch()">立即运行</button>
        </div>
      </div>
    </div>

    <div id="stepUninstall" class="hidden">
      <h1>卸载自习室预约</h1>
      <p class="sub">将删除程序文件、快捷方式和临时文件，<b>保留</b>安装目录下 data 文件夹里的业务数据。</p>
      <div class="spacer"></div>
      <div class="actions">
        <p class="foot">卸载后仍可在同目录保留数据</p>
        <div class="btns">
          <button class="ghost" onclick="pywebview.api.close_window()">取消</button>
          <button class="primary" id="btnUninstall" onclick="startUninstall()">确认卸载</button>
        </div>
      </div>
    </div>
  </main>

<script>
  const UNINSTALL_MODE = __MODE__;
  const $ = (id) => document.getElementById(id);
  const show = (id) => {
    ['stepWelcome', 'stepProgress', 'stepDone', 'stepUninstall'].forEach((name) => $(name).classList.add('hidden'));
    $(id).classList.remove('hidden');
  };

  let currentTarget = '';
  let pollTimer = null;

  // 业务数据跟在安装目录下，改目录时同步更新，让用户提前知道数据会存哪
  function syncDataPath() {
    const box = $('dataPath');
    if (!box) return;
    box.textContent = currentTarget ? currentTarget + '\\data' : '—';
  }

  function showError(text) {
    const box = $('welcomeError');
    box.textContent = text;
    box.classList.remove('hidden');
    $('btnInstall').disabled = false;
    $('btnUninstall').disabled = false;
  }

  function renderStatus(s) {
    if (!s || !s.type) return;
    if (s.type === 'progress') {
      $('barFill').style.width = (s.value || 0) + '%';
      $('statusText').textContent = s.text || '';
    } else if (s.type === 'done') {
      show('stepDone');
      $('donePath').textContent = s.target || '';
    } else if (s.type === 'uninstalled') {
      show('stepDone');
      $('doneTitle').textContent = '卸载完成';
      $('doneText').textContent = '程序文件与快捷方式已移除，业务数据仍保留。';
      $('donePath').textContent = (currentTarget || '') + '\\data';
      $('btnLaunch').classList.add('hidden');
    } else if (s.type === 'error') {
      show('stepProgress');
      $('progressTitle').textContent = '操作失败';
      $('statusText').textContent = s.text || '未知错误';
    }
  }

  function startPolling() {
    if (pollTimer) return;
    pollTimer = setInterval(async () => {
      try {
        const resp = await fetch('/status', { cache: 'no-store' });
        renderStatus(await resp.json());
      } catch (err) { /* 忽略瞬时错误 */ }
    }, 350);
  }

  async function startInstall() {
    $('welcomeError').classList.add('hidden');
    $('btnInstall').disabled = true;
    try {
      const resp = await fetch('/install', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ target: currentTarget || '' })
      });
      if (!resp.ok) throw new Error('HTTP ' + resp.status);
      show('stepProgress');
      startPolling();
    } catch (err) {
      showError('无法开始安装：' + err.message);
    }
  }

  async function startUninstall() {
    $('btnUninstall').disabled = true;
    show('stepProgress');
    $('progressTitle').textContent = '正在卸载';
    startPolling();
    try {
      await fetch('/uninstall', { method: 'POST' });
    } catch (err) {
      showError('无法卸载：' + err.message);
    }
  }

  async function chooseDir() {
    const api = window.pywebview && window.pywebview.api;
    if (!api || typeof api.choose_dir !== 'function') {
      showError('当前环境无法打开目录选择框，将安装到默认位置');
      return;
    }
    try {
      const picked = await api.choose_dir();
      if (picked) {
        currentTarget = picked;
        $('targetPath').textContent = picked;
        syncDataPath();
        $('welcomeError').classList.add('hidden');
      }
    } catch (err) {
      showError('无法打开目录选择框：' + err.message);
    }
  }

  window.onInstallEvent = (event) => renderStatus(event);

  const drag = $('drag');
  drag.addEventListener('mousedown', (e) => { if (e.button === 0) pywebview.api.start_drag(); });

  // pywebview 的 api 在页面加载后才注入，需要等待
  function initInfo() {
    if (!window.pywebview || !window.pywebview.api) {
      setTimeout(initInfo, 120);
      return;
    }
    if (UNINSTALL_MODE) {
      $('barTitle').textContent = '自习室预约 卸载程序';
      show('stepUninstall');
    }
    currentTarget = $('targetPath').textContent.trim();
    if (currentTarget === '—') currentTarget = '';
    syncDataPath();
  }
  initInfo();
</script>
</body></html>
"""


def serve_html(html: str, api):
    """用本地 HTTP 提供安装页。

    pywebview 在 ``html=`` 模式下不会注入 js_api，导致界面调用全部无响应；
    走 http:// 加载后桥接才正常工作（与主程序一致）。
    """
    import http.server
    import socketserver

    payload = html.encode("utf-8")

    class Handler(http.server.BaseHTTPRequestHandler):
        def _send(self, body: bytes, content_type: str) -> None:
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, data) -> None:
            import json as _json

            self._send(_json.dumps(data, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

        def do_GET(self) -> None:  # noqa: N802 - 覆写基类
            if self.path.startswith("/status"):
                self._json(STATE)
                return
            self._send(payload, "text/html; charset=utf-8")

        def do_POST(self) -> None:  # noqa: N802 - 覆写基类
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b"{}"
            try:
                import json as _json

                data = _json.loads(raw.decode("utf-8") or "{}")
            except ValueError:
                data = {}
            if self.path.startswith("/install"):
                api.install(str(data.get("target") or ""))
                self._json({"ok": True})
                return
            if self.path.startswith("/uninstall"):
                api.uninstall()
                self._json({"ok": True})
                return
            self._json({"ok": False})

        def log_message(self, *args) -> None:  # noqa: D102 - 静默
            pass

    class Server(socketserver.ThreadingTCPServer):
        allow_reuse_address = True
        daemon_threads = True  # 每个请求独立线程，避免浏览器长连接阻塞轮询

    server = Server(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True, name="installer-http")
    thread.start()
    return f"http://127.0.0.1:{server.server_address[1]}/", server


def run_ui(uninstall_mode: bool) -> None:
    import webview

    api = InstallerApi()
    shown_target = installed_path() if uninstall_mode else install_dir()
    html = (
        PAGE_HTML.replace("__VERSION__", APP_VERSION)
        .replace("__TARGET__", str(shown_target))
        .replace("__MODE__", "true" if uninstall_mode else "false")
    )
    url, server = serve_html(html, api)
    window = webview.create_window(
        f"{APP_TITLE} 安装程序",
        url=url,
        js_api=api,
        width=640,
        height=404,
        frameless=True,
        easy_drag=False,
        resizable=False,
    )
    api._window = window  # noqa: SLF001 - 有意私有，见 InstallerApi.__init__
    try:
        webview.start(gui="edgechromium")
    finally:
        server.shutdown()
        # 退出后清掉本次运行解压出来的临时副本，别把垃圾留在用户 C 盘
        schedule_self_cleanup()


def main() -> int:
    uninstall_mode = "--uninstall" in sys.argv
    try:
        run_ui(uninstall_mode)
        return 0
    except Exception as exc:  # noqa: BLE001
        log = data_dir() / "logs"
        log.mkdir(parents=True, exist_ok=True)
        with (log / "installer.log").open("a", encoding="utf-8") as handle:
            handle.write(f"\n[{datetime.now():%Y-%m-%d %H:%M:%S}] {exc}\n{traceback.format_exc()}")
        try:
            ctypes.windll.user32.MessageBoxW(None, f"安装程序启动失败：{exc}", APP_TITLE, 0x10)
        except Exception:  # noqa: BLE001
            pass
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
