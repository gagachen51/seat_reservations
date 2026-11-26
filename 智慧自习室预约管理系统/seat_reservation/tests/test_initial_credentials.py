"""首次初始化管理员凭据文件的生成、展示与清理回归。

凭据文件只写入运行期数据目录（与数据库同级），已被 ``.gitignore`` 排除；
首次改密成功后自动删除，避免明文密码长期残留。
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from seat_reservation.config import Settings
from seat_reservation.domain.models import Role, User
from seat_reservation.security import hash_password
from seat_reservation.service.container import AppContext
from seat_reservation.storage.repository import SqliteRepository
from seat_reservation.storage.seed import INITIAL_CREDENTIALS_FILENAME


def _ctx(tmp_path: Path) -> AppContext:
    settings = Settings(
        env="test",
        db_path=tmp_path / "data" / "seat_reservation.db",
        scheduler_enabled=False,
        demo_mode=False,
    )
    return AppContext(settings)


def _credentials(tmp_path: Path) -> Path:
    return tmp_path / "data" / INITIAL_CREDENTIALS_FILENAME


def test_bootstrap_admin_writes_initial_credentials_file(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    password = ctx.bootstrap_admin()
    assert password

    target = _credentials(tmp_path)
    assert target.is_file()
    text = target.read_text(encoding="utf-8")
    assert "账号 ID：Y0001" in text
    assert f"初始密码：{password}" in text
    assert "生成时间：" in text
    assert "首次登录后系统将强制要求修改密码" in text
    assert "删除此文件" in text
    assert list(target.parent.glob("initial_admin_credentials*.txt")) == [target]


def test_change_password_removes_credentials_and_new_password_logs_in(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    password = ctx.bootstrap_admin()
    target = _credentials(tmp_path)
    assert target.is_file()

    ctx.auth.change_password("Y0001", password, "Changed-Pass-2026!")

    assert not target.exists()
    _, _, user = ctx.auth.login("Y0001", "Changed-Pass-2026!")
    assert user.id == "Y0001"
    assert user.must_change_password is False


def test_other_account_change_password_keeps_admin_credentials(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    ctx.bootstrap_admin("Admin-Pass-2026!")
    now = datetime.now()
    with ctx.db.transaction() as conn:
        SqliteRepository(conn).save_user(
            User(
                id="S9001",
                name="学生",
                role=Role.STUDENT.value,
                password_hash=hash_password("Student-Pass-2026!"),
                must_change_password=True,
                created_at=now,
                updated_at=now,
            )
        )

    ctx.auth.change_password("S9001", "Student-Pass-2026!", "Student-Pass-2027!")

    # 其他账号改密不能误删尚未完成首次改密的初始管理员凭据。
    assert _credentials(tmp_path).is_file()


def test_second_bootstrap_is_idempotent_and_keeps_credentials(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    ctx.bootstrap_admin("Admin-Pass-2026!")
    target = _credentials(tmp_path)
    original = target.read_text(encoding="utf-8")

    assert ctx.bootstrap_admin() is None
    assert target.read_text(encoding="utf-8") == original
