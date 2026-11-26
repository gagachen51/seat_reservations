"""数据库迁移定义（版本号只增不减）。"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Migration:
    version: int
    name: str
    statements: tuple[str, ...]


_V1 = (
    """
    CREATE TABLE users (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        role TEXT NOT NULL CHECK (role IN ('student','teacher','room_admin','sys_admin')),
        department TEXT NOT NULL DEFAULT '',
        class_name TEXT NOT NULL DEFAULT '',
        credit INTEGER NOT NULL DEFAULT 100,
        status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','disabled')),
        limit_type TEXT NOT NULL DEFAULT 'none'
            CHECK (limit_type IN ('none','credit','consecutive','blacklist')),
        limit_until TEXT,
        consecutive_no_show INTEGER NOT NULL DEFAULT 0,
        password_hash TEXT NOT NULL,
        must_change_password INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """,
    "CREATE INDEX ix_users_role ON users(role)",
    "CREATE INDEX ix_users_class ON users(class_name)",
    """
    CREATE TABLE rooms (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        location TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open','closed')),
        open_slot INTEGER NOT NULL DEFAULT 14 CHECK (open_slot BETWEEN 0 AND 47),
        close_slot INTEGER NOT NULL DEFAULT 46 CHECK (close_slot BETWEEN 1 AND 48),
        created_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE seats (
        id TEXT PRIMARY KEY,
        room_id TEXT NOT NULL REFERENCES rooms(id) ON DELETE RESTRICT,
        seat_type TEXT NOT NULL DEFAULT 'standard'
            CHECK (seat_type IN ('standard','power','quiet','discussion')),
        capacity INTEGER NOT NULL DEFAULT 1 CHECK (capacity BETWEEN 1 AND 20),
        admin_status TEXT NOT NULL DEFAULT 'available'
            CHECK (admin_status IN ('available','maintenance','disabled')),
        has_power INTEGER NOT NULL DEFAULT 0,
        is_window INTEGER NOT NULL DEFAULT 0,
        note TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL
    )
    """,
    "CREATE INDEX ix_seats_room ON seats(room_id)",
    """
    CREATE TABLE reservations (
        id TEXT PRIMARY KEY,
        user_id TEXT NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
        seat_id TEXT NOT NULL REFERENCES seats(id) ON DELETE RESTRICT,
        date TEXT NOT NULL,
        start_slot INTEGER NOT NULL CHECK (start_slot BETWEEN 0 AND 47),
        end_slot INTEGER NOT NULL CHECK (end_slot BETWEEN 1 AND 48),
        start_at TEXT NOT NULL,
        end_at TEXT NOT NULL,
        status TEXT NOT NULL
            CHECK (status IN ('pending','in_use','away','completed','cancelled','no_show','released')),
        source TEXT NOT NULL DEFAULT 'self' CHECK (source IN ('self','waitlist')),
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        checked_in_at TEXT,
        checkin_deadline TEXT,
        away_at TEXT,
        away_deadline TEXT,
        checked_out_at TEXT,
        end_reason TEXT,
        credential_code TEXT,
        credential_issued_at TEXT,
        credential_used INTEGER NOT NULL DEFAULT 0,
        policy_json TEXT NOT NULL,
        version INTEGER NOT NULL DEFAULT 1,
        CHECK (end_slot > start_slot)
    )
    """,
    "CREATE INDEX ix_reservations_user_date ON reservations(user_id, date)",
    "CREATE INDEX ix_reservations_seat_date ON reservations(seat_id, date)",
    "CREATE INDEX ix_reservations_status ON reservations(status)",
    "CREATE INDEX ix_reservations_start ON reservations(start_at)",
    "CREATE INDEX ix_reservations_credential ON reservations(credential_code)",
    """
    CREATE TABLE reservation_slots (
        reservation_id TEXT NOT NULL REFERENCES reservations(id) ON DELETE CASCADE,
        seat_id TEXT NOT NULL,
        user_id TEXT NOT NULL,
        date TEXT NOT NULL,
        slot INTEGER NOT NULL CHECK (slot BETWEEN 0 AND 47),
        PRIMARY KEY (seat_id, date, slot)
    )
    """,
    "CREATE UNIQUE INDEX ux_slots_user ON reservation_slots(user_id, date, slot)",
    "CREATE INDEX ix_slots_reservation ON reservation_slots(reservation_id)",
    """
    CREATE TABLE violations (
        id TEXT PRIMARY KEY,
        reservation_id TEXT NOT NULL REFERENCES reservations(id) ON DELETE CASCADE,
        user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        type TEXT NOT NULL,
        points INTEGER NOT NULL CHECK (points >= 0),
        source TEXT NOT NULL DEFAULT 'system' CHECK (source IN ('system','manual')),
        status TEXT NOT NULL DEFAULT 'active'
            CHECK (status IN ('active','appealing','revoked')),
        occurred_at TEXT NOT NULL,
        note TEXT NOT NULL DEFAULT '',
        operator_id TEXT,
        created_at TEXT NOT NULL,
        appeal_reason TEXT,
        appeal_at TEXT,
        review_note TEXT,
        reviewed_by TEXT,
        reviewed_at TEXT
    )
    """,
    "CREATE INDEX ix_violations_user ON violations(user_id, occurred_at)",
    "CREATE INDEX ix_violations_status ON violations(status)",
    """
    CREATE TABLE credit_logs (
        id TEXT PRIMARY KEY,
        user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        delta INTEGER NOT NULL,
        balance_after INTEGER NOT NULL,
        reason TEXT NOT NULL,
        violation_id TEXT,
        operator_id TEXT,
        created_at TEXT NOT NULL
    )
    """,
    "CREATE INDEX ix_credit_user_time ON credit_logs(user_id, created_at)",
    """
    CREATE TABLE waitlist (
        id TEXT PRIMARY KEY,
        user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        seat_id TEXT NOT NULL REFERENCES seats(id) ON DELETE CASCADE,
        date TEXT NOT NULL,
        start_slot INTEGER NOT NULL,
        end_slot INTEGER NOT NULL,
        status TEXT NOT NULL
            CHECK (status IN ('queued','notified','confirmed','expired','cancelled')),
        seq INTEGER NOT NULL,
        created_at TEXT NOT NULL,
        notified_at TEXT,
        confirm_deadline TEXT,
        defer_count INTEGER NOT NULL DEFAULT 0,
        CHECK (end_slot > start_slot)
    )
    """,
    "CREATE INDEX ix_waitlist_slot ON waitlist(seat_id, date, start_slot, end_slot, status)",
    "CREATE INDEX ix_waitlist_user ON waitlist(user_id)",
    """
    CREATE TABLE notifications (
        id TEXT PRIMARY KEY,
        user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        type TEXT NOT NULL,
        content TEXT NOT NULL,
        channel TEXT NOT NULL DEFAULT 'inbox' CHECK (channel IN ('inbox','sms','wechat')),
        status TEXT NOT NULL DEFAULT 'sent' CHECK (status IN ('sent','failed','read')),
        created_at TEXT NOT NULL,
        read_at TEXT,
        dedupe_key TEXT
    )
    """,
    "CREATE UNIQUE INDEX ux_notifications_dedupe ON notifications(user_id, dedupe_key) WHERE dedupe_key IS NOT NULL",
    "CREATE INDEX ix_notifications_user_time ON notifications(user_id, created_at)",
    """
    CREATE TABLE audit_logs (
        id TEXT PRIMARY KEY,
        actor_id TEXT NOT NULL,
        actor_role TEXT NOT NULL,
        action TEXT NOT NULL,
        target_type TEXT NOT NULL,
        target_id TEXT NOT NULL,
        before_json TEXT,
        after_json TEXT,
        ip TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL
    )
    """,
    "CREATE INDEX ix_audit_time ON audit_logs(created_at)",
    "CREATE INDEX ix_audit_actor ON audit_logs(actor_id)",
    """
    CREATE TRIGGER trg_audit_no_update
    BEFORE UPDATE ON audit_logs
    BEGIN
        SELECT RAISE(ABORT, 'audit_logs is append-only');
    END
    """,
    """
    CREATE TRIGGER trg_audit_no_delete
    BEFORE DELETE ON audit_logs
    BEGIN
        SELECT RAISE(ABORT, 'audit_logs is append-only');
    END
    """,
    """
    CREATE TABLE system_params (
        code TEXT PRIMARY KEY,
        value TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        updated_by TEXT
    )
    """,
    """
    CREATE TABLE complaints (
        id TEXT PRIMARY KEY,
        user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        seat_id TEXT,
        reservation_id TEXT,
        type TEXT NOT NULL CHECK (type IN ('occupied_seat','noise','facility','misjudged')),
        content TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','resolved','rated')),
        feedback TEXT,
        resolved_by TEXT,
        resolved_at TEXT,
        rating INTEGER CHECK (rating IS NULL OR rating BETWEEN 1 AND 5),
        rating_comment TEXT,
        created_at TEXT NOT NULL
    )
    """,
    "CREATE INDEX ix_complaints_status ON complaints(status, created_at)",
    "CREATE INDEX ix_complaints_user ON complaints(user_id)",
    """
    CREATE TABLE reviews (
        id TEXT PRIMARY KEY,
        reservation_id TEXT NOT NULL UNIQUE REFERENCES reservations(id) ON DELETE CASCADE,
        user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        seat_id TEXT NOT NULL REFERENCES seats(id) ON DELETE CASCADE,
        rating INTEGER NOT NULL CHECK (rating BETWEEN 1 AND 5),
        tags_json TEXT NOT NULL DEFAULT '[]',
        content TEXT NOT NULL DEFAULT '',
        reply TEXT,
        replied_by TEXT,
        replied_at TEXT,
        created_at TEXT NOT NULL
    )
    """,
    "CREATE INDEX ix_reviews_seat ON reviews(seat_id)",
    """
    CREATE TABLE blacklist (
        id TEXT PRIMARY KEY,
        user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        reason TEXT NOT NULL,
        source TEXT NOT NULL CHECK (source IN ('auto','manual')),
        start_at TEXT NOT NULL,
        end_at TEXT NOT NULL,
        released_at TEXT,
        released_by TEXT,
        created_by TEXT,
        created_at TEXT NOT NULL
    )
    """,
    "CREATE INDEX ix_blacklist_user ON blacklist(user_id, end_at)",
    """
    CREATE TABLE seat_blocks (
        id TEXT PRIMARY KEY,
        seat_id TEXT NOT NULL REFERENCES seats(id) ON DELETE CASCADE,
        reason TEXT NOT NULL,
        start_at TEXT NOT NULL,
        end_at TEXT NOT NULL,
        created_by TEXT NOT NULL,
        created_at TEXT NOT NULL,
        released_at TEXT
    )
    """,
    "CREATE INDEX ix_seat_blocks_seat ON seat_blocks(seat_id, start_at, end_at)",
    """
    CREATE TABLE sequences (
        name TEXT PRIMARY KEY,
        value INTEGER NOT NULL
    )
    """,
    """
    CREATE TABLE sessions (
        token_hash TEXT PRIMARY KEY,
        user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        created_at TEXT NOT NULL,
        expires_at TEXT NOT NULL,
        last_seen_at TEXT NOT NULL,
        ip TEXT NOT NULL DEFAULT '',
        user_agent TEXT NOT NULL DEFAULT '',
        csrf_token TEXT NOT NULL
    )
    """,
    "CREATE INDEX ix_sessions_user ON sessions(user_id)",
    "CREATE INDEX ix_sessions_expiry ON sessions(expires_at)",
)


# v2：座位封禁生命周期（FR-21 / BR-09 / PR-06）
# 只加列、**不回填**：
#   maintenance_by_block = 1 → 维护状态由封禁流程设置，封禁到期/释放时可自动恢复为 available
#   maintenance_by_block = 0 → 非封禁管理（手工维护/停用/正常），任何封禁到期都不得自动恢复
# 历史数据没有“维护来源”记录，按“手工维护”保守处理，避免把人工维护的座位误恢复。
_V2 = (
    "ALTER TABLE seats ADD COLUMN maintenance_by_block INTEGER NOT NULL DEFAULT 0",
)


# v4：B 模块资源/预约约束迁移（``RESOURCE_MIGRATION_VERSION = 4``；v3 保留给共享层审计）
#
# 目标（L-07）：
#   1) ``reservation_slots`` 的 seat_id/user_id 必须与父预约一致（触发器兜底，SQLite 无法 ALTER ADD 组合外键）
#   2) ``waitlist`` 的时段范围限制为 0..47 / 1..48 且 end_slot > start_slot（重建表加 CHECK）
#   3) ``complaints`` 的 seat_id/reservation_id 改为可空外键（重建表）
#   4) ``seats`` 房间内座位序号唯一（表达式唯一索引，序号取 id 中最后一个 '-' 之后的部分）
#
# 兼容策略：先清理历史不合规数据（不改预约业务语义、不删除预约），再建约束；
# 重复执行安全（IF NOT EXISTS / 重建前先清理）。
_V4 = (
    # ---- 1. reservation_slots 与父预约一致性 ----
    """
    CREATE TRIGGER IF NOT EXISTS trg_slots_parent_insert
    BEFORE INSERT ON reservation_slots
    FOR EACH ROW
    WHEN NOT EXISTS (
        SELECT 1 FROM reservations r
        WHERE r.id = NEW.reservation_id
          AND r.seat_id = NEW.seat_id
          AND r.user_id = NEW.user_id
    )
    BEGIN
        SELECT RAISE(ABORT, 'reservation_slots 与预约记录的 seat_id/user_id 不一致');
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS trg_slots_parent_update
    BEFORE UPDATE ON reservation_slots
    FOR EACH ROW
    WHEN NOT EXISTS (
        SELECT 1 FROM reservations r
        WHERE r.id = NEW.reservation_id
          AND r.seat_id = NEW.seat_id
          AND r.user_id = NEW.user_id
    )
    BEGIN
        SELECT RAISE(ABORT, 'reservation_slots 与预约记录的 seat_id/user_id 不一致');
    END
    """,
    # ---- 2. waitlist 时段范围（先清理，再重建加 CHECK）----
    "UPDATE waitlist SET start_slot = MIN(MAX(start_slot, 0), 47) WHERE start_slot < 0 OR start_slot > 47",
    "UPDATE waitlist SET end_slot = MIN(MAX(end_slot, 1), 48) WHERE end_slot < 1 OR end_slot > 48",
    "UPDATE waitlist SET end_slot = start_slot + 1 WHERE end_slot <= start_slot AND start_slot < 47",
    "UPDATE waitlist SET start_slot = 47, end_slot = 48 WHERE end_slot <= start_slot AND start_slot >= 47",
    """
    CREATE TABLE waitlist_v4 (
        id TEXT PRIMARY KEY,
        user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        seat_id TEXT NOT NULL REFERENCES seats(id) ON DELETE CASCADE,
        date TEXT NOT NULL,
        start_slot INTEGER NOT NULL CHECK (start_slot BETWEEN 0 AND 47),
        end_slot INTEGER NOT NULL CHECK (end_slot BETWEEN 1 AND 48),
        status TEXT NOT NULL
            CHECK (status IN ('queued','notified','confirmed','expired','cancelled')),
        seq INTEGER NOT NULL,
        created_at TEXT NOT NULL,
        notified_at TEXT,
        confirm_deadline TEXT,
        defer_count INTEGER NOT NULL DEFAULT 0,
        CHECK (end_slot > start_slot)
    )
    """,
    """
    INSERT INTO waitlist_v4(
        id, user_id, seat_id, date, start_slot, end_slot, status, seq,
        created_at, notified_at, confirm_deadline, defer_count
    )
    SELECT id, user_id, seat_id, date, start_slot, end_slot, status, seq,
           created_at, notified_at, confirm_deadline, defer_count
    FROM waitlist
    """,
    "DROP TABLE waitlist",
    "ALTER TABLE waitlist_v4 RENAME TO waitlist",
    "CREATE INDEX IF NOT EXISTS ix_waitlist_slot ON waitlist(seat_id, date, start_slot, end_slot, status)",
    "CREATE INDEX IF NOT EXISTS ix_waitlist_user ON waitlist(user_id)",
    # ---- 3. complaints 的 seat/reservation 可空外键（先清理孤儿，再重建）----
    "UPDATE complaints SET seat_id = NULL WHERE seat_id IS NOT NULL AND seat_id NOT IN (SELECT id FROM seats)",
    "UPDATE complaints SET reservation_id = NULL WHERE reservation_id IS NOT NULL AND reservation_id NOT IN (SELECT id FROM reservations)",
    """
    CREATE TABLE complaints_v4 (
        id TEXT PRIMARY KEY,
        user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        seat_id TEXT REFERENCES seats(id) ON DELETE SET NULL,
        reservation_id TEXT REFERENCES reservations(id) ON DELETE SET NULL,
        type TEXT NOT NULL CHECK (type IN ('occupied_seat','noise','facility','misjudged')),
        content TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','resolved','rated')),
        feedback TEXT,
        resolved_by TEXT,
        resolved_at TEXT,
        rating INTEGER CHECK (rating IS NULL OR rating BETWEEN 1 AND 5),
        rating_comment TEXT,
        created_at TEXT NOT NULL
    )
    """,
    """
    INSERT INTO complaints_v4(
        id, user_id, seat_id, reservation_id, type, content, status,
        feedback, resolved_by, resolved_at, rating, rating_comment, created_at
    )
    SELECT id, user_id, seat_id, reservation_id, type, content, status,
           feedback, resolved_by, resolved_at, rating, rating_comment, created_at
    FROM complaints
    """,
    "DROP TABLE complaints",
    "ALTER TABLE complaints_v4 RENAME TO complaints",
    "CREATE INDEX IF NOT EXISTS ix_complaints_status ON complaints(status, created_at)",
    "CREATE INDEX IF NOT EXISTS ix_complaints_user ON complaints(user_id)",
    # ---- 4. 座位编号重复：由 seats.id 主键保证（无需额外约束）----
    # 说明：`seats.id` 已是全局主键，重复编号在插入时即被拒绝；
    # “房间内序号唯一”与既有业务“座位可跨自习室移动（update_seat）”冲突
    # （移动后同房间可能出现两个同序号座位），故本期不新增该约束，见整改报告 L-07。
)


MIGRATIONS: tuple[Migration, ...] = (
    Migration(1, "initial_schema", _V1),
    Migration(2, "seat_block_lifecycle", _V2),
    Migration(4, "resource_constraints", _V4),
)

# 公共底座预留版本号（只增不减，后续迁移直接使用，不得复用）。
# v3：共享层审计表/审计字段扩展；v4：B 模块资源/预约约束迁移。
LATEST_MIGRATION_VERSION = 4
AUDIT_MIGRATION_VERSION = 3
RESOURCE_MIGRATION_VERSION = 4
