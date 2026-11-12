"""测试辅助：固定时钟、直接建库种子、事务级调用封装。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from seat_reservation.domain.models import (
    Actor,
    Reservation,
    ReservationStatus,
    Role,
    Violation,
    ViolationStatus,
    ViolationType,
)
from seat_reservation.domain.params import ParamSet
from seat_reservation.domain.timeutil import datetime_at, hhmm
from seat_reservation.security import hash_password
from seat_reservation.service.container import AppContext
from seat_reservation.storage.repository import SqliteRepository

NOW = datetime(2026, 5, 11, 8, 0, 0)
D = NOW.date()
PASSWORD = "Passw0rd!23"


def seed_fixture(ctx: AppContext, now: datetime = NOW) -> None:
    params = ParamSet()
    with ctx.db.transaction() as conn:
        repo = SqliteRepository(conn)
        repo.save_params(params.snapshot(), "SYSTEM", now)
        encoded = hash_password(PASSWORD, iterations=1000)
        users = [
            ("S1", "张伟", Role.STUDENT, "软件 2301", 100),
            ("S2", "李娜", Role.STUDENT, "软件 2301", 100),
            ("S3", "王强", Role.STUDENT, "软件 2302", 100),
            ("S4", "赵敏", Role.STUDENT, "软件 2301", 55),
            ("S5", "李华", Role.STUDENT, "软件 2302", 100),
            ("T1", "刘明", Role.TEACHER, "软件 2301", 100),
            ("A1", "王丽", Role.ROOM_ADMIN, "", 100),
            ("Y1", "陈刚", Role.SYS_ADMIN, "", 100),
        ]
        for user_id, name, role, class_name, credit in users:
            from seat_reservation.domain.models import User

            repo.save_user(
                User(
                    id=user_id,
                    name=name,
                    role=role.value,
                    department="计算机学院",
                    class_name=class_name,
                    credit=credit,
                    password_hash=encoded,
                    created_at=now,
                    updated_at=now,
                )
            )
        from seat_reservation.domain.models import Room, Seat, SeatType

        for room_id, name in (("R1", "A101"), ("R2", "B201")):
            repo.save_room(
                Room(
                    id=room_id,
                    name=name,
                    location="教学楼",
                    open_slot=14,
                    close_slot=46,
                    created_at=now,
                )
            )
        for room_id, count in (("R1", 6), ("R2", 2)):
            for index in range(1, count + 1):
                repo.save_seat(
                    Seat(
                        id=f"{room_id}-{index:03d}",
                        room_id=room_id,
                        seat_type=SeatType.STANDARD.value,
                        has_power=index % 2 == 0,
                        is_window=index == 1,
                        created_at=now,
                    )
                )

        # 赵敏：2 天前未签到，信用分 55，处于 BR-07 限制
        old_start = datetime_at(D - timedelta(days=2), 18)  # 09:00
        reservation = Reservation(
            id=f"R{(D - timedelta(days=2)):%Y%m%d}-9001",
            user_id="S4",
            seat_id="R1-006",
            day=D - timedelta(days=2),
            start_slot=18,
            end_slot=20,
            start_at=old_start,
            end_at=old_start + timedelta(hours=1),
            status=ReservationStatus.NO_SHOW.value,
            created_at=old_start - timedelta(days=2),
            updated_at=old_start + timedelta(minutes=16),
            checkin_deadline=old_start + timedelta(minutes=15),
            checked_out_at=old_start + timedelta(minutes=16),
            end_reason="no_show",
            policy=params.snapshot(),
        )
        repo.insert_reservation(reservation)
        repo.insert_violation(
            Violation(
                id=repo.next_id("violation", "VIO-", 6),
                reservation_id=reservation.id,
                user_id="S4",
                violation_type=ViolationType.NO_SHOW.value,
                points=10,
                status=ViolationStatus.ACTIVE.value,
                occurred_at=old_start + timedelta(minutes=16),
                note="超过签到窗口未签到",
                created_at=old_start + timedelta(minutes=16),
            )
        )
        repo.insert_credit_log(
            log_id=repo.next_id("credit", "CL-", 7),
            user_id="S4",
            delta=-45,
            balance_after=55,
            reason="历史信用记录",
            violation_id=None,
            operator_id=None,
            created_at=now - timedelta(days=30),
        )
        user = repo.get_user("S4")
        if user:
            user.limit_type = "credit"
            user.updated_at = now
            repo.save_user(user)


@dataclass
class Env:
    ctx: AppContext

    # -- 基础 -----------------------------------------------------------
    def actor(self, user_id: str) -> Actor:
        with self.ctx.db.read() as conn:
            user = SqliteRepository(conn).get_user(user_id)
        assert user is not None, user_id
        return Actor(
            id=user.id,
            name=user.name,
            role=Role(user.role),
            class_name=user.class_name,
            department=user.department,
        )

    def user(self, user_id: str):
        with self.ctx.db.read() as conn:
            return SqliteRepository(conn).get_user(user_id)

    def reservation(self, reservation_id: str) -> Reservation:
        with self.ctx.db.read() as conn:
            item = SqliteRepository(conn).get_reservation(reservation_id)
        assert item is not None
        return item

    # -- 领域动作 -------------------------------------------------------
    def create(self, user_id: str, seat_id: str, start: time, end: time, *, now: datetime = NOW, day: date = D):
        with self.ctx.engine() as engine:
            return engine.create_reservation(self.actor(user_id), seat_id, day, start, end, now)

    def credential(self, user_id: str, reservation_id: str, *, now: datetime = NOW):
        with self.ctx.engine() as engine:
            return engine.issue_credential(self.actor(user_id), reservation_id, now)

    def check_in(self, user_id: str, reservation_id: str, code: str | None = None, *, now: datetime = NOW):
        with self.ctx.engine() as engine:
            return engine.check_in(self.actor(user_id), reservation_id, code, now)

    def away(self, user_id: str, reservation_id: str, *, now: datetime = NOW):
        with self.ctx.engine() as engine:
            return engine.start_away(self.actor(user_id), reservation_id, now)

    def resume(self, user_id: str, reservation_id: str, *, now: datetime = NOW):
        with self.ctx.engine() as engine:
            return engine.resume_use(self.actor(user_id), reservation_id, now)

    def check_out(self, user_id: str, reservation_id: str, *, now: datetime = NOW):
        with self.ctx.engine() as engine:
            return engine.check_out(self.actor(user_id), reservation_id, now)

    def cancel(self, user_id: str, reservation_id: str, *, now: datetime = NOW):
        with self.ctx.engine() as engine:
            return engine.cancel_reservation(self.actor(user_id), reservation_id, now)

    def sweep(self, *, now: datetime = NOW):
        with self.ctx.engine() as engine:
            return engine.run_timeout_sweep(now)

    def join_waitlist(self, user_id: str, seat_id: str, start: time, end: time, *, now: datetime = NOW):
        with self.ctx.engine() as engine:
            return engine.join_waitlist(self.actor(user_id), seat_id, D, start, end, now)

    def confirm_waitlist(self, user_id: str, entry_id: str, *, now: datetime = NOW):
        with self.ctx.engine() as engine:
            return engine.confirm_waitlist(self.actor(user_id), entry_id, now)

    def block_seat(self, user_id: str, seat_id: str, reason: str, start: datetime, end: datetime, *, now: datetime = NOW):
        with self.ctx.engine() as engine:
            return engine.block_seat(self.actor(user_id), seat_id, reason, start, end, now)

    def force_release(self, user_id: str, reservation_id: str, reason: str, *, now: datetime = NOW):
        with self.ctx.engine() as engine:
            return engine.force_release(self.actor(user_id), reservation_id, reason, now)

    def appeal(self, user_id: str, violation_id: str, reason: str, *, now: datetime = NOW):
        with self.ctx.engine() as engine:
            return engine.submit_appeal(self.actor(user_id), violation_id, reason, now)

    def review_appeal(self, user_id: str, violation_id: str, decision: str, note: str = "已核查", *, now: datetime = NOW):
        with self.ctx.engine() as engine:
            return engine.review_appeal(self.actor(user_id), violation_id, decision, note, now)

    def set_param(self, user_id: str, code: str, value, *, now: datetime = NOW):
        with self.ctx.engine() as engine:
            return engine.set_param(self.actor(user_id), code, str(value), now)

    # -- 读模型 ---------------------------------------------------------
    def seats(self, start: time, end: time, *, user_id: str = "S1", day: date = D):
        with self.ctx.engine(write=False) as engine:
            rows = engine.list_seats(self.actor(user_id), day, start, end)
        return {row["id"]: row for row in rows}

    def seat_state(self, seat_id: str, start: time, end: time, *, day: date = D) -> str:
        return self.seats(start, end, day=day)[seat_id]["state"]

    def violations(self, user_id: str):
        with self.ctx.engine(write=False) as engine:
            page = engine.list_violations(self.actor(user_id), page_size=100)
        return list(page.items)

    def notifications(self, user_id: str):
        with self.ctx.engine(write=False) as engine:
            page = engine.list_notifications(self.actor(user_id), page_size=100)
        return list(page.items)

    def audit(self):
        with self.ctx.engine(write=False) as engine:
            page = engine.list_audit(self.actor("Y1"), page_size=100)
        return list(page.items)

    def count_reservations(self) -> int:
        with self.ctx.db.read() as conn:
            return int(conn.execute("SELECT COUNT(*) AS c FROM reservations").fetchone()["c"])


def make_credential(env: Env, user_id: str, reservation_id: str, *, now: datetime = NOW) -> str:
    result = env.credential(user_id, reservation_id, now=now)
    assert result.ok, result.message
    return result.data["code"]
