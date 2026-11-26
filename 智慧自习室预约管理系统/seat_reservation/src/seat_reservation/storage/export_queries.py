"""D 模块新增 CSV 导出查询。

只负责新增的 4 类导出：

* ``complaints``：投诉记录
* ``blacklist``：黑名单记录
* ``credit_logs``：信用分流水
* ``waitlist``：候补记录

现有 ``reservations``、``violations``、``audit`` 的日期过滤已经在
``SqliteRepository`` 的列表方法中实现，由 ``repository.export_rows`` 复用，
不在本模块重复编写。

日期口径
--------
* 投诉、黑名单、信用流水按记录创建时间过滤（闭区间）。
* 候补按目标使用日期 ``waitlist.date`` 过滤（闭区间）。

本模块不做权限判断、不做 CSV 转义；由上层 engine/route 负责。
"""

from __future__ import annotations

import sqlite3
from datetime import date
from typing import Any

from ..domain.timeutil import hhmm


def _timestamp_clause(column: str, date_from: date | None, date_to: date | None) -> tuple[str, list[Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    if date_from:
        clauses.append(f"{column} >= ?")
        params.append(f"{date_from.isoformat()} 00:00:00")
    if date_to:
        clauses.append(f"{column} <= ?")
        params.append(f"{date_to.isoformat()} 23:59:59")
    return (" AND ".join(clauses) if clauses else "1=1"), params


def _date_clause(column: str, date_from: date | None, date_to: date | None) -> tuple[str, list[Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    if date_from:
        clauses.append(f"{column} >= ?")
        params.append(date_from.isoformat())
    if date_to:
        clauses.append(f"{column} <= ?")
        params.append(date_to.isoformat())
    return (" AND ".join(clauses) if clauses else "1=1"), params


def _text(value: Any) -> str:
    return "" if value is None else str(value)


def complaint_rows(
    conn: sqlite3.Connection, *, date_from: date | None, date_to: date | None
) -> tuple[list[str], list[list[Any]]]:
    where, params = _timestamp_clause("c.created_at", date_from, date_to)
    rows = conn.execute(
        f"""
        SELECT c.*, u.name AS user_name
        FROM complaints c
        JOIN users u ON u.id = c.user_id
        WHERE {where}
        ORDER BY c.created_at DESC, c.id DESC
        LIMIT 10001
        """,
        params,
    ).fetchall()
    headers = ["投诉号", "学生账号", "学生姓名", "类型", "状态", "内容", "反馈", "处理人", "处理时间", "评分", "创建时间"]
    return headers, [
        [
            row["id"],
            row["user_id"],
            row["user_name"],
            row["type"],
            row["status"],
            row["content"],
            _text(row["feedback"]),
            _text(row["resolved_by"]),
            _text(row["resolved_at"]),
            _text(row["rating"]),
            _text(row["created_at"]),
        ]
        for row in rows
    ]


def blacklist_rows(
    conn: sqlite3.Connection, *, date_from: date | None, date_to: date | None
) -> tuple[list[str], list[list[Any]]]:
    where, params = _timestamp_clause("b.created_at", date_from, date_to)
    rows = conn.execute(
        f"""
        SELECT b.*, u.name AS user_name
        FROM blacklist b
        JOIN users u ON u.id = b.user_id
        WHERE {where}
        ORDER BY b.created_at DESC, b.id DESC
        LIMIT 10001
        """,
        params,
    ).fetchall()
    headers = ["记录号", "学生账号", "学生姓名", "原因", "来源", "开始时间", "结束时间", "释放时间", "释放人", "创建时间"]
    return headers, [
        [
            row["id"],
            row["user_id"],
            row["user_name"],
            row["reason"],
            row["source"],
            _text(row["start_at"]),
            _text(row["end_at"]),
            _text(row["released_at"]),
            _text(row["released_by"]),
            _text(row["created_at"]),
        ]
        for row in rows
    ]


def credit_log_rows(
    conn: sqlite3.Connection, *, date_from: date | None, date_to: date | None
) -> tuple[list[str], list[list[Any]]]:
    where, params = _timestamp_clause("cl.created_at", date_from, date_to)
    rows = conn.execute(
        f"""
        SELECT cl.*, u.name AS user_name
        FROM credit_logs cl
        JOIN users u ON u.id = cl.user_id
        WHERE {where}
        ORDER BY cl.created_at DESC, cl.id DESC
        LIMIT 10001
        """,
        params,
    ).fetchall()
    headers = ["流水号", "学生账号", "学生姓名", "变动", "余额", "原因", "违规号", "操作人", "创建时间"]
    return headers, [
        [
            row["id"],
            row["user_id"],
            row["user_name"],
            row["delta"],
            row["balance_after"],
            row["reason"],
            _text(row["violation_id"]),
            _text(row["operator_id"]),
            _text(row["created_at"]),
        ]
        for row in rows
    ]


def waitlist_rows(
    conn: sqlite3.Connection, *, date_from: date | None, date_to: date | None
) -> tuple[list[str], list[list[Any]]]:
    where, params = _date_clause("w.date", date_from, date_to)
    rows = conn.execute(
        f"""
        SELECT w.*, u.name AS user_name, s.room_id AS room_id, r.name AS room_name
        FROM waitlist w
        JOIN users u ON u.id = w.user_id
        JOIN seats s ON s.id = w.seat_id
        JOIN rooms r ON r.id = s.room_id
        WHERE {where}
        ORDER BY w.date DESC, w.start_slot, w.seq, w.id
        LIMIT 10001
        """,
        params,
    ).fetchall()
    headers = ["候补号", "学生账号", "学生姓名", "座位", "自习室", "日期", "开始", "结束", "状态", "序号", "创建时间", "确认截止"]
    return headers, [
        [
            row["id"],
            row["user_id"],
            row["user_name"],
            row["seat_id"],
            row["room_name"],
            row["date"],
            hhmm(int(row["start_slot"])),
            hhmm(int(row["end_slot"])),
            row["status"],
            row["seq"],
            _text(row["created_at"]),
            _text(row["confirm_deadline"]),
        ]
        for row in rows
    ]
