"""运营分析查询（D 模块）。

本模块只负责按日期区间/自习室做只读统计，不判断权限、不校验业务规则、
不格式化 CSV。所有函数在调用方开启的事务内执行，不自行提交。

口径约定
--------
* 日期区间为闭区间；预约按 ``reservations.date`` 归属日期。
* ``reservations`` 统计区间内全部预约；``cancelled``、``released`` 分开统计，
  不合并（Q-02 未确认前保持原始口径）。
* **预约时段利用率**：使用 ``reservations`` 的时段长度计算，不使用
  ``reservation_slots``。``reservation_slots`` 只代表当前占用，已完成/已释放的
  历史预约会被删除，不能用于历史区间统计。该指标 =
  非取消预约的预订时段数 /（座位数 × 每日开放时段数 × 天数），
  反映**预约资源**的占用，不等同于用户实际签到后的使用时长。
* 比率分母：
  - ``checkin_rate`` = 已签到预约 /（已签到预约 + 明确未签到预约）；
    已签到后进入 released 的预约仍计入 checkins；签到前被管理员释放的预约
    不计入签到率。
  - ``completion_rate`` = completed / 出勤结果预约（completed + no_show；
    不含 pending/in_use/away/released/cancelled）。
  - ``no_show_rate`` = no_show / 出勤结果预约（completed + no_show）。
  - ``cancel_rate`` = cancelled / 全部预约（含 cancelled）。
  - ``complaint_resolution_rate`` = 已办结投诉（resolved + rated）/ 投诉总数。
  - ``waitlist.confirmed_rate`` = confirmed / 候补总数（含 queued/notified 等）。
* 投诉按 ``created_at`` 归属日期；有 ``seat_id`` 时按座位房间归属，
  否则尝试通过 ``reservation_id`` 关联房间；无座位/预约的投诉在按房间筛选时不计数。
* 候补按 ``waitlist.date``（目标使用日期）归属区间。
"""

from __future__ import annotations

import sqlite3
from datetime import date, timedelta
from typing import Any

from ..domain.timeutil import hhmm

WAITLIST_STATUSES = ("queued", "notified", "confirmed", "expired", "cancelled")

METRIC_NOTES: dict[str, str] = {
    "booking_utilization": (
        "预约时段利用率 = 非取消预约的预订时段数 /（座位数 × 每日开放时段数 × 天数）；"
        "反映预约资源占用，不等同于实际签到使用时长。"
    ),
    "checkin_rate": (
        "已签到预约 /（已签到预约 + 明确未签到预约）；"
        "已签到后进入 released 的预约仍计入 checkins，签到前被释放的不计入。"
    ),
    "completion_rate": (
        "已完成预约 / 出勤结果预约（completed + no_show；"
        "不含 pending/in_use/away/released/cancelled）。"
    ),
    "no_show_rate": (
        "未签到预约 / 出勤结果预约（completed + no_show；"
        "不含 pending/in_use/away/released/cancelled）。"
    ),
    "cancel_rate": "已取消预约 / 全部预约（含 cancelled）。",
    "complaint_resolution_rate": "已办结投诉（resolved + rated）/ 投诉总数。",
    "waitlist.confirmed_rate": "已确认候补 / 候补总数（含 queued/notified 等）。",
    "released": (
        "released 单独统计，不进入 completion/no_show 分母；"
        "签到后进入 released 的预约仍计入 checkins，签到前释放的不计入签到率。"
    ),
    "avg_minutes": "已完成预约的平均预约时长，不等同于实际在座时长。",
}


def _rate(part: int, total: int) -> float:
    return round(part / total * 100, 1) if total else 0.0


def _days(date_from: date, date_to: date) -> list[date]:
    days: list[date] = []
    current = date_from
    while current <= date_to:
        days.append(current)
        current += timedelta(days=1)
    return days


def _timestamp_bounds(date_from: date, date_to: date) -> tuple[str, str]:
    return f"{date_from.isoformat()} 00:00:00", f"{date_to.isoformat()} 23:59:59"


def _reservation_where(room_id: str | None) -> tuple[str, list[Any]]:
    clause = "r.date BETWEEN ? AND ?"
    params: list[Any] = []
    if room_id:
        clause += " AND s.room_id = ?"
        params.append(room_id)
    return clause, params


def _daily_summary(conn: sqlite3.Connection, date_from: date, date_to: date, room_id: str | None) -> list[dict[str, Any]]:
    where, room_params = _reservation_where(room_id)
    rows = conn.execute(
        f"""
        SELECT r.date AS day,
               COUNT(*) AS reservations,
               COALESCE(SUM(CASE WHEN r.status='completed' THEN 1 ELSE 0 END), 0) AS completed,
               COALESCE(SUM(CASE WHEN r.status='no_show' THEN 1 ELSE 0 END), 0) AS no_show,
               COALESCE(SUM(CASE WHEN r.status='cancelled' THEN 1 ELSE 0 END), 0) AS cancelled,
               COALESCE(SUM(CASE WHEN r.status='released' THEN 1 ELSE 0 END), 0) AS released,
               COALESCE(SUM(CASE WHEN r.checked_in_at IS NOT NULL THEN 1 ELSE 0 END), 0) AS checkins
        FROM reservations r
        JOIN seats s ON s.id = r.seat_id
        WHERE {where}
        GROUP BY r.date
        ORDER BY r.date
        """,
        [date_from.isoformat(), date_to.isoformat(), *room_params],
    ).fetchall()
    indexed = {row["day"]: row for row in rows}
    items: list[dict[str, Any]] = []
    for day in _days(date_from, date_to):
        row = indexed.get(day.isoformat())
        items.append(
            {
                "date": day.isoformat(),
                "label": day.strftime("%m-%d"),
                "reservations": int(row["reservations"] or 0) if row else 0,
                "completed": int(row["completed"] or 0) if row else 0,
                "no_show": int(row["no_show"] or 0) if row else 0,
                "cancelled": int(row["cancelled"] or 0) if row else 0,
                "released": int(row["released"] or 0) if row else 0,
                "checkins": int(row["checkins"] or 0) if row else 0,
            }
        )
    return items


def _peak_hours(conn: sqlite3.Connection, date_from: date, date_to: date, room_id: str | None) -> list[dict[str, Any]]:
    where, room_params = _reservation_where(room_id)
    rows = conn.execute(
        f"""
        SELECT r.start_slot AS slot,
               COUNT(*) AS count,
               AVG((r.end_slot - r.start_slot) * 30.0) AS avg_minutes
        FROM reservations r
        JOIN seats s ON s.id = r.seat_id
        WHERE {where}
        GROUP BY r.start_slot
        ORDER BY r.start_slot
        """,
        [date_from.isoformat(), date_to.isoformat(), *room_params],
    ).fetchall()
    return [
        {
            "slot": int(row["slot"]),
            "hour": hhmm(int(row["slot"])),
            "count": int(row["count"] or 0),
            "avg_minutes": round(float(row["avg_minutes"]), 1) if row["avg_minutes"] is not None else 0.0,
        }
        for row in rows
    ]


def _room_ranking(conn: sqlite3.Connection, date_from: date, date_to: date, room_id: str | None) -> list[dict[str, Any]]:
    """按自习室返回预约时段利用率；不等同于实际座位使用率。"""
    room_sql = "SELECT id, name, location, status, open_slot, close_slot FROM rooms"
    room_params: list[Any] = []
    if room_id:
        room_sql += " WHERE id = ?"
        room_params.append(room_id)
    room_sql += " ORDER BY id"
    rooms = conn.execute(room_sql, room_params).fetchall()
    days = max((date_to - date_from).days + 1, 0)
    items: list[dict[str, Any]] = []
    for room in rooms:
        seat_count = int(
            conn.execute("SELECT COUNT(*) AS c FROM seats WHERE room_id = ?", (room["id"],)).fetchone()["c"]
        )
        row = conn.execute(
            """
            SELECT COUNT(*) AS reservations,
                   COALESCE(SUM(CASE WHEN r.status='completed' THEN 1 ELSE 0 END), 0) AS completed,
                   COALESCE(SUM(CASE WHEN r.status='no_show' THEN 1 ELSE 0 END), 0) AS no_show,
                   COALESCE(SUM(CASE WHEN r.status='cancelled' THEN 1 ELSE 0 END), 0) AS cancelled,
                   COALESCE(SUM(CASE WHEN r.status='released' THEN 1 ELSE 0 END), 0) AS released,
                   COALESCE(SUM(CASE WHEN r.checked_in_at IS NOT NULL THEN 1 ELSE 0 END), 0) AS checkins,
                   COALESCE(SUM(CASE WHEN r.status != 'cancelled' THEN r.end_slot - r.start_slot ELSE 0 END), 0) AS booked_slots,
                   AVG(CASE WHEN r.status='completed' THEN (r.end_slot - r.start_slot) * 30.0 END) AS avg_minutes
            FROM reservations r
            JOIN seats s ON s.id = r.seat_id
            WHERE r.date BETWEEN ? AND ? AND s.room_id = ?
            """,
            (date_from.isoformat(), date_to.isoformat(), room["id"]),
        ).fetchone()
        capacity_slots = seat_count * max(int(room["close_slot"]) - int(room["open_slot"]), 0) * days
        booked_slots = int(row["booked_slots"] or 0)
        items.append(
            {
                "id": room["id"],
                "name": room["name"],
                "location": room["location"],
                "status": room["status"],
                "open_time": hhmm(int(room["open_slot"])),
                "close_time": hhmm(int(room["close_slot"])),
                "total_seats": seat_count,
                "reservations": int(row["reservations"] or 0),
                "completed": int(row["completed"] or 0),
                "no_show": int(row["no_show"] or 0),
                "cancelled": int(row["cancelled"] or 0),
                "released": int(row["released"] or 0),
                "checkins": int(row["checkins"] or 0),
                "booked_slots": booked_slots,
                "capacity_slots": capacity_slots,
                "booking_utilization": round(booked_slots / capacity_slots * 100, 1) if capacity_slots else 0.0,
                "avg_minutes": round(float(row["avg_minutes"]), 1) if row["avg_minutes"] is not None else 0.0,
            }
        )
    return items


def _violation_trend(conn: sqlite3.Connection, date_from: date, date_to: date, room_id: str | None) -> dict[str, list[dict[str, Any]]]:
    start_at, end_at = _timestamp_bounds(date_from, date_to)
    clause = "v.occurred_at >= ? AND v.occurred_at <= ?"
    params: list[Any] = [start_at, end_at]
    if room_id:
        clause += " AND s.room_id = ?"
        params.append(room_id)
    rows = conn.execute(
        f"""
        SELECT substr(v.occurred_at, 1, 10) AS day, v.type AS type, COUNT(*) AS count
        FROM violations v
        JOIN reservations r ON r.id = v.reservation_id
        JOIN seats s ON s.id = r.seat_id
        WHERE {clause}
        GROUP BY day, v.type
        ORDER BY day, v.type
        """,
        params,
    ).fetchall()
    trend = [
        {"date": row["day"], "type": row["type"], "count": int(row["count"] or 0)}
        for row in rows
    ]
    totals: dict[str, int] = {}
    for item in trend:
        totals[item["type"]] = totals.get(item["type"], 0) + item["count"]
    by_type = [
        {"type": key, "count": value}
        for key, value in sorted(totals.items(), key=lambda item: (-item[1], item[0]))
    ]
    return {"by_type": by_type, "trend": trend}


def _rating_distribution(conn: sqlite3.Connection, date_from: date, date_to: date, room_id: str | None) -> dict[str, Any]:
    start_at, end_at = _timestamp_bounds(date_from, date_to)
    clause = "rv.created_at >= ? AND rv.created_at <= ?"
    params: list[Any] = [start_at, end_at]
    if room_id:
        clause += " AND s.room_id = ?"
        params.append(room_id)
    rows = conn.execute(
        f"""
        SELECT rv.rating AS rating, COUNT(*) AS count
        FROM reviews rv
        JOIN seats s ON s.id = rv.seat_id
        WHERE {clause}
        GROUP BY rv.rating
        ORDER BY rv.rating
        """,
        params,
    ).fetchall()
    counts = {int(row["rating"]): int(row["count"] or 0) for row in rows}
    total = sum(counts.values())
    weighted = sum(rating * count for rating, count in counts.items())
    return {
        "total": total,
        "average": round(weighted / total, 2) if total else None,
        "distribution": [{"rating": rating, "count": counts.get(rating, 0)} for rating in range(1, 6)],
    }


def _waitlist_funnel(conn: sqlite3.Connection, date_from: date, date_to: date, room_id: str | None) -> dict[str, Any]:
    clause = "w.date >= ? AND w.date <= ?"
    params: list[Any] = [date_from.isoformat(), date_to.isoformat()]
    if room_id:
        clause += " AND s.room_id = ?"
        params.append(room_id)
    rows = conn.execute(
        f"""
        SELECT w.status AS status, COUNT(*) AS count
        FROM waitlist w
        JOIN seats s ON s.id = w.seat_id
        WHERE {clause}
        GROUP BY w.status
        """,
        params,
    ).fetchall()
    counts = {row["status"]: int(row["count"] or 0) for row in rows}
    total = sum(counts.values())
    confirmed = counts.get("confirmed", 0)
    return {
        "total": total,
        "waiting": counts.get("queued", 0) + counts.get("notified", 0),
        "confirmed_rate": _rate(confirmed, total),
        "by_status": [
            {"status": status, "count": counts.get(status, 0)}
            for status in WAITLIST_STATUSES
        ],
    }


def _complaint_sla(conn: sqlite3.Connection, date_from: date, date_to: date, room_id: str | None) -> dict[str, Any]:
    start_at, end_at = _timestamp_bounds(date_from, date_to)
    base = """
        FROM complaints c
        LEFT JOIN seats cs ON cs.id = c.seat_id
        LEFT JOIN reservations cr ON cr.id = c.reservation_id
        LEFT JOIN seats rs ON rs.id = cr.seat_id
        WHERE c.created_at >= ? AND c.created_at <= ?
    """
    params: list[Any] = [start_at, end_at]
    if room_id:
        base += " AND COALESCE(cs.room_id, rs.room_id) = ?"
        params.append(room_id)
    rows = conn.execute(
        f"SELECT c.status AS status, COUNT(*) AS count {base} GROUP BY c.status",
        params,
    ).fetchall()
    counts = {row["status"]: int(row["count"] or 0) for row in rows}
    total = sum(counts.values())
    resolved = counts.get("resolved", 0)
    rated = counts.get("rated", 0)
    completed = resolved + rated
    sla = conn.execute(
        f"""
        SELECT AVG((julianday(c.resolved_at) - julianday(c.created_at)) * 24.0) AS avg_hours,
               AVG(c.rating) AS avg_rating
        {base} AND c.resolved_at IS NOT NULL
        """,
        params,
    ).fetchone()
    return {
        "total": total,
        "pending": counts.get("pending", 0),
        "resolved": resolved,
        "rated": rated,
        "completed": completed,
        "resolution_rate": _rate(completed, total),
        "average_hours": round(float(sla["avg_hours"]), 1) if sla["avg_hours"] is not None else None,
        "average_rating": round(float(sla["avg_rating"]), 2) if sla["avg_rating"] is not None else None,
    }


def build_range_summary(
    conn: sqlite3.Connection,
    *,
    date_from: date,
    date_to: date,
    room_id: str | None = None,
) -> dict[str, Any]:
    """返回运营分析完整结果；权限与日期合法性由上层负责。"""

    where, room_params = _reservation_where(room_id)
    kpi_row = conn.execute(
        f"""
        SELECT COUNT(*) AS reservations,
               COALESCE(SUM(CASE WHEN r.status='pending' THEN 1 ELSE 0 END), 0) AS pending,
               COALESCE(SUM(CASE WHEN r.status='completed' THEN 1 ELSE 0 END), 0) AS completed,
               COALESCE(SUM(CASE WHEN r.status='cancelled' THEN 1 ELSE 0 END), 0) AS cancelled,
               COALESCE(SUM(CASE WHEN r.status='released' THEN 1 ELSE 0 END), 0) AS released,
               COALESCE(SUM(CASE WHEN r.status='no_show' THEN 1 ELSE 0 END), 0) AS no_show,
               COALESCE(SUM(CASE WHEN r.checked_in_at IS NOT NULL THEN 1 ELSE 0 END), 0) AS checkins,
               AVG(CASE WHEN r.status='completed' THEN (r.end_slot - r.start_slot) * 30.0 END) AS avg_minutes
        FROM reservations r
        JOIN seats s ON s.id = r.seat_id
        WHERE {where}
        """,
        [date_from.isoformat(), date_to.isoformat(), *room_params],
    ).fetchone()
    reservations = int(kpi_row["reservations"] or 0)
    pending = int(kpi_row["pending"] or 0)
    completed = int(kpi_row["completed"] or 0)
    cancelled = int(kpi_row["cancelled"] or 0)
    released = int(kpi_row["released"] or 0)
    no_show = int(kpi_row["no_show"] or 0)
    checkins = int(kpi_row["checkins"] or 0)
    valid_reservations = max(reservations - cancelled, 0)
    checkin_base = checkins + no_show
    attendance_base = completed + no_show
    ratings = _rating_distribution(conn, date_from, date_to, room_id)
    waitlist = _waitlist_funnel(conn, date_from, date_to, room_id)
    complaints = _complaint_sla(conn, date_from, date_to, room_id)
    rooms = _room_ranking(conn, date_from, date_to, room_id)
    return {
        "range": {
            "date_from": date_from.isoformat(),
            "date_to": date_to.isoformat(),
            "days": max((date_to - date_from).days + 1, 0),
            "room_id": room_id,
        },
        "metric_notes": dict(METRIC_NOTES),
        "kpi": {
            "reservations": reservations,
            "valid_reservations": valid_reservations,
            "pending": pending,
            "completed": completed,
            "cancelled": cancelled,
            "released": released,
            "no_show": no_show,
            "checkins": checkins,
            "checkin_base": checkin_base,
            "attendance_base": attendance_base,
            "checkin_rate": _rate(checkins, checkin_base),
            "completion_rate": _rate(completed, attendance_base),
            "no_show_rate": _rate(no_show, attendance_base),
            "cancel_rate": _rate(cancelled, reservations),
            "avg_minutes": round(float(kpi_row["avg_minutes"]), 1) if kpi_row["avg_minutes"] is not None else 0.0,
            "average_rating": ratings["average"],
            "complaint_resolution_rate": complaints["resolution_rate"],
            "waitlist_waiting": waitlist["waiting"],
            "total_seats": sum(int(room["total_seats"]) for room in rooms),
        },
        "daily": _daily_summary(conn, date_from, date_to, room_id),
        "peak_hours": _peak_hours(conn, date_from, date_to, room_id),
        "rooms": rooms,
        "violations": _violation_trend(conn, date_from, date_to, room_id),
        "ratings": ratings,
        "waitlist": waitlist,
        "complaints": complaints,
    }
