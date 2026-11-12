from __future__ import annotations

import sys
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from seat_reservation.config import Settings  # noqa: E402
from seat_reservation.service.container import AppContext  # noqa: E402
from tests.helpers import NOW, Env, seed_fixture  # noqa: E402

# 测试库统一落在项目 var/test-db 下，每次用唯一文件名：
# 不依赖 pytest 的 tmp_path，也不删除任何已有文件（避免误删与权限拦截）。
_DB_ROOT = Path(__file__).resolve().parents[1] / "var" / "test-db"


def _isolated_db(node_name: str) -> Path:
    _DB_ROOT.mkdir(parents=True, exist_ok=True)
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
