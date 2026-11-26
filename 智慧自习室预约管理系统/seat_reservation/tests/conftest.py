from __future__ import annotations

import atexit
import os
import shutil
import sys
import tempfile
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from seat_reservation.config import Settings  # noqa: E402
from seat_reservation.service.container import AppContext  # noqa: E402
from tests.helpers import NOW, Env, seed_fixture  # noqa: E402

# 测试库默认放在系统临时目录：不污染项目 var/，也避开云同步目录对 SQLite
# WAL 文件锁的干扰。整个目录在 pytest 进程退出时删除。
# 需要保留现场时设置 SEAT_TEST_DB_DIR，指向一个自定义目录即可（此时不清理）。
_OVERRIDE = os.environ.get("SEAT_TEST_DB_DIR", "").strip()
if _OVERRIDE:
    _DB_ROOT = Path(_OVERRIDE).expanduser().resolve()
    _DB_ROOT.mkdir(parents=True, exist_ok=True)
    _KEEP_DB = True
else:
    _DB_ROOT = Path(tempfile.mkdtemp(prefix="seat-reservation-tests-"))
    _KEEP_DB = False


def _cleanup_db_root() -> None:
    if not _KEEP_DB:
        shutil.rmtree(_DB_ROOT, ignore_errors=True)


atexit.register(_cleanup_db_root)


def _isolated_db(node_name: str) -> Path:
    safe = "".join(ch if (ch.isalnum() or ch in "-_.") else "_" for ch in node_name)[:70]
    return _DB_ROOT / f"{safe}-{uuid.uuid4().hex[:10]}.db"


@pytest.fixture
def ctx(request) -> AppContext:
    settings = Settings.for_tests(_isolated_db(request.node.name))
    context = AppContext(settings)
    context.initialize(demo=False)
    seed_fixture(context, NOW)
    return context


@pytest.fixture
def env(ctx: AppContext) -> Env:
    return Env(ctx)
