"""常驻定时任务（FR-44 / NFR-02）。

单进程内启动一个守护线程，每 ``interval`` 秒执行一次扫描：
未签到违约、暂离超时、到点未签退、候补超时顺延、开始前提醒。
扫描本身幂等，多实例同时运行也不会重复扣分（状态先变更再提交）。
"""

from __future__ import annotations

import threading
from datetime import datetime
from typing import TYPE_CHECKING, Any

from ..logging_setup import get_logger, job_var

if TYPE_CHECKING:  # pragma: no cover
    from .container import AppContext

logger = get_logger(__name__)


class Scheduler:
    def __init__(self, context: "AppContext", interval_seconds: int = 60) -> None:
        self.context = context
        self.interval = max(10, interval_seconds)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._last_run: datetime | None = None
        self._last_summary: dict[str, int] = {}
        self._last_error: str | None = None
        self._runs = 0

    # ------------------------------------------------------------------
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="seat-scheduler", daemon=True)
        self._thread.start()
        logger.info("scheduler started, interval=%ss", self.interval)

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=timeout)
        logger.info("scheduler stopped")

    def _loop(self) -> None:
        # 启动稍等，避免与迁移/种子抢锁
        while not self._stop.wait(3):
            try:
                self.run_once()
            except Exception as exc:  # noqa: BLE001 - 后台任务必须吞掉异常继续
                with self._lock:
                    self._last_error = f"{type(exc).__name__}: {exc}"
                logger.exception("scheduler tick failed")
            if self._stop.wait(self.interval):
                break

    def run_once(self, now: datetime | None = None) -> dict[str, int]:
        now = now or datetime.now()
        job_var.set("sweep")
        try:
            with self.context.engine(write=True) as engine:
                summary = engine.run_timeout_sweep(now)
        finally:
            job_var.set("-")
        with self._lock:
            self._last_run = now
            self._last_summary = summary
            self._last_error = None
            self._runs += 1
        if any(summary.values()):
            logger.info("sweep summary=%s", summary)
        return summary

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {
                "running": bool(self._thread and self._thread.is_alive()),
                "interval_seconds": self.interval,
                "runs": self._runs,
                "last_run_at": self._last_run.isoformat(sep=" ") if self._last_run else None,
                "last_summary": dict(self._last_summary),
                "last_error": self._last_error,
            }
