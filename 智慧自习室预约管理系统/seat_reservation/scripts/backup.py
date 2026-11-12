#!/usr/bin/env python3
"""在线备份 SQLite 数据库。

用法：
    python scripts/backup.py                     # 备份到 backup/，文件名带时间戳
    python scripts/backup.py --out D:/bak        # 指定输出目录
    python scripts/backup.py --keep 30           # 额外清理 30 天前的备份

设计要点：
* 使用 sqlite3 的在线备份接口，服务运行中也能取得一致快照；
* 备份文件转为单文件模式（journal_mode=DELETE），便于异地拷贝；
* 备份后立即 integrity_check，不通过则以非零码退出，便于定时任务告警；
* 默认不删除任何文件；只有显式传入 --keep 才会清理旧备份。
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import sqlite3
import sys
from pathlib import Path

DEFAULT_DB = Path("var") / "seat_reservation.db"
DEFAULT_OUT = Path("backup")


def backup(db_path: Path, out_dir: Path) -> Path:
    if not db_path.exists():
        raise SystemExit(f"数据库不存在：{db_path}")
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    target = out_dir / f"seat-{stamp}.db"

    source = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    destination = sqlite3.connect(target)
    try:
        with destination:
            source.backup(destination)
        destination.execute("PRAGMA journal_mode=DELETE")
        destination.commit()
    finally:
        destination.close()
        source.close()

    check = sqlite3.connect(target)
    try:
        status = check.execute("PRAGMA integrity_check").fetchone()[0]
        tables = check.execute(
            "SELECT count(*) FROM sqlite_master WHERE type='table'"
        ).fetchone()[0]
    finally:
        check.close()

    if status != "ok":
        raise SystemExit(f"备份完整性校验失败：{status}（文件 {target}）")
    print(f"备份完成 {target} 完整性={status} 表数={tables} 大小={target.stat().st_size} 字节")
    return target


def prune(out_dir: Path, keep_days: int) -> None:
    cutoff = dt.datetime.now() - dt.timedelta(days=keep_days)
    removed = 0
    for item in sorted(out_dir.glob("seat-*.db")):
        if dt.datetime.fromtimestamp(item.stat().st_mtime) < cutoff:
            item.unlink()
            removed += 1
    if removed:
        print(f"已清理 {removed} 个超过 {keep_days} 天的备份")


def main() -> int:
    parser = argparse.ArgumentParser(description="在线备份 SQLite 数据库")
    parser.add_argument(
        "--db",
        default=os.getenv("SEAT_DB_PATH", str(DEFAULT_DB)),
        help="数据库路径，默认取 SEAT_DB_PATH 或 var/seat_reservation.db",
    )
    parser.add_argument("--out", default=str(DEFAULT_OUT), help="备份输出目录")
    parser.add_argument(
        "--keep",
        type=int,
        default=0,
        help="保留天数；大于 0 时才清理更早的备份",
    )
    args = parser.parse_args()

    out_dir = Path(args.out)
    backup(Path(args.db), out_dir)
    if args.keep > 0:
        prune(out_dir, args.keep)
    return 0


if __name__ == "__main__":
    sys.exit(main())
