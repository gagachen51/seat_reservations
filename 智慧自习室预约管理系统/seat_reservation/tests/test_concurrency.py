"""并发唯一性（NFR-03）与数据库约束。"""

from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, time

import pytest

from seat_reservation.domain.models import Actor, Role, User
from seat_reservation.domain.params import ParamSet
from seat_reservation.errors import Conflict
from seat_reservation.security import hash_password
from seat_reservation.service.container import AppContext
from seat_reservation.storage.repository import SqliteRepository
from tests.helpers import D, NOW


def _add_users(ctx: AppContext, count: int) -> list[str]:
    ids = []
    with ctx.db.transaction() as conn:
        repo = SqliteRepository(conn)
        repo.save_params(ParamSet().snapshot(), "SYSTEM", NOW)
        for index in range(count):
            user_id = f"C{index:03d}"
            ids.append(user_id)
            repo.save_user(
                User(
                    id=user_id,
                    name=f"并发{index}",
                    role=Role.STUDENT.value,
                    credit=100,
                    password_hash=hash_password("Passw0rd!23", iterations=1000),
                    created_at=NOW,
                    updated_at=NOW,
                )
            )
    return ids


def test_same_seat_same_slot_only_one_wins(ctx: AppContext) -> None:
    user_ids = _add_users(ctx, 8)

    def book(user_id: str) -> str:
        context = AppContext(ctx.settings)
        try:
            with context.engine() as engine:
                actor = Actor(id=user_id, name=user_id, role=Role.STUDENT)
                result = engine.create_reservation(
                    actor, "R1-001", D, time(9, 0), time(10, 0), NOW
                )
            return "ok" if result.ok else "conflict"
        except Exception:  # noqa: BLE001 - 竞争失败统一记 conflict
            return "conflict"

    with ThreadPoolExecutor(max_workers=8) as pool:
        outcomes = list(pool.map(book, user_ids))

    assert outcomes.count("ok") == 1, outcomes
    with ctx.db.read() as conn:
        reservations = conn.execute(
            "SELECT COUNT(*) AS c FROM reservations WHERE seat_id='R1-001' AND status='pending'"
        ).fetchone()["c"]
        slots = conn.execute(
            "SELECT COUNT(*) AS c FROM reservation_slots WHERE seat_id='R1-001' AND date=?",
            (D.isoformat(),),
        ).fetchone()["c"]
    assert reservations == 1
    assert slots == 2


def test_same_user_overlapping_slots_rejected(ctx: AppContext) -> None:
    _add_users(ctx, 1)
    with ctx.engine() as engine:
        actor = Actor(id="C000", name="并发0", role=Role.STUDENT)
        first = engine.create_reservation(actor, "R1-001", D, time(9, 0), time(10, 0), NOW)
        assert first.ok
    with pytest.raises(Conflict) as exc:
        with ctx.engine() as engine:
            actor = Actor(id="C000", name="并发0", role=Role.STUDENT)
            engine.create_reservation(actor, "R1-002", D, time(9, 30), time(10, 30), NOW)
    assert exc.value.rule == "BR-11"


def test_slot_primary_key_blocks_duplicate_occupancy(ctx: AppContext) -> None:
    with ctx.engine() as engine:
        result = engine.create_reservation(
            Actor(id="S1", name="张伟", role=Role.STUDENT), "R1-001", D, time(9, 0), time(10, 0), NOW
        )
    assert result.ok
    with ctx.db.transaction() as conn:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO reservation_slots(reservation_id, seat_id, user_id, date, slot) "
                "VALUES (?, 'R1-001', 'S1', ?, 18)",
                (result.data["id"], D.isoformat()),
            )
