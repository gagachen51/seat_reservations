#!/usr/bin/env python3
"""免安装启动器：``python run.py serve``。

把 ``src`` 加入 ``sys.path``，因此无需 ``pip install -e .`` 也能运行；
生产环境仍建议安装为包后使用 ``seat-reservation`` 命令。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from seat_reservation.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
