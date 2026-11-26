"""仅迁移自习室和座位；保留现有资源，不导入账号和预约。"""
from __future__ import annotations

import argparse
from contextlib import closing
from datetime import datetime
from pathlib import Path
import sqlite3
import uuid

TABLES = {
    'rooms': ('id', 'name', 'location', 'status', 'open_slot', 'close_slot', 'created_at'),
    'seats': ('id', 'room_id', 'seat_type', 'capacity', 'admin_status', 'has_power',
              'is_window', 'note', 'created_at'),
}


def migrate(source: Path, target: Path) -> dict:
    source, target = source.resolve(strict=True), target.resolve(strict=True)
    if source == target or source.samefile(target):
        raise ValueError('Source and destination must be different databases.')
    with closing(sqlite3.connect(source.as_uri() + '?mode=ro', uri=True, timeout=10)) as old:
        old.execute('BEGIN')
        rows = {table: old.execute(f'SELECT {",".join(cols)} FROM {table}').fetchall()
                for table, cols in TABLES.items()}
        old.rollback()
    if not rows['rooms'] or not rows['seats']:
        raise ValueError('Old database has no rooms or seats. No changes made.')
    backup = target.parent / 'backups' / (
        f'{target.stem}-before-resources-{datetime.now():%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:8]}.db'
    )
    backup.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(target.as_uri() + '?mode=rw', uri=True, timeout=15)) as new:
        new.execute('PRAGMA foreign_keys=ON')
        # SQLite backup includes committed WAL content, unlike copying just the .db file.
        with closing(sqlite3.connect(backup)) as saved:
            new.backup(saved)
        new.execute('BEGIN IMMEDIATE')
        try:
            counts = {}
            for table, columns in TABLES.items():
                existing = {r[0] for r in new.execute(f'SELECT id FROM {table}')}
                pending = [r for r in rows[table] if r[0] not in existing]
                marks = ','.join('?' for _ in columns)
                new.executemany(f'INSERT INTO {table} ({",".join(columns)}) VALUES ({marks})', pending)
                counts[table] = {'added': len(pending), 'kept_existing': len(rows[table])-len(pending),
                                 'total': new.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]}
            if new.execute('PRAGMA foreign_key_check').fetchone():
                raise ValueError('Foreign key check failed; import rolled back.')
            new.commit()
        except Exception:
            new.rollback()
            raise
    return {'backup': str(backup), **counts}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--target', type=Path, required=True)
    args = parser.parse_args()
    result = migrate(args.source, args.target)
    print('Backup:', result['backup'])
    for table in TABLES:
        print(table, result[table])
    print('Done. Accounts and reservations unchanged. Refresh the seat list.')
