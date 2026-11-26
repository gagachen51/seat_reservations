"""演示种子数据与正式库的首次管理员初始化。

* 相对 ``now`` 生成，保证任何时间打开系统都有“今天”的数据可看。
* 仅通过仓储写入，不绕过约束。
* 演示密码**不再硬编码**：默认随机生成，只能通过 ``SEAT_DEMO_PASSWORD``
  或调用方显式传入；调用方只在初始化渠道展示一次。
* 正式库首次初始化用 ``bootstrap_admin``：随机密码 + 强制首次登录改密。
* 历史信用补差使用一条显式 ``credit_logs``（原因含“演示数据”），避免伪造违规记录。
"""

from __future__ import annotations

import os
from datetime import date, datetime, time, timedelta
from pathlib import Path

from ..domain.models import (
    AuditLog,
    BlacklistRecord,
    Complaint,
    ComplaintStatus,
    ComplaintType,
    EndReason,
    LimitType,
    Notification,
    NotificationType,
    Reservation,
    ReservationSource,
    ReservationStatus,
    Review,
    Role,
    Room,
    Seat,
    SeatAdminStatus,
    SeatType,
    User,
    Violation,
    ViolationSource,
    ViolationStatus,
    ViolationType,
    WaitlistEntry,
    WaitlistStatus,
)
from ..domain.params import ParamSet
from ..domain.timeutil import add_minutes, datetime_at, fmt_datetime, slot_of_time
from ..security import generate_initial_password, hash_password
from .database import Database
from .repository import SqliteRepository


def resolve_demo_password(explicit: str | None = None) -> str:
    """演示密码的解析顺序：调用方显式值 → ``SEAT_DEMO_PASSWORD`` → 随机生成。

    返回值只能交给初始化渠道（命令行输出 / 桌面首次启动提示）展示一次，
    不得写入日志、源码或长期明文文件，也不得通过 HTTP 接口返回。
    """
    if explicit:
        return explicit
    configured = os.environ.get("SEAT_DEMO_PASSWORD", "").strip()
    return configured or generate_initial_password()

ROOMS = [
    ("A101", "第一教学楼 A101", "一楼东侧", 8),
    ("B201", "第二教学楼 B201", "二楼中庭", 8),
    ("C301", "图书馆 C301", "三楼静音区", 7),
]

# 判断库内是否已经写入标准演示数据：四个角色各取一个标志账号。
# 只有同时存在这三个非系统管理员账号，才认为演示数据完整。
DEMO_USER_IDS = ("S2023001", "T7001", "A9001")


def _reset(conn) -> None:
    """清空业务表后重新写入演示数据。

    ``audit_logs`` 必须保留：审计表是 append-only（数据库触发器禁止 UPDATE /
    DELETE），既不能删，也不应该在演示数据初始化时抹掉历史审计。重复初始化
    会追加一条新的 ``seed_demo`` 审计，而不是覆盖旧记录。
    """
    for table in (
        "reservation_slots",
        "reviews",
        "complaints",
        "violations",
        "credit_logs",
        "waitlist",
        "notifications",
        "blacklist",
        "seat_blocks",
        "sessions",
        "reservations",
        "seats",
        "rooms",
        "users",
        "sequences",
        "system_params",
    ):
        if table == "sequences":
            # 审计编号序列必须保留，否则重新初始化会从 A-0000001 重号，
            # 与保留下来的历史审计主键冲突。
            conn.execute("DELETE FROM sequences WHERE name <> 'audit'")
            continue
        conn.execute(f"DELETE FROM {table}")

    # 兼容“有审计记录但序列缺失/落后”的旧库：把 audit 序列推进到当前最大值。
    sequence = conn.execute(
        "SELECT value FROM sequences WHERE name = 'audit'"
    ).fetchone()
    maximum = conn.execute(
        "SELECT MAX(CAST(substr(id, 3) AS INTEGER)) AS m FROM audit_logs"
    ).fetchone()
    target = max(int(sequence["value"]) if sequence else 0, int(maximum["m"] or 0))
    if target:
        conn.execute(
            "INSERT INTO sequences(name, value) VALUES ('audit', ?) "
            "ON CONFLICT(name) DO UPDATE SET value = excluded.value",
            (target,),
        )


def _make_users(now: datetime, password: str, iterations: int) -> list[User]:
    encoded = hash_password(password, iterations=iterations)
    base = [
        ("S2023001", "张伟", Role.STUDENT, "计算机学院", "软件 2301", 100),
        ("S2023002", "李娜", Role.STUDENT, "计算机学院", "软件 2301", 100),
        ("S2023003", "王强", Role.STUDENT, "计算机学院", "软件 2302", 88),
        ("S2023005", "赵敏", Role.STUDENT, "计算机学院", "软件 2301", 55),
        ("S2023006", "李华", Role.STUDENT, "计算机学院", "软件 2302", 82),
        ("S2023007", "周涛", Role.STUDENT, "计算机学院", "软件 2301", 100),
        ("T7001", "刘明", Role.TEACHER, "计算机学院", "软件 2301", 100),
        ("A9001", "王丽", Role.ROOM_ADMIN, "后勤保障处", "", 100),
        ("Y0001", "陈刚", Role.SYS_ADMIN, "信息化中心", "", 100),
    ]
    users = []
    for user_id, name, role, department, class_name, credit in base:
        users.append(
            User(
                id=user_id,
                name=name,
                role=role.value,
                department=department,
                class_name=class_name,
                credit=credit,
                password_hash=encoded,
                created_at=now,
                updated_at=now,
            )
        )
    return users


def _make_seats(now: datetime) -> list[Seat]:
    seats: list[Seat] = []
    for room_id, _name, _location, count in ROOMS:
        for index in range(1, count + 1):
            seat_id = f"{room_id}-{index:03d}"
            seats.append(
                Seat(
                    id=seat_id,
                    room_id=room_id,
                    seat_type=SeatType.QUIET.value
                    if room_id == "C301"
                    else SeatType.STANDARD.value,
                    has_power=index % 2 == 1,
                    is_window=index % 3 == 0,
                    created_at=now,
                )
            )
    seats.append(
        Seat(
            id="C301-D01",
            room_id="C301",
            seat_type=SeatType.DISCUSSION.value,
            capacity=6,
            has_power=True,
            created_at=now,
        )
    )
    # 一个维护中的座位，便于演示封禁/解封
    seats[-2].admin_status = SeatAdminStatus.MAINTENANCE.value
    seats[-2].note = "桌面松动，等待维修"
    return seats


def _completed(
    reservation_id: str,
    user_id: str,
    seat_id: str,
    day: date,
    start_slot: int,
    end_slot: int,
    now: datetime,
    credit: int,
) -> Reservation:
    start_at, end_at = datetime_at(day, start_slot), datetime_at(day, end_slot)
    return Reservation(
        id=reservation_id,
        user_id=user_id,
        seat_id=seat_id,
        day=day,
        start_slot=start_slot,
        end_slot=end_slot,
        start_at=start_at,
        end_at=end_at,
        status=ReservationStatus.COMPLETED.value,
        source=ReservationSource.SELF.value,
        created_at=start_at - timedelta(days=1),
        updated_at=end_at,
        checked_in_at=start_at - timedelta(minutes=8),
        checkin_deadline=start_at + timedelta(minutes=15),
        checked_out_at=end_at - timedelta(minutes=3),
        end_reason=EndReason.NORMAL_CHECKOUT.value,
        credential_code=f"SEED{reservation_id[-4:]}",
        credential_issued_at=start_at - timedelta(minutes=10),
        credential_used=True,
        policy={},
        version=1,
    )


def seed_demo(
    db: Database,
    now: datetime,
    *,
    password: str | None = None,
    reset: bool = True,
    hash_iterations: int = 260_000,
) -> dict[str, int]:
    """写入演示数据，返回各表行数摘要。

    ``password`` 为空时随机生成，调用方需要展示时先用 ``resolve_demo_password``
    取得同一份值再传入。
    """
    password = password or generate_initial_password()
    db.migrate()
    params = ParamSet()
    counts: dict[str, int] = {}

    with db.transaction() as conn:
        repo = SqliteRepository(conn)
        if reset:
            _reset(conn)
        repo.save_params({k: v for k, v in params.snapshot().items()}, "SYSTEM", now)

        users = _make_users(now, password, hash_iterations)
        for user in users:
            repo.save_user(user)
        counts["users"] = len(users)

        rooms = [
            Room(
                id=room_id,
                name=name,
                location=location,
                open_slot=slot_of_time(time(7, 0)),
                close_slot=slot_of_time(time(23, 0)),
                created_at=now,
            )
            for room_id, name, location, _count in ROOMS
        ]
        for room in rooms:
            repo.save_room(room)

        seats = _make_seats(now)
        for seat in seats:
            repo.save_seat(seat)
        counts["seats"] = len(seats)

        # 历史 6 天：每天 3 条已完成预约，供趋势/使用率展示
        seat_ids = [seat.id for seat in seats if seat.admin_status == "available"]
        students = ["S2023001", "S2023002", "S2023003"]
        sequence = 0
        for offset in range(6, 0, -1):
            day = now.date() - timedelta(days=offset)
            for index, start_hour in enumerate((9, 14, 19)):
                sequence += 1
                user_id = students[(offset + index) % len(students)]
                seat_id = seat_ids[(offset * 3 + index) % len(seat_ids)]
                reservation = _completed(
                    f"R{day:%Y%m%d}-{sequence:04d}",
                    user_id,
                    seat_id,
                    day,
                    start_hour * 2,
                    start_hour * 2 + 2,
                    now,
                    100,
                )
                reservation.policy = params.snapshot()
                repo.insert_reservation(reservation)

        # 赵敏：2 天前未签到（可申诉），信用分 55 由历史补差形成
        low_day = now.date() - timedelta(days=2)
        low_start = 18  # 09:00
        low_reservation = Reservation(
            id=f"R{low_day:%Y%m%d}-9001",
            user_id="S2023005",
            seat_id="A101-006",
            day=low_day,
            start_slot=low_start,
            end_slot=low_start + 2,
            start_at=datetime_at(low_day, low_start),
            end_at=datetime_at(low_day, low_start + 2),
            status=ReservationStatus.NO_SHOW.value,
            created_at=datetime_at(low_day, low_start) - timedelta(days=2),
            updated_at=datetime_at(low_day, low_start + 1),
            checkin_deadline=datetime_at(low_day, low_start) + timedelta(minutes=15),
            checked_out_at=datetime_at(low_day, low_start + 1),
            end_reason=EndReason.NO_SHOW.value,
            policy=params.snapshot(),
        )
        repo.insert_reservation(low_reservation)
        repo.insert_violation(
            Violation(
                id=repo.next_id("violation", "VIO-", 6),
                reservation_id=low_reservation.id,
                user_id="S2023005",
                violation_type=ViolationType.NO_SHOW.value,
                points=10,
                source=ViolationSource.SYSTEM.value,
                status=ViolationStatus.ACTIVE.value,
                occurred_at=datetime_at(low_day, low_start + 1),
                note="演示数据：超过签到窗口未签到",
                created_at=datetime_at(low_day, low_start + 1),
            )
        )
        repo.insert_credit_log(
            log_id=repo.next_id("credit", "CL-", 7),
            user_id="S2023005",
            delta=-35,
            balance_after=55,
            reason="历史信用记录导入（演示数据）",
            violation_id=None,
            operator_id=None,
            created_at=now - timedelta(days=10),
        )
        zhaomin = repo.get_user("S2023005")
        if zhaomin:
            zhaomin.limit_type = LimitType.CREDIT.value
            zhaomin.updated_at = now
            repo.save_user(zhaomin)

        # 李华：黑名单中
        lihua = repo.get_user("S2023006")
        if lihua:
            lihua.limit_type = LimitType.BLACKLIST.value
            lihua.limit_until = now + timedelta(days=5)
            lihua.updated_at = now
            repo.save_user(lihua)
        repo.insert_blacklist(
            BlacklistRecord(
                id=repo.next_id("blacklist", "BL-", 6),
                user_id="S2023006",
                reason="30 天内有效违约 3 次（演示数据）",
                source="auto",
                start_at=now - timedelta(days=2),
                end_at=now + timedelta(days=5),
                created_at=now - timedelta(days=2),
            )
        )

        # 今日：一条待签到 + 一条使用中，便于演示与巡检
        open_slot = slot_of_time(time(7, 0))
        close_slot = slot_of_time(time(23, 0))
        current_slot = min(
            max((now.hour * 60 + now.minute) // 30, open_slot), close_slot - 2
        )
        pending_slot = current_slot + 2
        pending_day = now.date()
        if pending_slot + 2 > close_slot:
            pending_slot = slot_of_time(time(9, 0))
            pending_day = now.date() + timedelta(days=1)
        pending = Reservation(
            id=f"R{pending_day:%Y%m%d}-7001",
            user_id="S2023001",
            seat_id="B201-001",
            day=pending_day,
            start_slot=pending_slot,
            end_slot=pending_slot + 2,
            start_at=datetime_at(pending_day, pending_slot),
            end_at=datetime_at(pending_day, pending_slot + 2),
            status=ReservationStatus.PENDING.value,
            created_at=now - timedelta(minutes=10),
            updated_at=now - timedelta(minutes=10),
            checkin_deadline=datetime_at(pending_day, pending_slot) + timedelta(minutes=15),
            policy=params.snapshot(),
        )
        repo.insert_reservation(pending)
        repo.occupy(pending)
        repo.insert_notification(
            Notification(
                id=repo.next_id("notification", "N-", 7),
                user_id="S2023001",
                notification_type=NotificationType.RESERVATION_CREATED.value,
                content=f"预约成功：B201-001 {pending_day.isoformat()} {pending.start_time}-{pending.end_time}",
                created_at=now - timedelta(minutes=10),
            )
        )

        if open_slot <= current_slot <= close_slot - 2:
            in_use = Reservation(
                id=f"R{now.date():%Y%m%d}-7002",
                user_id="S2023002",
                seat_id="B201-002",
                day=now.date(),
                start_slot=current_slot,
                end_slot=min(current_slot + 2, close_slot),
                start_at=datetime_at(now.date(), current_slot),
                end_at=datetime_at(now.date(), min(current_slot + 2, close_slot)),
                status=ReservationStatus.IN_USE.value,
                created_at=now - timedelta(hours=1),
                updated_at=now,
                checked_in_at=now - timedelta(minutes=20),
                checkin_deadline=datetime_at(now.date(), current_slot) + timedelta(minutes=15),
                credential_code="SEEDINUSE",
                credential_issued_at=now - timedelta(minutes=25),
                credential_used=True,
                policy=params.snapshot(),
            )
            repo.insert_reservation(in_use)
            repo.occupy(in_use)
        else:
            in_use = None

        # 候补：周涛排队等 B201-001 的同一时段
        repo.insert_waitlist(
            WaitlistEntry(
                id=repo.next_id("waitlist", "W-", 7),
                user_id="S2023007",
                seat_id="B201-001",
                day=pending_day,
                start_slot=pending_slot,
                end_slot=pending_slot + 2,
                status=WaitlistStatus.QUEUED.value,
                seq=1,
                created_at=now - timedelta(minutes=5),
            )
        )

        # 投诉：一条待处理、一条已办结并评分
        repo.insert_complaint(
            Complaint(
                id=repo.next_id("complaint", "CP-", 6),
                user_id="S2023003",
                complaint_type=ComplaintType.NOISE.value,
                content="B201 靠窗位置有人长期打电话，影响自习。",
                seat_id="B201-002",
                status=ComplaintStatus.PENDING.value,
                created_at=now - timedelta(hours=2),
            )
        )
        repo.insert_complaint(
            Complaint(
                id=repo.next_id("complaint", "CP-", 6),
                user_id="S2023002",
                complaint_type=ComplaintType.FACILITY.value,
                content="A101-003 的插座没有电。",
                seat_id="A101-003",
                status=ComplaintStatus.RATED.value,
                feedback="已更换插座面板并现场测试。",
                resolved_by="A9001",
                resolved_at=now - timedelta(days=1, hours=3),
                rating=5,
                rating_comment="处理很及时",
                created_at=now - timedelta(days=1, hours=6),
            )
        )

        # 评价：两条已完成预约的评价
        completed_rows = conn.execute(
            "SELECT id, user_id, seat_id FROM reservations WHERE status='completed' ORDER BY id LIMIT 2"
        ).fetchall()
        for index, row in enumerate(completed_rows):
            repo.insert_review(
                Review(
                    id=repo.next_id("review", "RV-", 6),
                    reservation_id=row["id"],
                    user_id=row["user_id"],
                    seat_id=row["seat_id"],
                    rating=5 if index == 0 else 2,
                    tags=["安静", "插座稳定"] if index == 0 else ["灯光偏暗"],
                    content="环境安静，桌面干净。" if index == 0 else "灯光有点暗，希望能调整。",
                    reply="感谢反馈，已记录。" if index == 1 else None,
                    replied_by="A9001" if index == 1 else None,
                    replied_at=now - timedelta(days=1) if index == 1 else None,
                    created_at=now - timedelta(days=1, hours=2)
                    if index == 0
                    else now - timedelta(days=2, hours=1),
                )
            )

        repo.insert_audit(
            AuditLog(
                id=repo.next_id("audit", "A-", 7),
                actor_id="SYSTEM",
                actor_role="system",
                action="seed_demo",
                target_type="database",
                target_id="initial",
                before=None,
                after={"users": len(users), "seats": len(seats)},
                ip="127.0.0.1",
                created_at=now,
            )
        )

        counts["reservations"] = conn.execute(
            "SELECT COUNT(*) AS c FROM reservations"
        ).fetchone()["c"]
        counts["users"] = len(users)
        counts["seats"] = len(seats)
        counts["rooms"] = len(rooms)

    return counts


def seed_if_empty(db: Database, now: datetime, *, password: str | None = None) -> bool:
    db.migrate()
    with db.read() as conn:
        row = conn.execute("SELECT COUNT(*) AS c FROM users").fetchone()
        if row["c"]:
            return False
    seed_demo(db, now, password=password)
    return True


INITIAL_CREDENTIALS_FILENAME = "initial_admin_credentials.txt"
BOOTSTRAP_ADMIN_ID = "Y0001"


def initial_admin_credentials_path(db: Database) -> Path:
    """一次性初始凭据文件：与数据库同级的运行期数据目录，绝不进入 Git。"""
    return db.path.parent / INITIAL_CREDENTIALS_FILENAME


def write_initial_admin_credentials(
    db: Database, *, user_id: str, name: str, password: str, created_at: datetime
) -> Path:
    """把首次初始化的管理员凭据写入本地数据目录，便于部署者复制随机密码。

    文件包含明文密码，属于一次性运行期凭据：首次改密成功后由
    ``AuthService.change_password`` 自动删除，调用方不得写入仓库或日志。
    """
    target = initial_admin_credentials_path(db)
    body = "\n".join(
        [
            "智慧自习室预约管理系统 · 初始管理员凭据",
            "=" * 40,
            "",
            f"账号 ID：{user_id}",
            f"姓名：{name}",
            f"初始密码：{password}",
            f"生成时间：{created_at.strftime('%Y-%m-%d %H:%M:%S')}",
            "",
            "安全提示：",
            "1. 首次登录后系统将强制要求修改密码。",
            "2. 改密完成后请删除此文件以防凭据残留（系统会自动删除）。",
            "3. 请勿将本文件提交到版本库或发送给无关人员。",
            "",
        ]
    )
    target.write_text(body, encoding="utf-8")
    try:
        os.chmod(target, 0o600)
    except OSError:  # pragma: no cover - Windows 对权限位支持有限
        pass
    return target


def remove_initial_admin_credentials(db: Database) -> bool:
    """删除一次性初始凭据文件；文件不存在时返回 False。"""
    try:
        initial_admin_credentials_path(db).unlink()
        return True
    except FileNotFoundError:
        return False


def bootstrap_admin(
    db: Database,
    now: datetime,
    *,
    user_id: str = BOOTSTRAP_ADMIN_ID,
    name: str = "系统管理员",
    department: str = "信息化中心",
    password: str | None = None,
) -> str | None:
    """正式库首次初始化：创建一个随机密码的系统管理员。

    * 只在 ``users`` 为空时执行；重复调用是幂等的（返回 ``None``）。
    * 密码随机生成（或调用方显式传入），不在源码/日志中留痕。
    * 首次创建时在数据库同级的运行期数据目录写入一次性凭据文件
      （``initial_admin_credentials.txt``，已被 ``.gitignore`` 排除），
      首次改密成功后自动删除。
    * ``must_change_password=True``：首次登录后必须先修改密码。
    * 建号、默认参数与审计记录在**同一个写事务**中，失败不会留下半初始化数据。

    返回值仅在本次真正创建时返回明文密码，供初始化渠道一次性展示。
    """
    password = password or generate_initial_password()
    # 密码策略与 hash_password 一致，提前失败，避免事务内才抛错。
    if len(password) < 8:
        raise ValueError("初始密码至少 8 位")

    db.migrate()
    with db.read() as conn:
        if conn.execute("SELECT COUNT(*) AS c FROM users").fetchone()["c"]:
            return None

    with db.transaction() as conn:
        repo = SqliteRepository(conn)
        # 写事务内再次确认：并发首启时只有一个进程能建号。
        if conn.execute("SELECT COUNT(*) AS c FROM users").fetchone()["c"]:
            return None
        repo.save_params(ParamSet().snapshot(), "SYSTEM", now)
        repo.save_user(
            User(
                id=user_id,
                name=name,
                role=Role.SYS_ADMIN.value,
                department=department,
                class_name="",
                credit=100,
                password_hash=hash_password(password),
                must_change_password=True,
                created_at=now,
                updated_at=now,
            )
        )
        repo.insert_audit(
            AuditLog(
                id=repo.next_id("audit", "A-", 7),
                actor_id="SYSTEM",
                actor_role="system",
                action="bootstrap_admin",
                target_type="user",
                target_id=user_id,
                before=None,
                after={"role": Role.SYS_ADMIN.value, "must_change_password": True},
                ip="127.0.0.1",
                created_at=now,
            )
        )
    try:
        write_initial_admin_credentials(
            db, user_id=user_id, name=name, password=password, created_at=now
        )
    except OSError:
        # 凭据文件写失败不阻断初始化：密码仍由启动器弹窗/命令行展示一次。
        pass
    return password

