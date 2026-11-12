"""SQLite 连接、事务与迁移执行器。

关键点
------
* WAL + ``busy_timeout``：允许读写并发，写事务排队而不是立刻报错。
* 写事务统一 ``BEGIN IMMEDIATE``：预约创建先拿写锁再检查冲突，配合
  ``reservation_slots`` 的主键约束满足 NFR-03 的并发唯一性。
* 迁移按版本号顺序执行，记录在 ``schema_migrations``，可重复调用。
"""

from __future__ import annotations

import logging
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Sequence

from .migrations import MIGRATIONS, Migration

logger = logging.getLogger(__name__)


def _configure(conn: sqlite3.Connection) -> None:
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.execute("PRAGMA temp_store = MEMORY")


class Database:
    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(
            self.path,
            timeout=10.0,
            isolation_level=None,
            check_same_thread=False,
        )
        _configure(conn)
        return conn

    @contextmanager
    def transaction(self, *, write: bool = True) -> Iterator[sqlite3.Connection]:
        conn = self.connect()
        try:
            conn.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            yield conn
            conn.execute("COMMIT")
        except Exception:
            try:
                conn.execute("ROLLBACK")
            except sqlite3.Error:  # pragma: no cover - 连接已损坏
                pass
            raise
        finally:
            conn.close()

    @contextmanager
    def read(self) -> Iterator[sqlite3.Connection]:
        with self.transaction(write=False) as conn:
            yield conn

    def migrate(self) -> list[int]:
        applied: list[int] = []
        with self.transaction() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version INTEGER PRIMARY KEY,
                    name TEXT NOT NULL,
                    applied_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
                )
                """
            )
            current = {
                int(row["version"])
                for row in conn.execute("SELECT version FROM schema_migrations").fetchall()
            }
            for migration in MIGRATIONS:
                if migration.version in current:
                    continue
                for statement in migration.statements:
                    conn.execute(statement)
                conn.execute(
                    "INSERT INTO schema_migrations(version, name) VALUES (?, ?)",
                    (migration.version, migration.name),
                )
                applied.append(migration.version)
                logger.info("applied migration %s %s", migration.version, migration.name)
        return applied

    def schema_version(self) -> int:
        with self.read() as conn:
            try:
                row = conn.execute("SELECT MAX(version) AS v FROM schema_migrations").fetchone()
            except sqlite3.OperationalError:
                return 0
        return int(row["v"] or 0)

    def ping(self) -> bool:
        try:
            with self.read() as conn:
                conn.execute("SELECT 1").fetchone()
            return True
        except sqlite3.Error:  # pragma: no cover - 仅故障时
            return False

    def ensure_writable(self) -> None:
        """启动自检：数据库必须可写。只读介质会导致所有写操作失败，应尽早暴露。"""
        try:
            with self.transaction() as conn:
                conn.execute("UPDATE system_params SET value = value WHERE 0")
        except sqlite3.OperationalError as exc:
            raise RuntimeError(
                f"数据库不可写：{self.path}（{exc}）。请检查目录权限、磁盘空间与文件是否被占用。"
            ) from exc

    def size_bytes(self) -> int:
        return self.path.stat().st_size if self.path.exists() else 0

    def vacuum(self) -> None:
        conn = self.connect()
        try:
            conn.execute("VACUUM")
        finally:
            conn.close()


def migration_statements(statements: Sequence[str]) -> tuple[str, ...]:
    """迁移语句保持显式列表，避免按分号切分触发 SQL 语法陷阱。"""
    return tuple(statement.strip() for statement in statements if statement.strip())
