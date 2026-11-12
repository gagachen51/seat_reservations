"""SQLite 仓储实现。

* 一个仓储实例绑定一个连接，生命周期由上层事务控制。
* 所有 SQL 参数化；日期时间以 ``YYYY-MM-DD HH:MM:SS`` 文本存储，便于排序与比较。
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timedelta
from typing import Any, Iterable, Sequence

from ..domain.models import (
    AuditLog,
    BlacklistRecord,
    Complaint,
    Notification,
    Page,
    Reservation,
    ReservationView,
    Review,
    Room,
    Seat,
    SeatBlock,
    SeatView,
    User,
    Violation,
    WaitlistEntry,
    OCCUPYING_STATUSES,
    QUOTA_STATUSES,
    SEAT_STATE_LABELS,
)
from ..domain.params import ParamSet
from ..domain.rules import seat_state_from_reservation
from ..domain.timeutil import DATETIME_FMT, fmt_datetime, hhmm, minutes_of_slots
from ..errors import SlotConflict


def _dt(value: str | None) -> datetime | None:
    return datetime.strptime(value, DATETIME_FMT) if value else None


def _d(value: str) -> date:
    return date.fromisoformat(value)


def _json(value: str | None) -> Any:
    if not value:
        return None
    try:
        return json.loads(value)
    except json.JSONDecodeError:  # pragma: no cover - 防御性
        return None


def _placeholders(count: int) -> str:
    return ",".join("?" for _ in range(count))


class SqliteRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    # ------------------------------------------------------------------
    # 行映射
    # ------------------------------------------------------------------
    @staticmethod
    def _user(row: sqlite3.Row) -> User:
        return User(
            id=row["id"],
            name=row["name"],
            role=row["role"],
            department=row["department"],
            class_name=row["class_name"],
            credit=row["credit"],
            status=row["status"],
            limit_type=row["limit_type"],
            limit_until=_dt(row["limit_until"]),
            consecutive_no_show=row["consecutive_no_show"],
            password_hash=row["password_hash"],
            must_change_password=bool(row["must_change_password"]),
            created_at=_dt(row["created_at"]),
            updated_at=_dt(row["updated_at"]),
        )

    @staticmethod
    def _room(row: sqlite3.Row) -> Room:
        return Room(
            id=row["id"],
            name=row["name"],
            location=row["location"],
            status=row["status"],
            open_slot=row["open_slot"],
            close_slot=row["close_slot"],
            created_at=_dt(row["created_at"]),
        )

    @staticmethod
    def _seat(row: sqlite3.Row) -> Seat:
        return Seat(
            id=row["id"],
            room_id=row["room_id"],
            seat_type=row["seat_type"],
            capacity=row["capacity"],
            admin_status=row["admin_status"],
            has_power=bool(row["has_power"]),
            is_window=bool(row["is_window"]),
            note=row["note"],
            created_at=_dt(row["created_at"]),
        )

    @staticmethod
    def _reservation(row: sqlite3.Row) -> Reservation:
        return Reservation(
            id=row["id"],
            user_id=row["user_id"],
            seat_id=row["seat_id"],
            day=_d(row["date"]),
            start_slot=row["start_slot"],
            end_slot=row["end_slot"],
            start_at=_dt(row["start_at"]) or datetime.min,
            end_at=_dt(row["end_at"]) or datetime.min,
            status=row["status"],
            source=row["source"],
            created_at=_dt(row["created_at"]),
            updated_at=_dt(row["updated_at"]),
            checked_in_at=_dt(row["checked_in_at"]),
            checkin_deadline=_dt(row["checkin_deadline"]),
            away_at=_dt(row["away_at"]),
            away_deadline=_dt(row["away_deadline"]),
            checked_out_at=_dt(row["checked_out_at"]),
            end_reason=row["end_reason"],
            credential_code=row["credential_code"],
            credential_issued_at=_dt(row["credential_issued_at"]),
            credential_used=bool(row["credential_used"]),
            policy=_json(row["policy_json"]) or {},
            version=row["version"],
        )

    @staticmethod
    def _violation(row: sqlite3.Row) -> Violation:
        return Violation(
            id=row["id"],
            reservation_id=row["reservation_id"],
            user_id=row["user_id"],
            violation_type=row["type"],
            points=row["points"],
            source=row["source"],
            status=row["status"],
            occurred_at=_dt(row["occurred_at"]),
            note=row["note"],
            operator_id=row["operator_id"],
            created_at=_dt(row["created_at"]),
            appeal_reason=row["appeal_reason"],
            appeal_at=_dt(row["appeal_at"]),
            review_note=row["review_note"],
            reviewed_by=row["reviewed_by"],
            reviewed_at=_dt(row["reviewed_at"]),
        )

    @staticmethod
    def _waitlist(row: sqlite3.Row) -> WaitlistEntry:
        return WaitlistEntry(
            id=row["id"],
            user_id=row["user_id"],
            seat_id=row["seat_id"],
            day=_d(row["date"]),
            start_slot=row["start_slot"],
            end_slot=row["end_slot"],
            status=row["status"],
            seq=row["seq"],
            created_at=_dt(row["created_at"]),
            notified_at=_dt(row["notified_at"]),
            confirm_deadline=_dt(row["confirm_deadline"]),
            defer_count=row["defer_count"],
        )

    @staticmethod
    def _complaint(row: sqlite3.Row) -> Complaint:
        return Complaint(
            id=row["id"],
            user_id=row["user_id"],
            complaint_type=row["type"],
            content=row["content"],
            seat_id=row["seat_id"],
            reservation_id=row["reservation_id"],
            status=row["status"],
            feedback=row["feedback"],
            resolved_by=row["resolved_by"],
            resolved_at=_dt(row["resolved_at"]),
            rating=row["rating"],
            rating_comment=row["rating_comment"],
            created_at=_dt(row["created_at"]),
        )

    @staticmethod
    def _review(row: sqlite3.Row) -> Review:
        return Review(
            id=row["id"],
            reservation_id=row["reservation_id"],
            user_id=row["user_id"],
            seat_id=row["seat_id"],
            rating=row["rating"],
            tags=_json(row["tags_json"]) or [],
            content=row["content"],
            reply=row["reply"],
            replied_by=row["replied_by"],
            replied_at=_dt(row["replied_at"]),
            created_at=_dt(row["created_at"]),
        )

    # ------------------------------------------------------------------
    # 用户
    # ------------------------------------------------------------------
    def get_user(self, user_id: str) -> User | None:
        row = self.conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return self._user(row) if row else None

    def save_user(self, user: User) -> None:
        now = fmt_datetime(user.updated_at or datetime.now())
        self.conn.execute(
            """
            INSERT INTO users(id, name, role, department, class_name, credit, status, limit_type,
                              limit_until, consecutive_no_show, password_hash, must_change_password,
                              created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                name=excluded.name,
                role=excluded.role,
                department=excluded.department,
                class_name=excluded.class_name,
                credit=excluded.credit,
                status=excluded.status,
                limit_type=excluded.limit_type,
                limit_until=excluded.limit_until,
                consecutive_no_show=excluded.consecutive_no_show,
                password_hash=excluded.password_hash,
                must_change_password=excluded.must_change_password,
                updated_at=excluded.updated_at
            """,
            (
                user.id,
                user.name,
                user.role,
                user.department,
                user.class_name,
                user.credit,
                user.status,
                user.limit_type,
                fmt_datetime(user.limit_until),
                user.consecutive_no_show,
                user.password_hash,
                int(user.must_change_password),
                fmt_datetime(user.created_at or datetime.now()),
                now,
            ),
        )

    def list_users(
        self,
        *,
        role: str | None = None,
        class_name: str | None = None,
        keyword: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> Page:
        where: list[str] = []
        params: list[Any] = []
        if role:
            where.append("role = ?")
            params.append(role)
        if class_name:
            where.append("class_name = ?")
            params.append(class_name)
        if keyword:
            where.append("(id LIKE ? OR name LIKE ?)")
            params.extend([f"%{keyword}%", f"%{keyword}%"])
        clause = (" WHERE " + " AND ".join(where)) if where else ""
        total = self.conn.execute(f"SELECT COUNT(*) AS c FROM users{clause}", params).fetchone()["c"]
        rows = self.conn.execute(
            f"SELECT * FROM users{clause} ORDER BY role, id LIMIT ? OFFSET ?",
            [*params, page_size, (page - 1) * page_size],
        ).fetchall()
        return Page([self._user(row) for row in rows], total, page, page_size)

    def count_sys_admins(self, exclude: str | None = None) -> int:
        sql = "SELECT COUNT(*) AS c FROM users WHERE role='sys_admin' AND status='active'"
        params: list[Any] = []
        if exclude:
            sql += " AND id != ?"
            params.append(exclude)
        return int(self.conn.execute(sql, params).fetchone()["c"])

    def students_of_class(self, class_name: str) -> list[User]:
        rows = self.conn.execute(
            "SELECT * FROM users WHERE role='student' AND class_name = ? ORDER BY id",
            (class_name,),
        ).fetchall()
        return [self._user(row) for row in rows]

    # ------------------------------------------------------------------
    # 自习室 / 座位
    # ------------------------------------------------------------------
    def get_room(self, room_id: str) -> Room | None:
        row = self.conn.execute("SELECT * FROM rooms WHERE id = ?", (room_id,)).fetchone()
        return self._room(row) if row else None

    def list_rooms(self) -> list[Room]:
        rows = self.conn.execute("SELECT * FROM rooms ORDER BY id").fetchall()
        return [self._room(row) for row in rows]

    def save_room(self, room: Room) -> None:
        self.conn.execute(
            """
            INSERT INTO rooms(id, name, location, status, open_slot, close_slot, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                name=excluded.name, location=excluded.location, status=excluded.status,
                open_slot=excluded.open_slot, close_slot=excluded.close_slot
            """,
            (
                room.id,
                room.name,
                room.location,
                room.status,
                room.open_slot,
                room.close_slot,
                fmt_datetime(room.created_at or datetime.now()),
            ),
        )

    def get_seat(self, seat_id: str) -> Seat | None:
        row = self.conn.execute("SELECT * FROM seats WHERE id = ?", (seat_id,)).fetchone()
        return self._seat(row) if row else None

    def list_seats(
        self, *, room_id: str | None = None, seat_type: str | None = None
    ) -> list[Seat]:
        where, params = [], []
        if room_id:
            where.append("room_id = ?")
            params.append(room_id)
        if seat_type:
            where.append("seat_type = ?")
            params.append(seat_type)
        clause = (" WHERE " + " AND ".join(where)) if where else ""
        rows = self.conn.execute(f"SELECT * FROM seats{clause} ORDER BY id", params).fetchall()
        return [self._seat(row) for row in rows]

    def save_seat(self, seat: Seat) -> None:
        self.conn.execute(
            """
            INSERT INTO seats(id, room_id, seat_type, capacity, admin_status, has_power, is_window, note, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                room_id=excluded.room_id, seat_type=excluded.seat_type, capacity=excluded.capacity,
                admin_status=excluded.admin_status, has_power=excluded.has_power,
                is_window=excluded.is_window, note=excluded.note
            """,
            (
                seat.id,
                seat.room_id,
                seat.seat_type,
                seat.capacity,
                seat.admin_status,
                int(seat.has_power),
                int(seat.is_window),
                seat.note,
                fmt_datetime(seat.created_at or datetime.now()),
            ),
        )

    def seat_views(
        self,
        day: date,
        start_slot: int,
        end_slot: int,
        *,
        room_id: str | None = None,
        seat_type: str | None = None,
        has_power: bool | None = None,
        is_window: bool | None = None,
        only_available: bool = False,
    ) -> list[SeatView]:
        where, params = ["1=1"], []
        if room_id:
            where.append("s.room_id = ?")
            params.append(room_id)
        if seat_type:
            where.append("s.seat_type = ?")
            params.append(seat_type)
        if has_power is not None:
            where.append("s.has_power = ?")
            params.append(int(has_power))
        if is_window is not None:
            where.append("s.is_window = ?")
            params.append(int(is_window))
        rows = self.conn.execute(
            f"""
            SELECT s.*, r.name AS room_name
            FROM seats s JOIN rooms r ON r.id = s.room_id
            WHERE {' AND '.join(where)}
            ORDER BY s.room_id, s.id
            """,
            params,
        ).fetchall()

        occupied: dict[str, tuple[str, str]] = {}
        for row in self.conn.execute(
            """
            SELECT sl.seat_id, r.status, r.id AS reservation_id
            FROM reservation_slots sl JOIN reservations r ON r.id = sl.reservation_id
            WHERE sl.date = ? AND sl.slot >= ? AND sl.slot < ?
            """,
            (day.isoformat(), start_slot, end_slot),
        ).fetchall():
            current = occupied.get(row["seat_id"])
            rank = {"in_use": 3, "away": 2, "pending": 1}
            if current is None or rank.get(row["status"], 0) > rank.get(current[0], 0):
                occupied[row["seat_id"]] = (row["status"], row["reservation_id"])

        block_start = datetime.combine(day, datetime.min.time()) + timedelta(minutes=start_slot * 30)
        block_end = datetime.combine(day, datetime.min.time()) + timedelta(minutes=end_slot * 30)
        blocked = {
            row["seat_id"]
            for row in self.conn.execute(
                """
                SELECT seat_id FROM seat_blocks
                WHERE released_at IS NULL AND start_at < ? AND end_at > ?
                """,
                (fmt_datetime(block_end), fmt_datetime(block_start)),
            ).fetchall()
        }

        views: list[SeatView] = []
        for row in rows:
            seat = self._seat(row)
            if seat.admin_status == "disabled":
                state = "disabled"
                holder = None
            elif seat.admin_status == "maintenance" or seat.id in blocked:
                state = "maintenance"
                holder = None
            elif seat.id in occupied:
                status, holder = occupied[seat.id]
                state = seat_state_from_reservation(status).value
            else:
                state, holder = "available", None
            view = SeatView(
                seat=seat,
                room_name=row["room_name"],
                state=state,
                state_label=SEAT_STATE_LABELS.get(state, state),
                available=state == "available",
                holder_reservation_id=holder,
            )
            if only_available and not view.available:
                continue
            views.append(view)
        return views

    # ------------------------------------------------------------------
    # 座位封禁
    # ------------------------------------------------------------------
    def insert_seat_block(self, block: SeatBlock) -> None:
        self.conn.execute(
            """
            INSERT INTO seat_blocks(id, seat_id, reason, start_at, end_at, created_by, created_at, released_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                block.id,
                block.seat_id,
                block.reason,
                fmt_datetime(block.start_at),
                fmt_datetime(block.end_at),
                block.created_by,
                fmt_datetime(block.created_at or datetime.now()),
                fmt_datetime(block.released_at),
            ),
        )

    def close_seat_block(self, block_id: str, released_at: datetime) -> None:
        self.conn.execute(
            "UPDATE seat_blocks SET released_at = ? WHERE id = ? AND released_at IS NULL",
            (fmt_datetime(released_at), block_id),
        )

    def active_seat_blocks(self, seat_id: str | None = None) -> list[SeatBlock]:
        sql = "SELECT * FROM seat_blocks WHERE released_at IS NULL"
        params: list[Any] = []
        if seat_id:
            sql += " AND seat_id = ?"
            params.append(seat_id)
        rows = self.conn.execute(sql + " ORDER BY start_at", params).fetchall()
        return [
            SeatBlock(
                id=row["id"],
                seat_id=row["seat_id"],
                reason=row["reason"],
                start_at=_dt(row["start_at"]) or datetime.min,
                end_at=_dt(row["end_at"]) or datetime.min,
                created_by=row["created_by"],
                created_at=_dt(row["created_at"]),
                released_at=_dt(row["released_at"]),
            )
            for row in rows
        ]

    def seat_block_overlaps(self, seat_id: str, start: datetime, end: datetime) -> bool:
        row = self.conn.execute(
            """
            SELECT 1 FROM seat_blocks
            WHERE seat_id = ? AND released_at IS NULL AND start_at < ? AND end_at > ?
            LIMIT 1
            """,
            (seat_id, fmt_datetime(end), fmt_datetime(start)),
        ).fetchone()
        return row is not None

    # ------------------------------------------------------------------
    # 预约
    # ------------------------------------------------------------------
    def get_reservation(self, reservation_id: str) -> Reservation | None:
        row = self.conn.execute(
            "SELECT * FROM reservations WHERE id = ?", (reservation_id,)
        ).fetchone()
        return self._reservation(row) if row else None

    def find_reservation_by_credential(self, code: str) -> Reservation | None:
        row = self.conn.execute(
            """
            SELECT * FROM reservations
            WHERE credential_code = ? AND status = 'pending'
            ORDER BY credential_issued_at DESC LIMIT 1
            """,
            (code.upper(),),
        ).fetchone()
        return self._reservation(row) if row else None

    def find_user_active_reservation(
        self, user_id: str, exclude: str | None = None
    ) -> Reservation | None:
        sql = "SELECT * FROM reservations WHERE user_id = ? AND status IN ('in_use','away')"
        params: list[Any] = [user_id]
        if exclude:
            sql += " AND id != ?"
            params.append(exclude)
        row = self.conn.execute(sql + " LIMIT 1", params).fetchone()
        return self._reservation(row) if row else None

    def insert_reservation(self, reservation: Reservation) -> None:
        self.conn.execute(
            """
            INSERT INTO reservations(
                id, user_id, seat_id, date, start_slot, end_slot, start_at, end_at, status, source,
                created_at, updated_at, checked_in_at, checkin_deadline, away_at, away_deadline,
                checked_out_at, end_reason, credential_code, credential_issued_at, credential_used,
                policy_json, version)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                reservation.id,
                reservation.user_id,
                reservation.seat_id,
                reservation.day.isoformat(),
                reservation.start_slot,
                reservation.end_slot,
                fmt_datetime(reservation.start_at),
                fmt_datetime(reservation.end_at),
                reservation.status,
                reservation.source,
                fmt_datetime(reservation.created_at),
                fmt_datetime(reservation.updated_at),
                fmt_datetime(reservation.checked_in_at),
                fmt_datetime(reservation.checkin_deadline),
                fmt_datetime(reservation.away_at),
                fmt_datetime(reservation.away_deadline),
                fmt_datetime(reservation.checked_out_at),
                reservation.end_reason,
                reservation.credential_code,
                fmt_datetime(reservation.credential_issued_at),
                int(reservation.credential_used),
                json.dumps(reservation.policy, ensure_ascii=False),
                reservation.version,
            ),
        )

    def update_reservation(self, reservation: Reservation) -> None:
        self.conn.execute(
            """
            UPDATE reservations SET
                status=?, updated_at=?, checked_in_at=?, checkin_deadline=?, away_at=?, away_deadline=?,
                checked_out_at=?, end_reason=?, credential_code=?, credential_issued_at=?,
                credential_used=?, version=?
            WHERE id=?
            """,
            (
                reservation.status,
                fmt_datetime(reservation.updated_at),
                fmt_datetime(reservation.checked_in_at),
                fmt_datetime(reservation.checkin_deadline),
                fmt_datetime(reservation.away_at),
                fmt_datetime(reservation.away_deadline),
                fmt_datetime(reservation.checked_out_at),
                reservation.end_reason,
                reservation.credential_code,
                fmt_datetime(reservation.credential_issued_at),
                int(reservation.credential_used),
                reservation.version,
                reservation.id,
            ),
        )

    def occupy(self, reservation: Reservation) -> None:
        rows = [
            (
                reservation.id,
                reservation.seat_id,
                reservation.user_id,
                reservation.day.isoformat(),
                slot,
            )
            for slot in range(reservation.start_slot, reservation.end_slot)
        ]
        try:
            self.conn.executemany(
                "INSERT INTO reservation_slots(reservation_id, seat_id, user_id, date, slot) VALUES (?, ?, ?, ?, ?)",
                rows,
            )
        except sqlite3.IntegrityError as exc:
            raise SlotConflict("该座位或本人时段已被占用，请刷新后重试", rule="BR-11") from exc

    def release(self, reservation_id: str) -> None:
        self.conn.execute("DELETE FROM reservation_slots WHERE reservation_id = ?", (reservation_id,))

    def find_seat_conflict(
        self, seat_id: str, day: date, start_slot: int, end_slot: int
    ) -> str | None:
        row = self.conn.execute(
            """
            SELECT reservation_id FROM reservation_slots
            WHERE seat_id = ? AND date = ? AND slot >= ? AND slot < ? LIMIT 1
            """,
            (seat_id, day.isoformat(), start_slot, end_slot),
        ).fetchone()
        return row["reservation_id"] if row else None

    def find_user_conflict(
        self, user_id: str, day: date, start_slot: int, end_slot: int
    ) -> str | None:
        row = self.conn.execute(
            """
            SELECT reservation_id FROM reservation_slots
            WHERE user_id = ? AND date = ? AND slot >= ? AND slot < ? LIMIT 1
            """,
            (user_id, day.isoformat(), start_slot, end_slot),
        ).fetchone()
        return row["reservation_id"] if row else None

    def daily_usage(self, user_id: str, day: date) -> tuple[int, int]:
        row = self.conn.execute(
            f"""
            SELECT COUNT(*) AS c, COALESCE(SUM((end_slot - start_slot) * 30), 0) AS minutes
            FROM reservations
            WHERE user_id = ? AND date = ? AND status IN ({_placeholders(len(QUOTA_STATUSES))})
            """,
            (user_id, day.isoformat(), *sorted(QUOTA_STATUSES)),
        ).fetchone()
        return int(row["c"]), int(row["minutes"])

    def list_reservations(
        self,
        *,
        user_id: str | None = None,
        status: str | None = None,
        day: date | None = None,
        date_from: date | None = None,
        date_to: date | None = None,
        seat_id: str | None = None,
        room_id: str | None = None,
        keyword: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> Page:
        where, params = ["1=1"], []
        if user_id:
            where.append("r.user_id = ?")
            params.append(user_id)
        if status:
            where.append("r.status = ?")
            params.append(status)
        if day:
            where.append("r.date = ?")
            params.append(day.isoformat())
        if date_from:
            where.append("r.date >= ?")
            params.append(date_from.isoformat())
        if date_to:
            where.append("r.date <= ?")
            params.append(date_to.isoformat())
        if seat_id:
            where.append("r.seat_id = ?")
            params.append(seat_id)
        if room_id:
            where.append("s.room_id = ?")
            params.append(room_id)
        if keyword:
            where.append("(r.id LIKE ? OR r.user_id LIKE ? OR u.name LIKE ? OR r.seat_id LIKE ?)")
            params.extend([f"%{keyword}%"] * 4)
        clause = " AND ".join(where)
        base = """
            FROM reservations r
            JOIN seats s ON s.id = r.seat_id
            JOIN rooms rm ON rm.id = s.room_id
            JOIN users u ON u.id = r.user_id
            LEFT JOIN reviews rv ON rv.reservation_id = r.id
        """
        total = self.conn.execute(
            f"SELECT COUNT(*) AS c {base} WHERE {clause}", params
        ).fetchone()["c"]
        rows = self.conn.execute(
            f"""
            SELECT r.*, s.room_id AS room_id, rm.name AS room_name, u.name AS user_name,
                   (rv.id IS NOT NULL) AS has_review
            {base} WHERE {clause}
            ORDER BY r.start_at DESC, r.id DESC LIMIT ? OFFSET ?
            """,
            [*params, page_size, (page - 1) * page_size],
        ).fetchall()
        views = [
            ReservationView(
                reservation=self._reservation(row),
                seat_id=row["seat_id"],
                room_id=row["room_id"],
                room_name=row["room_name"],
                user_name=row["user_name"],
                has_review=bool(row["has_review"]),
            )
            for row in rows
        ]
        return Page(views, total, page, page_size)

    def list_active_overlapping(
        self, seat_id: str, start: datetime, end: datetime
    ) -> list[Reservation]:
        rows = self.conn.execute(
            f"""
            SELECT * FROM reservations
            WHERE seat_id = ? AND status IN ({_placeholders(len(OCCUPYING_STATUSES))})
              AND start_at < ? AND end_at > ?
            ORDER BY start_at
            """,
            (seat_id, *sorted(OCCUPYING_STATUSES), fmt_datetime(end), fmt_datetime(start)),
        ).fetchall()
        return [self._reservation(row) for row in rows]

    def list_due_pending(self, now: datetime) -> list[Reservation]:
        rows = self.conn.execute(
            "SELECT * FROM reservations WHERE status='pending' AND checkin_deadline < ? ORDER BY start_at",
            (fmt_datetime(now),),
        ).fetchall()
        return [self._reservation(row) for row in rows]

    def list_due_away(self, now: datetime) -> list[Reservation]:
        rows = self.conn.execute(
            "SELECT * FROM reservations WHERE status='away' AND away_deadline < ? ORDER BY start_at",
            (fmt_datetime(now),),
        ).fetchall()
        return [self._reservation(row) for row in rows]

    def list_due_in_use(self, now: datetime) -> list[Reservation]:
        rows = self.conn.execute(
            """
            SELECT * FROM reservations
            WHERE status IN ('in_use','away') AND end_at < ?
            ORDER BY start_at
            """,
            (fmt_datetime(now),),
        ).fetchall()
        return [self._reservation(row) for row in rows]

    def list_due_reminders(self, now: datetime, before_minutes: int) -> list[Reservation]:
        rows = self.conn.execute(
            """
            SELECT * FROM reservations
            WHERE status='pending' AND start_at > ? AND start_at <= ?
            ORDER BY start_at
            """,
            (
                fmt_datetime(now),
                fmt_datetime(now + timedelta(minutes=before_minutes)),
            ),
        ).fetchall()
        return [self._reservation(row) for row in rows]

    def count_reservations(
        self, *, day: date | None = None, status_in: Sequence[str] | None = None
    ) -> int:
        sql = "SELECT COUNT(*) AS c FROM reservations WHERE 1=1"
        params: list[Any] = []
        if day:
            sql += " AND date = ?"
            params.append(day.isoformat())
        if status_in:
            sql += f" AND status IN ({_placeholders(len(status_in))})"
            params.extend(status_in)
        return int(self.conn.execute(sql, params).fetchone()["c"])

    # ------------------------------------------------------------------
    # 违规 / 信用
    # ------------------------------------------------------------------
    def insert_violation(self, violation: Violation) -> None:
        self.conn.execute(
            """
            INSERT INTO violations(id, reservation_id, user_id, type, points, source, status,
                                   occurred_at, note, operator_id, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                violation.id,
                violation.reservation_id,
                violation.user_id,
                violation.violation_type,
                violation.points,
                violation.source,
                violation.status,
                fmt_datetime(violation.occurred_at),
                violation.note,
                violation.operator_id,
                fmt_datetime(violation.created_at),
            ),
        )

    def update_violation(self, violation: Violation) -> None:
        self.conn.execute(
            """
            UPDATE violations SET status=?, note=?, appeal_reason=?, appeal_at=?,
                   review_note=?, reviewed_by=?, reviewed_at=?
            WHERE id=?
            """,
            (
                violation.status,
                violation.note,
                violation.appeal_reason,
                fmt_datetime(violation.appeal_at),
                violation.review_note,
                violation.reviewed_by,
                fmt_datetime(violation.reviewed_at),
                violation.id,
            ),
        )

    def get_violation(self, violation_id: str) -> Violation | None:
        row = self.conn.execute("SELECT * FROM violations WHERE id = ?", (violation_id,)).fetchone()
        return self._violation(row) if row else None

    def list_violations(
        self,
        *,
        user_id: str | None = None,
        status: str | None = None,
        source: str | None = None,
        violation_type: str | None = None,
        date_from: date | None = None,
        date_to: date | None = None,
        keyword: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> Page:
        where, params = ["1=1"], []
        if user_id:
            where.append("v.user_id = ?")
            params.append(user_id)
        if status:
            where.append("v.status = ?")
            params.append(status)
        if source:
            where.append("v.source = ?")
            params.append(source)
        if violation_type:
            where.append("v.type = ?")
            params.append(violation_type)
        if date_from:
            where.append("v.occurred_at >= ?")
            params.append(fmt_datetime(datetime.combine(date_from, datetime.min.time())))
        if date_to:
            where.append("v.occurred_at <= ?")
            params.append(fmt_datetime(datetime.combine(date_to, datetime.max.time().replace(microsecond=0))))
        if keyword:
            where.append("(v.id LIKE ? OR v.user_id LIKE ? OR u.name LIKE ? OR v.reservation_id LIKE ?)")
            params.extend([f"%{keyword}%"] * 4)
        clause = " AND ".join(where)
        total = self.conn.execute(
            f"SELECT COUNT(*) AS c FROM violations v JOIN users u ON u.id=v.user_id WHERE {clause}",
            params,
        ).fetchone()["c"]
        rows = self.conn.execute(
            f"""
            SELECT v.* FROM violations v JOIN users u ON u.id=v.user_id
            WHERE {clause} ORDER BY v.occurred_at DESC, v.id DESC LIMIT ? OFFSET ?
            """,
            [*params, page_size, (page - 1) * page_size],
        ).fetchall()
        return Page([self._violation(row) for row in rows], total, page, page_size)

    def count_active_violations(self, user_id: str, since: datetime) -> int:
        row = self.conn.execute(
            """
            SELECT COUNT(*) AS c FROM violations
            WHERE user_id = ? AND status IN ('active','appealing') AND occurred_at >= ?
            """,
            (user_id, fmt_datetime(since)),
        ).fetchone()
        return int(row["c"])

    def credit_delta_for_violation(self, violation_id: str) -> int | None:
        """该违规实际写入信用流水的分值；信用触底时可能没有流水（返回 None）。"""
        row = self.conn.execute(
            "SELECT delta FROM credit_logs WHERE violation_id = ? ORDER BY id DESC LIMIT 1",
            (violation_id,),
        ).fetchone()
        return int(row["delta"]) if row else None

    def insert_credit_log(
        self,
        *,
        log_id: str,
        user_id: str,
        delta: int,
        balance_after: int,
        reason: str,
        violation_id: str | None,
        operator_id: str | None,
        created_at: datetime,
    ) -> None:
        self.conn.execute(
            """
            INSERT INTO credit_logs(id, user_id, delta, balance_after, reason, violation_id, operator_id, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (log_id, user_id, delta, balance_after, reason, violation_id, operator_id, fmt_datetime(created_at)),
        )

    def count_rewards(self, user_id: str, day: date) -> int:
        start = datetime.combine(day, datetime.min.time())
        end = start + timedelta(days=1)
        row = self.conn.execute(
            """
            SELECT COUNT(*) AS c FROM credit_logs
            WHERE user_id = ? AND reason = '按时签到签退奖励' AND delta > 0
              AND created_at >= ? AND created_at < ?
            """,
            (user_id, fmt_datetime(start), fmt_datetime(end)),
        ).fetchone()
        return int(row["c"])

    def sum_credit_delta(self, user_id: str, reason: str) -> int:
        row = self.conn.execute(
            "SELECT COALESCE(SUM(delta),0) AS s FROM credit_logs WHERE user_id=? AND reason=?",
            (user_id, reason),
        ).fetchone()
        return int(row["s"])

    def list_credit_logs(self, user_id: str, page: int = 1, page_size: int = 20) -> Page:
        total = self.conn.execute(
            "SELECT COUNT(*) AS c FROM credit_logs WHERE user_id = ?", (user_id,)
        ).fetchone()["c"]
        rows = self.conn.execute(
            "SELECT * FROM credit_logs WHERE user_id = ? ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?",
            (user_id, page_size, (page - 1) * page_size),
        ).fetchall()
        items = [
            {
                "id": row["id"],
                "user_id": row["user_id"],
                "delta": row["delta"],
                "balance_after": row["balance_after"],
                "reason": row["reason"],
                "violation_id": row["violation_id"],
                "operator_id": row["operator_id"],
                "created_at": row["created_at"],
            }
            for row in rows
        ]
        return Page(items, total, page, page_size)

    def violation_breakdown(self, since: datetime) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            """
            SELECT type, COUNT(*) AS count, COALESCE(SUM(points),0) AS points
            FROM violations WHERE occurred_at >= ?
            GROUP BY type ORDER BY count DESC
            """,
            (fmt_datetime(since),),
        ).fetchall()
        return [dict(row) for row in rows]

    # ------------------------------------------------------------------
    # 候补
    # ------------------------------------------------------------------
    def insert_waitlist(self, entry: WaitlistEntry) -> None:
        self.conn.execute(
            """
            INSERT INTO waitlist(id, user_id, seat_id, date, start_slot, end_slot, status, seq,
                                 created_at, notified_at, confirm_deadline, defer_count)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                entry.id,
                entry.user_id,
                entry.seat_id,
                entry.day.isoformat(),
                entry.start_slot,
                entry.end_slot,
                entry.status,
                entry.seq,
                fmt_datetime(entry.created_at),
                fmt_datetime(entry.notified_at),
                fmt_datetime(entry.confirm_deadline),
                entry.defer_count,
            ),
        )

    def update_waitlist(self, entry: WaitlistEntry) -> None:
        self.conn.execute(
            """
            UPDATE waitlist SET status=?, notified_at=?, confirm_deadline=?, defer_count=?
            WHERE id=?
            """,
            (
                entry.status,
                fmt_datetime(entry.notified_at),
                fmt_datetime(entry.confirm_deadline),
                entry.defer_count,
                entry.id,
            ),
        )

    def get_waitlist(self, entry_id: str) -> WaitlistEntry | None:
        row = self.conn.execute("SELECT * FROM waitlist WHERE id = ?", (entry_id,)).fetchone()
        return self._waitlist(row) if row else None

    def find_waitlist(
        self, user_id: str, seat_id: str, day: date, start_slot: int, end_slot: int
    ) -> WaitlistEntry | None:
        row = self.conn.execute(
            """
            SELECT * FROM waitlist
            WHERE user_id=? AND seat_id=? AND date=? AND start_slot=? AND end_slot=?
            ORDER BY created_at DESC LIMIT 1
            """,
            (user_id, seat_id, day.isoformat(), start_slot, end_slot),
        ).fetchone()
        return self._waitlist(row) if row else None

    def waitlist_candidates(
        self, seat_id: str, day: date, start_slot: int, end_slot: int, limit: int, max_defer: int
    ) -> list[WaitlistEntry]:
        rows = self.conn.execute(
            """
            SELECT * FROM waitlist
            WHERE seat_id=? AND date=? AND start_slot=? AND end_slot=?
              AND status='queued' AND defer_count <= ?
            ORDER BY seq LIMIT ?
            """,
            (seat_id, day.isoformat(), start_slot, end_slot, max_defer, limit),
        ).fetchall()
        return [self._waitlist(row) for row in rows]

    def count_waitlist_queue(self, seat_id: str, day: date, start_slot: int, end_slot: int) -> int:
        row = self.conn.execute(
            """
            SELECT COUNT(*) AS c FROM waitlist
            WHERE seat_id=? AND date=? AND start_slot=? AND end_slot=?
              AND status IN ('queued','notified')
            """,
            (seat_id, day.isoformat(), start_slot, end_slot),
        ).fetchone()
        return int(row["c"])

    def list_waitlist(
        self,
        *,
        user_id: str | None = None,
        status: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> Page:
        where, params = ["1=1"], []
        if user_id:
            where.append("w.user_id = ?")
            params.append(user_id)
        if status:
            where.append("w.status = ?")
            params.append(status)
        clause = " AND ".join(where)
        total = self.conn.execute(
            f"SELECT COUNT(*) AS c FROM waitlist w WHERE {clause}", params
        ).fetchone()["c"]
        rows = self.conn.execute(
            f"""
            SELECT w.*, s.room_id AS room_id, r.name AS room_name FROM waitlist w
            JOIN seats s ON s.id=w.seat_id JOIN rooms r ON r.id=s.room_id
            WHERE {clause} ORDER BY w.created_at DESC LIMIT ? OFFSET ?
            """,
            [*params, page_size, (page - 1) * page_size],
        ).fetchall()
        items = []
        for row in rows:
            item = self._waitlist(row).as_dict()
            item.update({"room_id": row["room_id"], "room_name": row["room_name"]})
            items.append(item)
        return Page(items, total, page, page_size)

    def due_waitlist(self, now: datetime) -> list[WaitlistEntry]:
        rows = self.conn.execute(
            "SELECT * FROM waitlist WHERE status='notified' AND confirm_deadline < ?",
            (fmt_datetime(now),),
        ).fetchall()
        return [self._waitlist(row) for row in rows]

    # ------------------------------------------------------------------
    # 通知
    # ------------------------------------------------------------------
    def insert_notification(self, notification: Notification) -> None:
        self.conn.execute(
            """
            INSERT OR IGNORE INTO notifications(id, user_id, type, content, channel, status, created_at, read_at, dedupe_key)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                notification.id,
                notification.user_id,
                notification.notification_type,
                notification.content,
                notification.channel,
                notification.status,
                fmt_datetime(notification.created_at),
                fmt_datetime(notification.read_at),
                notification.dedupe_key,
            ),
        )

    def list_notifications(self, user_id: str, page: int = 1, page_size: int = 20) -> Page:
        total = self.conn.execute(
            "SELECT COUNT(*) AS c FROM notifications WHERE user_id = ?", (user_id,)
        ).fetchone()["c"]
        rows = self.conn.execute(
            "SELECT * FROM notifications WHERE user_id = ? ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (user_id, page_size, (page - 1) * page_size),
        ).fetchall()
        items = [
            Notification(
                id=row["id"],
                user_id=row["user_id"],
                notification_type=row["type"],
                content=row["content"],
                channel=row["channel"],
                status=row["status"],
                created_at=_dt(row["created_at"]),
                read_at=_dt(row["read_at"]),
                dedupe_key=row["dedupe_key"],
            )
            for row in rows
        ]
        return Page(items, total, page, page_size)

    def unread_notification_count(self, user_id: str) -> int:
        row = self.conn.execute(
            "SELECT COUNT(*) AS c FROM notifications WHERE user_id=? AND read_at IS NULL",
            (user_id,),
        ).fetchone()
        return int(row["c"])

    def mark_notifications_read(
        self, user_id: str, ids: Sequence[str] | None, at: datetime
    ) -> int:
        if ids:
            cursor = self.conn.execute(
                f"UPDATE notifications SET read_at=?, status='read' WHERE user_id=? AND read_at IS NULL AND id IN ({_placeholders(len(ids))})",
                (fmt_datetime(at), user_id, *ids),
            )
        else:
            cursor = self.conn.execute(
                "UPDATE notifications SET read_at=?, status='read' WHERE user_id=? AND read_at IS NULL",
                (fmt_datetime(at), user_id),
            )
        return cursor.rowcount or 0

    def has_notification(self, user_id: str, dedupe_key: str) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM notifications WHERE user_id=? AND dedupe_key=? LIMIT 1",
            (user_id, dedupe_key),
        ).fetchone()
        return row is not None

    # ------------------------------------------------------------------
    # 投诉 / 评价
    # ------------------------------------------------------------------
    def insert_complaint(self, complaint: Complaint) -> None:
        self.conn.execute(
            """
            INSERT INTO complaints(id, user_id, seat_id, reservation_id, type, content, status,
                                   feedback, resolved_by, resolved_at, rating, rating_comment, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                complaint.id,
                complaint.user_id,
                complaint.seat_id,
                complaint.reservation_id,
                complaint.complaint_type,
                complaint.content,
                complaint.status,
                complaint.feedback,
                complaint.resolved_by,
                fmt_datetime(complaint.resolved_at),
                complaint.rating,
                complaint.rating_comment,
                fmt_datetime(complaint.created_at),
            ),
        )

    def update_complaint(self, complaint: Complaint) -> None:
        self.conn.execute(
            """
            UPDATE complaints SET status=?, feedback=?, resolved_by=?, resolved_at=?,
                   rating=?, rating_comment=? WHERE id=?
            """,
            (
                complaint.status,
                complaint.feedback,
                complaint.resolved_by,
                fmt_datetime(complaint.resolved_at),
                complaint.rating,
                complaint.rating_comment,
                complaint.id,
            ),
        )

    def get_complaint(self, complaint_id: str) -> Complaint | None:
        row = self.conn.execute("SELECT * FROM complaints WHERE id = ?", (complaint_id,)).fetchone()
        return self._complaint(row) if row else None

    def list_complaints(
        self,
        *,
        user_id: str | None = None,
        status: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> Page:
        where, params = ["1=1"], []
        if user_id:
            where.append("c.user_id = ?")
            params.append(user_id)
        if status:
            where.append("c.status = ?")
            params.append(status)
        clause = " AND ".join(where)
        total = self.conn.execute(
            f"SELECT COUNT(*) AS c FROM complaints c WHERE {clause}", params
        ).fetchone()["c"]
        rows = self.conn.execute(
            f"""
            SELECT c.*, u.name AS user_name FROM complaints c JOIN users u ON u.id=c.user_id
            WHERE {clause} ORDER BY c.created_at DESC LIMIT ? OFFSET ?
            """,
            [*params, page_size, (page - 1) * page_size],
        ).fetchall()
        items = []
        for row in rows:
            data = self._complaint(row).as_dict()
            data["user_name"] = row["user_name"]
            items.append(data)
        return Page(items, total, page, page_size)

    def insert_review(self, review: Review) -> None:
        self.conn.execute(
            """
            INSERT INTO reviews(id, reservation_id, user_id, seat_id, rating, tags_json, content,
                                reply, replied_by, replied_at, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                review.id,
                review.reservation_id,
                review.user_id,
                review.seat_id,
                review.rating,
                json.dumps(review.tags, ensure_ascii=False),
                review.content,
                review.reply,
                review.replied_by,
                fmt_datetime(review.replied_at),
                fmt_datetime(review.created_at),
            ),
        )

    def update_review(self, review: Review) -> None:
        self.conn.execute(
            "UPDATE reviews SET reply=?, replied_by=?, replied_at=? WHERE id=?",
            (
                review.reply,
                review.replied_by,
                fmt_datetime(review.replied_at),
                review.id,
            ),
        )

    def get_review(self, review_id: str) -> Review | None:
        row = self.conn.execute("SELECT * FROM reviews WHERE id = ?", (review_id,)).fetchone()
        return self._review(row) if row else None

    def review_of_reservation(self, reservation_id: str) -> Review | None:
        row = self.conn.execute(
            "SELECT * FROM reviews WHERE reservation_id = ?", (reservation_id,)
        ).fetchone()
        return self._review(row) if row else None

    def list_reviews(
        self,
        *,
        seat_id: str | None = None,
        user_id: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> Page:
        where, params = ["1=1"], []
        if seat_id:
            where.append("rv.seat_id = ?")
            params.append(seat_id)
        if user_id:
            where.append("rv.user_id = ?")
            params.append(user_id)
        clause = " AND ".join(where)
        total = self.conn.execute(
            f"SELECT COUNT(*) AS c FROM reviews rv WHERE {clause}", params
        ).fetchone()["c"]
        rows = self.conn.execute(
            f"""
            SELECT rv.*, s.room_id AS room_id, rm.name AS room_name
            FROM reviews rv JOIN seats s ON s.id=rv.seat_id JOIN rooms rm ON rm.id=s.room_id
            WHERE {clause} ORDER BY rv.created_at DESC LIMIT ? OFFSET ?
            """,
            [*params, page_size, (page - 1) * page_size],
        ).fetchall()
        items = []
        for row in rows:
            data = self._review(row).as_dict()
            data.update({"room_id": row["room_id"], "room_name": row["room_name"]})
            items.append(data)
        return Page(items, total, page, page_size)

    def average_rating(self, room_id: str | None = None) -> float | None:
        sql = "SELECT AVG(rv.rating) AS a FROM reviews rv JOIN seats s ON s.id=rv.seat_id WHERE 1=1"
        params: list[Any] = []
        if room_id:
            sql += " AND s.room_id = ?"
            params.append(room_id)
        row = self.conn.execute(sql, params).fetchone()
        return round(float(row["a"]), 2) if row and row["a"] is not None else None

    # ------------------------------------------------------------------
    # 黑名单
    # ------------------------------------------------------------------
    def insert_blacklist(self, record: BlacklistRecord) -> None:
        self.conn.execute(
            """
            INSERT INTO blacklist(id, user_id, reason, source, start_at, end_at, released_at,
                                  released_by, created_by, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record.id,
                record.user_id,
                record.reason,
                record.source,
                fmt_datetime(record.start_at),
                fmt_datetime(record.end_at),
                fmt_datetime(record.released_at),
                record.released_by,
                record.created_by,
                fmt_datetime(record.created_at),
            ),
        )

    def update_blacklist(self, record: BlacklistRecord) -> None:
        self.conn.execute(
            "UPDATE blacklist SET released_at=?, released_by=? WHERE id=?",
            (fmt_datetime(record.released_at), record.released_by, record.id),
        )

    def active_blacklist(self, user_id: str, now: datetime) -> BlacklistRecord | None:
        row = self.conn.execute(
            """
            SELECT * FROM blacklist
            WHERE user_id=? AND released_at IS NULL AND start_at <= ? AND end_at > ?
            ORDER BY end_at DESC LIMIT 1
            """,
            (user_id, fmt_datetime(now), fmt_datetime(now)),
        ).fetchone()
        if not row:
            return None
        return BlacklistRecord(
            id=row["id"],
            user_id=row["user_id"],
            reason=row["reason"],
            source=row["source"],
            start_at=_dt(row["start_at"]) or datetime.min,
            end_at=_dt(row["end_at"]) or datetime.min,
            released_at=_dt(row["released_at"]),
            released_by=row["released_by"],
            created_by=row["created_by"],
            created_at=_dt(row["created_at"]),
        )

    def list_blacklist(
        self,
        *,
        active_only: bool = False,
        page: int = 1,
        page_size: int = 20,
        now: datetime | None = None,
    ) -> Page:
        where, params = ["1=1"], []
        if active_only and now:
            where.append("b.released_at IS NULL AND b.start_at <= ? AND b.end_at > ?")
            params.extend([fmt_datetime(now), fmt_datetime(now)])
        clause = " AND ".join(where)
        total = self.conn.execute(
            f"SELECT COUNT(*) AS c FROM blacklist b WHERE {clause}", params
        ).fetchone()["c"]
        rows = self.conn.execute(
            f"""
            SELECT b.*, u.name AS user_name FROM blacklist b JOIN users u ON u.id=b.user_id
            WHERE {clause} ORDER BY b.created_at DESC LIMIT ? OFFSET ?
            """,
            [*params, page_size, (page - 1) * page_size],
        ).fetchall()
        items = []
        for row in rows:
            data = BlacklistRecord(
                id=row["id"],
                user_id=row["user_id"],
                reason=row["reason"],
                source=row["source"],
                start_at=_dt(row["start_at"]) or datetime.min,
                end_at=_dt(row["end_at"]) or datetime.min,
                released_at=_dt(row["released_at"]),
                released_by=row["released_by"],
                created_by=row["created_by"],
                created_at=_dt(row["created_at"]),
            ).as_dict()
            data["user_name"] = row["user_name"]
            items.append(data)
        return Page(items, total, page, page_size)

    # ------------------------------------------------------------------
    # 审计 / 参数 / 序列
    # ------------------------------------------------------------------
    def insert_audit(self, log: AuditLog) -> None:
        self.conn.execute(
            """
            INSERT INTO audit_logs(id, actor_id, actor_role, action, target_type, target_id,
                                   before_json, after_json, ip, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                log.id,
                log.actor_id,
                log.actor_role,
                log.action,
                log.target_type,
                log.target_id,
                json.dumps(log.before, ensure_ascii=False) if log.before is not None else None,
                json.dumps(log.after, ensure_ascii=False) if log.after is not None else None,
                log.ip,
                fmt_datetime(log.created_at),
            ),
        )

    def list_audit(
        self,
        *,
        actor_id: str | None = None,
        action: str | None = None,
        target_type: str | None = None,
        date_from: date | None = None,
        date_to: date | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> Page:
        where, params = ["1=1"], []
        if actor_id:
            where.append("actor_id = ?")
            params.append(actor_id)
        if action:
            where.append("action = ?")
            params.append(action)
        if target_type:
            where.append("target_type = ?")
            params.append(target_type)
        if date_from:
            where.append("created_at >= ?")
            params.append(fmt_datetime(datetime.combine(date_from, datetime.min.time())))
        if date_to:
            where.append("created_at <= ?")
            params.append(fmt_datetime(datetime.combine(date_to, datetime.max.time().replace(microsecond=0))))
        clause = " AND ".join(where)
        total = self.conn.execute(
            f"SELECT COUNT(*) AS c FROM audit_logs WHERE {clause}", params
        ).fetchone()["c"]
        rows = self.conn.execute(
            f"SELECT * FROM audit_logs WHERE {clause} ORDER BY created_at DESC LIMIT ? OFFSET ?",
            [*params, page_size, (page - 1) * page_size],
        ).fetchall()
        items = [
            AuditLog(
                id=row["id"],
                actor_id=row["actor_id"],
                actor_role=row["actor_role"],
                action=row["action"],
                target_type=row["target_type"],
                target_id=row["target_id"],
                before=_json(row["before_json"]),
                after=_json(row["after_json"]),
                ip=row["ip"],
                created_at=_dt(row["created_at"]),
            )
            for row in rows
        ]
        return Page(items, total, page, page_size)

    def load_params(self) -> ParamSet:
        rows = self.conn.execute("SELECT code, value FROM system_params").fetchall()
        return ParamSet({row["code"]: row["value"] for row in rows})

    def save_params(self, values: dict[str, str], actor_id: str, now: datetime) -> None:
        for code, value in values.items():
            self.conn.execute(
                """
                INSERT INTO system_params(code, value, updated_at, updated_by) VALUES (?, ?, ?, ?)
                ON CONFLICT(code) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at,
                                               updated_by=excluded.updated_by
                """,
                (code, value, fmt_datetime(now), actor_id),
            )

    def next_id(self, sequence: str, prefix: str, width: int) -> str:
        row = self.conn.execute("SELECT value FROM sequences WHERE name = ?", (sequence,)).fetchone()
        if row is None:
            value = 1
            self.conn.execute("INSERT INTO sequences(name, value) VALUES (?, ?)", (sequence, value))
        else:
            value = int(row["value"]) + 1
            self.conn.execute("UPDATE sequences SET value = ? WHERE name = ?", (value, sequence))
        return f"{prefix}{value:0{width}d}"

    def next_reservation_id(self, day: date) -> str:
        stamp = day.strftime("%Y%m%d")
        return self.next_id(f"reservation:{stamp}", f"R{stamp}-", 4)

    # ------------------------------------------------------------------
    # 统计
    # ------------------------------------------------------------------
    def seat_state_counts(
        self, day: date, start_slot: int, end_slot: int, room_id: str | None = None
    ) -> dict[str, int]:
        counts = {"available": 0, "reserved": 0, "occupied": 0, "held": 0, "maintenance": 0, "disabled": 0}
        for view in self.seat_views(day, start_slot, end_slot, room_id=room_id):
            counts[view.state] = counts.get(view.state, 0) + 1
        return counts

    def admin_stats(self, now: datetime, room_id: str | None = None) -> dict[str, Any]:
        today = now.date()
        slot = min(max(now.hour * 2 + (1 if now.minute >= 30 else 0), 0), 47)
        counts = self.seat_state_counts(today, slot, min(slot + 1, 48), room_id)
        total_seats = sum(counts.values())
        busy = counts["occupied"] + counts["held"]
        utilization = round(busy / total_seats * 100, 1) if total_seats else 0.0

        room_clause = " AND s.room_id = ?" if room_id else ""
        room_params: list[Any] = [room_id] if room_id else []
        today_count = self.conn.execute(
            f"""
            SELECT COUNT(*) AS c FROM reservations r JOIN seats s ON s.id=r.seat_id
            WHERE r.date = ? AND r.status != 'cancelled'{room_clause}
            """,
            [today.isoformat(), *room_params],
        ).fetchone()["c"]
        checkin_count = self.conn.execute(
            f"""
            SELECT COUNT(*) AS c FROM reservations r JOIN seats s ON s.id=r.seat_id
            WHERE r.date = ? AND r.checked_in_at IS NOT NULL{room_clause}
            """,
            [today.isoformat(), *room_params],
        ).fetchone()["c"]
        pending_count = self.conn.execute(
            f"""
            SELECT COUNT(*) AS c FROM reservations r JOIN seats s ON s.id=r.seat_id
            WHERE r.date = ? AND r.status='pending'{room_clause}
            """,
            [today.isoformat(), *room_params],
        ).fetchone()["c"]
        open_complaints = self.conn.execute(
            "SELECT COUNT(*) AS c FROM complaints WHERE status='pending'"
        ).fetchone()["c"]
        waitlist_waiting = self.conn.execute(
            "SELECT COUNT(*) AS c FROM waitlist WHERE status IN ('queued','notified')"
        ).fetchone()["c"]
        avg_rating = self.average_rating(room_id)

        since = now - timedelta(days=30)
        breakdown = self.violation_breakdown(since)

        trend = []
        for offset in range(6, -1, -1):
            day = today - timedelta(days=offset)
            row = self.conn.execute(
                f"""
                SELECT
                    COUNT(*) AS reservations,
                    SUM(CASE WHEN r.status='completed' THEN 1 ELSE 0 END) AS completed,
                    SUM(CASE WHEN r.status='no_show' THEN 1 ELSE 0 END) AS no_show,
                    SUM(CASE WHEN r.checked_in_at IS NOT NULL THEN 1 ELSE 0 END) AS checkins
                FROM reservations r JOIN seats s ON s.id=r.seat_id
                WHERE r.date = ?{room_clause}
                """,
                [day.isoformat(), *room_params],
            ).fetchone()
            trend.append(
                {
                    "date": day.isoformat(),
                    "label": day.strftime("%m-%d"),
                    "reservations": int(row["reservations"] or 0),
                    "completed": int(row["completed"] or 0),
                    "no_show": int(row["no_show"] or 0),
                    "checkins": int(row["checkins"] or 0),
                }
            )

        rooms = []
        for room in self.list_rooms():
            room_counts = self.seat_state_counts(today, slot, min(slot + 1, 48), room.id)
            total = sum(room_counts.values())
            rooms.append(
                {
                    "id": room.id,
                    "name": room.name,
                    "total": total,
                    "available": room_counts["available"],
                    "occupied": room_counts["occupied"] + room_counts["held"],
                    "maintenance": room_counts["maintenance"] + room_counts["disabled"],
                    "utilization": round(
                        (room_counts["occupied"] + room_counts["held"]) / total * 100, 1
                    )
                    if total
                    else 0.0,
                }
            )

        hourly_rows = self.conn.execute(
            f"""
            SELECT r.start_slot AS slot, COUNT(*) AS c
            FROM reservations r JOIN seats s ON s.id=r.seat_id
            WHERE r.date = ? AND r.status != 'cancelled'{room_clause}
            GROUP BY r.start_slot ORDER BY r.start_slot
            """,
            [today.isoformat(), *room_params],
        ).fetchall()
        hourly = [{"hour": hhmm(int(row["slot"])), "count": int(row["c"])} for row in hourly_rows]

        return {
            "generated_at": fmt_datetime(now),
            "date": today.isoformat(),
            "kpi": {
                "total_seats": total_seats,
                "available": counts["available"],
                "occupied": counts["occupied"],
                "held": counts["held"],
                "reserved": counts["reserved"],
                "maintenance": counts["maintenance"] + counts["disabled"],
                "utilization": utilization,
                "today_reservations": int(today_count),
                "today_checkins": int(checkin_count),
                "pending": int(pending_count),
                "open_complaints": int(open_complaints),
                "waitlist": int(waitlist_waiting),
                "average_rating": avg_rating,
            },
            "seat_states": [
                {"state": key, "label": SEAT_STATE_LABELS.get(key, key), "count": value}
                for key, value in counts.items()
            ],
            "trend": trend,
            "violations": breakdown,
            "rooms": rooms,
            "hourly": hourly,
        }

    def teacher_stats(self, class_name: str, now: datetime) -> dict[str, Any]:
        today = now.date()
        month_start = today.replace(day=1)
        since = now - timedelta(days=30)
        row = self.conn.execute(
            """
            SELECT
                (SELECT COUNT(*) FROM users WHERE role='student' AND class_name=?) AS students,
                (SELECT COUNT(*) FROM reservations r JOIN users u ON u.id=r.user_id
                    WHERE u.class_name=? AND r.date=?) AS today_reservations,
                (SELECT COUNT(*) FROM reservations r JOIN users u ON u.id=r.user_id
                    WHERE u.class_name=? AND r.date>=? AND r.status='completed') AS month_completed,
                (SELECT COUNT(*) FROM violations v JOIN users u ON u.id=v.user_id
                    WHERE u.class_name=? AND v.occurred_at>=?) AS violations_30,
                (SELECT COUNT(*) FROM reservations r JOIN users u ON u.id=r.user_id
                    WHERE u.class_name=? AND r.date>=? AND r.status IN ('completed','no_show')) AS settled,
                (SELECT COUNT(*) FROM reservations r JOIN users u ON u.id=r.user_id
                    WHERE u.class_name=? AND r.date>=? AND r.status='completed') AS completed_30,
                (SELECT COUNT(*) FROM reservations r JOIN users u ON u.id=r.user_id
                    WHERE u.class_name=? AND r.date>=? AND r.status='no_show') AS no_show_30,
                (SELECT COUNT(*) FROM reservations r JOIN users u ON u.id=r.user_id
                    WHERE u.class_name=? AND r.date=? AND r.checked_in_at IS NOT NULL) AS today_checkins
            """,
            (
                class_name,
                class_name,
                today.isoformat(),
                class_name,
                month_start.isoformat(),
                class_name,
                fmt_datetime(since),
                class_name,
                month_start.isoformat(),
                class_name,
                month_start.isoformat(),
                class_name,
                month_start.isoformat(),
                class_name,
                today.isoformat(),
            ),
        ).fetchone()
        settled = int(row["settled"] or 0)
        attendance = round(int(row["completed_30"] or 0) / settled * 100, 1) if settled else None

        trend = []
        for offset in range(6, -1, -1):
            day = today - timedelta(days=offset)
            item = self.conn.execute(
                """
                SELECT COUNT(*) AS reservations,
                       SUM(CASE WHEN r.status='completed' THEN 1 ELSE 0 END) AS completed
                FROM reservations r JOIN users u ON u.id=r.user_id
                WHERE u.class_name=? AND r.date=?
                """,
                (class_name, day.isoformat()),
            ).fetchone()
            trend.append(
                {
                    "date": day.isoformat(),
                    "label": day.strftime("%m-%d"),
                    "reservations": int(item["reservations"] or 0),
                    "completed": int(item["completed"] or 0),
                }
            )

        return {
            "class_name": class_name,
            "generated_at": fmt_datetime(now),
            "kpi": {
                "students": int(row["students"] or 0),
                "today_reservations": int(row["today_reservations"] or 0),
                "today_checkins": int(row["today_checkins"] or 0),
                "month_completed": int(row["month_completed"] or 0),
                "violations_30": int(row["violations_30"] or 0),
                "attendance_rate": attendance,
            },
            "trend": trend,
        }

    def student_stats(self, user_id: str, now: datetime) -> dict[str, Any]:
        today = now.date()
        month_start = today.replace(day=1).isoformat()
        row = self.conn.execute(
            """
            SELECT
                (SELECT COUNT(*) FROM reservations WHERE user_id=? AND date>=? ) AS month_total,
                (SELECT COUNT(*) FROM reservations WHERE user_id=? AND date>=? AND status='completed') AS month_completed,
                (SELECT COUNT(*) FROM reservations WHERE user_id=? AND date>=? AND status='no_show') AS month_no_show,
                (SELECT COUNT(*) FROM reservations WHERE user_id=? AND status='pending' AND start_at>?) AS upcoming,
                (SELECT COUNT(*) FROM waitlist WHERE user_id=? AND status IN ('queued','notified')) AS waitlist,
                (SELECT COALESCE(SUM(delta),0) FROM credit_logs WHERE user_id=? AND created_at>=?) AS month_credit_delta,
                (SELECT COUNT(*) FROM violations WHERE user_id=? AND status IN ('active','appealing')) AS active_violations
            """,
            (
                user_id,
                month_start,
                user_id,
                month_start,
                user_id,
                month_start,
                user_id,
                fmt_datetime(now),
                user_id,
                user_id,
                month_start,
                user_id,
            ),
        ).fetchone()
        return {
            "month_total": int(row["month_total"] or 0),
            "month_completed": int(row["month_completed"] or 0),
            "month_no_show": int(row["month_no_show"] or 0),
            "upcoming": int(row["upcoming"] or 0),
            "waitlist": int(row["waitlist"] or 0),
            "month_credit_delta": int(row["month_credit_delta"] or 0),
            "active_violations": int(row["active_violations"] or 0),
        }

    def today_active_seat_ids(self, day: date, now: datetime) -> set[str]:
        rows = self.conn.execute(
            """
            SELECT DISTINCT seat_id FROM reservations
            WHERE date=? AND status IN ('in_use','away')
            """,
            (day.isoformat(),),
        ).fetchall()
        return {row["seat_id"] for row in rows}
