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
from ..storage.seed import seed_demo, seed_if_empty
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
    def initialize(self, *, demo: bool = False) -> None:
        applied = self.db.migrate()
        self.db.ensure_writable()
        if applied:
            logger.info("database migrated: %s", applied)
        if demo:
            created = seed_if_empty(self.db, datetime.now())
            if created:
                logger.info("demo data seeded")

    @contextmanager
    def engine(self, *, write: bool = True) -> Iterator[DomainEngine]:
        with self.db.transaction(write=write) as conn:
            repo = SqliteRepository(conn)
            yield DomainEngine(repo, repo.load_params())

    def params(self) -> ParamSet:
        with self.db.read() as conn:
            return SqliteRepository(conn).load_params()

    def seed_demo(self, now: datetime | None = None) -> dict[str, int]:
        return seed_demo(self.db, now or datetime.now())

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
