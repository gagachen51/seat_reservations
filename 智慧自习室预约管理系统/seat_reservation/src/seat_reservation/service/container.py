"""应用装配：数据库、引擎工厂、认证与调度器。"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
from typing import Iterator

from ..config import Settings
from ..domain.engine import DomainEngine
from ..domain.params import ParamSet
from ..logging_setup import get_logger
from ..storage.database import Database
from ..storage.repository import SqliteRepository
from ..storage.seed import (
    DEMO_USER_IDS,
    bootstrap_admin,
    seed_demo,
    seed_if_empty,
)
from .auth import AuthService
from .scheduler import Scheduler

logger = get_logger(__name__)


class AppContext:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.db = Database(settings.db_path)
        self.auth = AuthService(self.db, settings)
        self.started_at = datetime.now()
        self.scheduler: Scheduler | None = None
        if settings.scheduler_enabled:
            self.scheduler = Scheduler(self, settings.scheduler_interval_seconds)

    # ------------------------------------------------------------------
    def initialize(self, *, demo: bool = False, demo_password: str | None = None) -> None:
        applied = self.db.migrate()
        self.db.ensure_writable()
        if applied:
            logger.info("database migrated: %s", applied)
        if demo:
            created = seed_if_empty(self.db, datetime.now(), password=demo_password)
            if created:
                logger.info("demo data seeded")

    def bootstrap_admin(self, password: str | None = None) -> str | None:
        """正式库首次初始化管理员；返回一次性初始密码（仅创建时）。"""
        return bootstrap_admin(self.db, datetime.now(), password=password)

    @contextmanager
    def engine(self, *, write: bool = True) -> Iterator[DomainEngine]:
        with self.db.transaction(write=write) as conn:
            repo = SqliteRepository(conn)
            yield DomainEngine(repo, repo.load_params())

    def params(self) -> ParamSet:
        with self.db.read() as conn:
            return SqliteRepository(conn).load_params()

    def seed_demo(self, now: datetime | None = None, *, password: str | None = None) -> dict[str, int]:
        return seed_demo(self.db, now or datetime.now(), password=password)

    def start_background_jobs(self) -> None:
        if self.scheduler is not None:
            self.scheduler.start()

    def stop_background_jobs(self) -> None:
        if self.scheduler is not None:
            self.scheduler.stop()

    def health(self) -> dict[str, object]:
        return {
            "status": "ok" if self.db.ping() else "degraded",
            "version": __import__("seat_reservation").__version__,
            "env": self.settings.env,
            "demo": self.settings.demo_mode,
            "db": {
                "schema_version": self.db.schema_version(),
                "size_bytes": self.db.size_bytes(),
            },
            "uptime_seconds": int((datetime.now() - self.started_at).total_seconds()),
            "scheduler": self.scheduler.status() if self.scheduler else {"running": False},
        }

    def demo_data_loaded(self) -> bool:
        """当前库是否已写入标准演示数据。

        不依赖 ``SEAT_DEMO_MODE``：桌面版默认以正式模式启动，用户可在「系统」
        页面一键初始化演示数据，此后 ``settings.demo_mode`` 仍为 False。
        """
        placeholders = ",".join("?" for _ in DEMO_USER_IDS)
        with self.db.read() as conn:
            row = conn.execute(
                f"SELECT COUNT(*) AS c FROM users WHERE id IN ({placeholders})",
                DEMO_USER_IDS,
            ).fetchone()
        return int(row["c"]) == len(DEMO_USER_IDS)

    def system_status(self) -> dict[str, object]:
        """系统管理页所需的完整运行状态。

        ``/api/health`` 按 L-02 只暴露可用性；管理员需要版本、Schema、库大小与
        调度器详情，因此单独提供带权限校验的聚合视图。
        """
        health = self.health()
        scheduler = (
            {**self.scheduler.status(), "enabled": True}
            if self.scheduler is not None
            else {
                "enabled": False,
                "running": False,
                "interval_seconds": 0,
                "runs": 0,
                "last_run_at": None,
                "last_summary": {},
                "last_error": None,
            }
        )
        db = health["db"]
        assert isinstance(db, dict)
        with self.db.read() as conn:
            user_count = int(conn.execute("SELECT COUNT(*) AS c FROM users").fetchone()["c"])
        return {
            "status": health["status"],
            "version": health["version"],
            "env": health["env"],
            "demo_mode": health["demo"],
            "demo_data": self.demo_data_loaded(),
            "db": {**db, "users": user_count},
            "uptime_seconds": health["uptime_seconds"],
            "scheduler": scheduler,
        }
