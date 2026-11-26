# -*- mode: python ; coding: utf-8 -*-
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules
from PyInstaller.utils.hooks import collect_all

datas = [('src/seat_reservation/web', 'seat_reservation/web')]
binaries = []
# Conda 环境把 sqlite3/ffi/lzma/bz2/expat 的 DLL 放在 Library/bin，
# PyInstaller 默认只扫描 DLLs，打包后会因缺 sqlite3.dll 启动失败。
for name in ("sqlite3.dll", "ffi.dll", "liblzma.dll", "libbz2.dll", "libexpat.dll"):
    for root in (Path(sys.base_prefix), Path(sys.prefix)):
        candidate = root / "Library" / "bin" / name
        if candidate.is_file():
            binaries.append((str(candidate), "."))
            break
hiddenimports = ['clr', 'webview.platforms.edgechromium', 'uvicorn.logging', 'uvicorn.loops.auto', 'uvicorn.loops.asyncio', 'uvicorn.protocols.http.auto', 'uvicorn.protocols.http.h11_impl', 'uvicorn.lifespan.on']
hiddenimports += collect_submodules('seat_reservation')
tmp_ret = collect_all('webview')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]


a = Analysis(
    ['desktop_launcher.py'],
    pathex=['src'],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='自习室预约',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['assets/app.ico'],
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='自习室预约',
)
