"""领域模型：枚举、实体、读模型与统一返回结构。

实体只表达业务含义，不依赖数据库与 HTTP；``as_dict`` 仅用于对外投影，
不包含密码哈希等敏感字段。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from enum import Enum
from typing import Any

from .timeutil import hhmm, minutes_of_slots


# ---------------------------------------------------------------------------
# 枚举
# ---------------------------------------------------------------------------
def _fmt(value: datetime | None) -> str | None:
    """统一对外时间格式，去掉微秒，避免 API 输出不一致。"""
    return value.strftime("%Y-%m-%d %H:%M:%S") if value else None


class StrEnum(str, Enum):
    def __str__(self) -> str:  # pragma: no cover - 便于日志
        return self.value


class Role(StrEnum):
    STUDENT = "student"
    TEACHER = "teacher"
    ROOM_ADMIN = "room_admin"
    SYS_ADMIN = "sys_admin"


ROLE_LABELS: dict[str, str] = {
    Role.STUDENT.value: "学生",
    Role.TEACHER.value: "教师",
    Role.ROOM_ADMIN.value: "自习室管理员",
    Role.SYS_ADMIN.value: "系统管理员",
}


class UserStatus(StrEnum):
    ACTIVE = "active"
    DISABLED = "disabled"


class LimitType(StrEnum):
    NONE = "none"
    CREDIT = "credit"
    CONSECUTIVE = "consecutive"
    BLACKLIST = "blacklist"


LIMIT_LABELS: dict[str, str] = {
    LimitType.NONE.value: "正常",
    LimitType.CREDIT.value: "信用分限制",
    LimitType.CONSECUTIVE.value: "连续违约限制",
    LimitType.BLACKLIST.value: "黑名单限制",
}


class SeatType(StrEnum):
    STANDARD = "standard"
    POWER = "power"
    QUIET = "quiet"
    DISCUSSION = "discussion"


SEAT_TYPE_LABELS: dict[str, str] = {
    SeatType.STANDARD.value: "普通位",
    SeatType.POWER.value: "电源位",
    SeatType.QUIET.value: "静音区",
    SeatType.DISCUSSION.value: "讨论间",
}


class SeatAdminStatus(StrEnum):
    AVAILABLE = "available"
    MAINTENANCE = "maintenance"
    DISABLED = "disabled"


class SeatState(StrEnum):
    """某一时段的派生状态（SM-02）。"""

    AVAILABLE = "available"
    RESERVED = "reserved"
    OCCUPIED = "occupied"
    HELD = "held"
    MAINTENANCE = "maintenance"
    DISABLED = "disabled"


SEAT_STATE_LABELS: dict[str, str] = {
    SeatState.AVAILABLE.value: "空闲",
    SeatState.RESERVED.value: "已预约",
    SeatState.OCCUPIED.value: "使用中",
    SeatState.HELD.value: "暂离保留",
    SeatState.MAINTENANCE.value: "维护中",
    SeatState.DISABLED.value: "停用",
}


class ReservationStatus(StrEnum):
    PENDING = "pending"
    IN_USE = "in_use"
    AWAY = "away"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    NO_SHOW = "no_show"
    RELEASED = "released"


RESERVATION_STATUS_LABELS: dict[str, str] = {
    ReservationStatus.PENDING.value: "待签到",
    ReservationStatus.IN_USE.value: "使用中",
    ReservationStatus.AWAY.value: "暂离",
    ReservationStatus.COMPLETED.value: "已完成",
    ReservationStatus.CANCELLED.value: "已取消",
    ReservationStatus.NO_SHOW.value: "已违约",
    ReservationStatus.RELEASED.value: "已释放",
}

#: 占用座位的状态（释放时必须归还时段）
OCCUPYING_STATUSES = frozenset(
    {ReservationStatus.PENDING.value, ReservationStatus.IN_USE.value, ReservationStatus.AWAY.value}
)

#: 计入 BR-02 当日额度的状态（SRS V-01 口径）
QUOTA_STATUSES = frozenset(
    {
        ReservationStatus.PENDING.value,
        ReservationStatus.IN_USE.value,
        ReservationStatus.AWAY.value,
        ReservationStatus.COMPLETED.value,
    }
)


class EndReason(StrEnum):
    NORMAL_CHECKOUT = "normal_checkout"
    TIMEOUT_CHECKOUT = "timeout_checkout"
    AWAY_TIMEOUT = "away_timeout"
    NO_SHOW = "no_show"
    SELF_CANCEL = "self_cancel"
    ADMIN_RELEASE = "admin_release"
    SEAT_BLOCKED = "seat_blocked"


END_REASON_LABELS: dict[str, str] = {
    EndReason.NORMAL_CHECKOUT.value: "正常签退",
    EndReason.TIMEOUT_CHECKOUT.value: "超时未签退",
    EndReason.AWAY_TIMEOUT.value: "暂离超时",
    EndReason.NO_SHOW.value: "未签到",
    EndReason.SELF_CANCEL.value: "本人取消",
    EndReason.ADMIN_RELEASE.value: "管理员释放",
    EndReason.SEAT_BLOCKED.value: "座位封禁取消",
}


class ViolationType(StrEnum):
    NO_SHOW = "no_show"
    AWAY_TIMEOUT = "away_timeout"
    LATE_CHECKOUT = "late_checkout"
    LATE_CANCEL = "late_cancel"
    FALSE_REPORT = "false_report"
    CREDENTIAL_MISUSE = "credential_misuse"
    MANUAL_INSPECTION = "manual_inspection"


VIOLATION_TYPE_LABELS: dict[str, str] = {
    ViolationType.NO_SHOW.value: "未签到",
    ViolationType.AWAY_TIMEOUT.value: "暂离超时",
    ViolationType.LATE_CHECKOUT.value: "超时未签退",
    ViolationType.LATE_CANCEL.value: "临近取消",
    ViolationType.FALSE_REPORT.value: "虚假上报",
    ViolationType.CREDENTIAL_MISUSE.value: "冒用凭证",
    ViolationType.MANUAL_INSPECTION.value: "现场巡检",
}


class ViolationStatus(StrEnum):
    ACTIVE = "active"
    APPEALING = "appealing"
    REVOKED = "revoked"


class ViolationSource(StrEnum):
    SYSTEM = "system"
    MANUAL = "manual"


class WaitlistStatus(StrEnum):
    QUEUED = "queued"
    NOTIFIED = "notified"
    CONFIRMED = "confirmed"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


class ComplaintType(StrEnum):
    OCCUPIED_SEAT = "occupied_seat"
    NOISE = "noise"
    FACILITY = "facility"
    MISJUDGED = "misjudged"


COMPLAINT_TYPE_LABELS: dict[str, str] = {
    ComplaintType.OCCUPIED_SEAT.value: "恶意占座",
    ComplaintType.NOISE.value: "环境喧哗",
    ComplaintType.FACILITY.value: "设施损坏",
    ComplaintType.MISJUDGED.value: "违约误判申诉",
}


class ComplaintStatus(StrEnum):
    PENDING = "pending"
    RESOLVED = "resolved"
    RATED = "rated"


class NotificationType(StrEnum):
    RESERVATION_CREATED = "reservation_created"
    CHECKIN_REMINDER = "checkin_reminder"
    VIOLATION = "violation"
    WAITLIST_AVAILABLE = "waitlist_available"
    SEAT_BLOCKED = "seat_blocked"
    ADMIN_RELEASED = "admin_released"
    APPEAL_RESULT = "appeal_result"
    COMPLAINT_RESULT = "complaint_result"
    CREDIT_CHANGED = "credit_changed"


class NotificationChannel(StrEnum):
    INBOX = "inbox"
    SMS = "sms"
    WECHAT = "wechat"


class ReservationSource(StrEnum):
    SELF = "self"
    WAITLIST = "waitlist"


# ---------------------------------------------------------------------------
# 实体
# ---------------------------------------------------------------------------
@dataclass(slots=True)
class User:
    id: str
    name: str
    role: str
    department: str = ""
    class_name: str = ""
    credit: int = 100
    status: str = UserStatus.ACTIVE.value
    limit_type: str = LimitType.NONE.value
    limit_until: datetime | None = None
    consecutive_no_show: int = 0
    password_hash: str = ""
    must_change_password: bool = False
    created_at: datetime | None = None
    updated_at: datetime | None = None

    @property
    def is_active(self) -> bool:
        return self.status == UserStatus.ACTIVE.value

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "role": self.role,
            "role_label": ROLE_LABELS.get(self.role, self.role),
            "department": self.department,
            "class_name": self.class_name,
            "credit": self.credit,
            "status": self.status,
            "limit_type": self.limit_type,
            "limit_label": LIMIT_LABELS.get(self.limit_type, self.limit_type),
            "limit_until": _fmt(self.limit_until) if self.limit_until else None,
            "consecutive_no_show": self.consecutive_no_show,
            "must_change_password": self.must_change_password,
        }


@dataclass(slots=True)
class Room:
    id: str
    name: str
    location: str = ""
    status: str = "open"
    open_slot: int = 14  # 07:00
    close_slot: int = 46  # 23:00
    created_at: datetime | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "location": self.location,
            "status": self.status,
            "open_time": hhmm(self.open_slot),
            "close_time": hhmm(self.close_slot),
        }


@dataclass(slots=True)
class Seat:
    id: str
    room_id: str
    seat_type: str = SeatType.STANDARD.value
    capacity: int = 1
    admin_status: str = SeatAdminStatus.AVAILABLE.value
    has_power: bool = False
    is_window: bool = False
    note: str = ""
    created_at: datetime | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "room_id": self.room_id,
            "type": self.seat_type,
            "type_label": SEAT_TYPE_LABELS.get(self.seat_type, self.seat_type),
            "capacity": self.capacity,
            "has_power": self.has_power,
            "is_window": self.is_window,
            "note": self.note,
        }


@dataclass(slots=True)
class Reservation:
    id: str
    user_id: str
    seat_id: str
    day: date
    start_slot: int
    end_slot: int
    start_at: datetime
    end_at: datetime
    status: str = ReservationStatus.PENDING.value
    source: str = ReservationSource.SELF.value
    created_at: datetime | None = None
    updated_at: datetime | None = None
    checked_in_at: datetime | None = None
    checkin_deadline: datetime | None = None
    away_at: datetime | None = None
    away_deadline: datetime | None = None
    checked_out_at: datetime | None = None
    end_reason: str | None = None
    credential_code: str | None = None
    credential_issued_at: datetime | None = None
    credential_used: bool = False
    policy: dict[str, str] = field(default_factory=dict)
    version: int = 1

    @property
    def minutes(self) -> int:
        return minutes_of_slots(self.start_slot, self.end_slot)

    @property
    def start_time(self) -> str:
        return hhmm(self.start_slot)

    @property
    def end_time(self) -> str:
        return hhmm(self.end_slot)

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "user_id": self.user_id,
            "seat_id": self.seat_id,
            "date": self.day.isoformat(),
            "start": self.start_time,
            "end": self.end_time,
            "start_slot": self.start_slot,
            "end_slot": self.end_slot,
            "minutes": self.minutes,
            "status": self.status,
            "status_label": RESERVATION_STATUS_LABELS.get(self.status, self.status),
            "source": self.source,
            "created_at": _fmt(self.created_at) if self.created_at else None,
            "checked_in_at": _fmt(self.checked_in_at) if self.checked_in_at else None,
            "away_deadline": _fmt(self.away_deadline) if self.away_deadline else None,
            "checked_out_at": _fmt(self.checked_out_at)
            if self.checked_out_at
            else None,
            "end_reason": self.end_reason,
            "end_reason_label": END_REASON_LABELS.get(self.end_reason or "", None),
            "checkin_opens_at": _fmt(
                self.start_at
                - timedelta(minutes=int((self.policy or {}).get("checkin.before_minutes", 15)))
            ),
            "checkin_deadline": _fmt(self.checkin_deadline)
            if self.checkin_deadline
            else None,
        }


@dataclass(slots=True)
class ReservationView:
    """列表读模型：带座位/自习室/姓名，供界面直接展示。"""

    reservation: Reservation
    seat_id: str
    room_id: str
    room_name: str
    user_name: str
    has_review: bool = False

    def as_dict(self) -> dict[str, Any]:
        data = self.reservation.as_dict()
        data.update(
            {
                "seat_id": self.seat_id,
                "room_id": self.room_id,
                "room_name": self.room_name,
                "user_name": self.user_name,
                "has_review": self.has_review,
            }
        )
        return data


@dataclass(slots=True)
class SeatView:
    seat: Seat
    room_name: str
    state: str
    state_label: str
    available: bool
    holder_reservation_id: str | None = None

    def as_dict(self) -> dict[str, Any]:
        data = self.seat.as_dict()
        data.update(
            {
                "room_name": self.room_name,
                "state": self.state,
                "state_label": self.state_label,
                "available": self.available,
                "holder_reservation_id": self.holder_reservation_id,
            }
        )
        return data


@dataclass(slots=True)
class Violation:
    id: str
    reservation_id: str
    user_id: str
    violation_type: str
    points: int
    source: str = ViolationSource.SYSTEM.value
    status: str = ViolationStatus.ACTIVE.value
    occurred_at: datetime | None = None
    note: str = ""
    operator_id: str | None = None
    created_at: datetime | None = None
    appeal_reason: str | None = None
    appeal_at: datetime | None = None
    review_note: str | None = None
    reviewed_by: str | None = None
    reviewed_at: datetime | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "reservation_id": self.reservation_id,
            "user_id": self.user_id,
            "type": self.violation_type,
            "type_label": VIOLATION_TYPE_LABELS.get(self.violation_type, self.violation_type),
            "points": self.points,
            "source": self.source,
            "status": self.status,
            "occurred_at": _fmt(self.occurred_at) if self.occurred_at else None,
            "note": self.note,
            "operator_id": self.operator_id,
            "appeal_reason": self.appeal_reason,
            "appeal_at": _fmt(self.appeal_at) if self.appeal_at else None,
            "review_note": self.review_note,
            "reviewed_by": self.reviewed_by,
        }


@dataclass(slots=True)
class WaitlistEntry:
    id: str
    user_id: str
    seat_id: str
    day: date
    start_slot: int
    end_slot: int
    status: str
    seq: int
    created_at: datetime | None = None
    notified_at: datetime | None = None
    confirm_deadline: datetime | None = None
    defer_count: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "user_id": self.user_id,
            "seat_id": self.seat_id,
            "date": self.day.isoformat(),
            "start": hhmm(self.start_slot),
            "end": hhmm(self.end_slot),
            "status": self.status,
            "seq": self.seq,
            "created_at": _fmt(self.created_at) if self.created_at else None,
            "confirm_deadline": _fmt(self.confirm_deadline)
            if self.confirm_deadline
            else None,
        }


@dataclass(slots=True)
class Notification:
    id: str
    user_id: str
    notification_type: str
    content: str
    channel: str = NotificationChannel.INBOX.value
    status: str = "sent"
    created_at: datetime | None = None
    read_at: datetime | None = None
    dedupe_key: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": self.notification_type,
            "content": self.content,
            "channel": self.channel,
            "status": self.status,
            "created_at": _fmt(self.created_at) if self.created_at else None,
            "read_at": _fmt(self.read_at) if self.read_at else None,
        }


@dataclass(slots=True)
class Complaint:
    id: str
    user_id: str
    complaint_type: str
    content: str
    seat_id: str | None = None
    reservation_id: str | None = None
    status: str = ComplaintStatus.PENDING.value
    feedback: str | None = None
    resolved_by: str | None = None
    resolved_at: datetime | None = None
    rating: int | None = None
    rating_comment: str | None = None
    created_at: datetime | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "user_id": self.user_id,
            "type": self.complaint_type,
            "type_label": COMPLAINT_TYPE_LABELS.get(self.complaint_type, self.complaint_type),
            "content": self.content,
            "seat_id": self.seat_id,
            "reservation_id": self.reservation_id,
            "status": self.status,
            "feedback": self.feedback,
            "resolved_by": self.resolved_by,
            "resolved_at": _fmt(self.resolved_at) if self.resolved_at else None,
            "rating": self.rating,
            "rating_comment": self.rating_comment,
            "created_at": _fmt(self.created_at) if self.created_at else None,
        }


@dataclass(slots=True)
class Review:
    id: str
    reservation_id: str
    user_id: str
    seat_id: str
    rating: int
    tags: list[str] = field(default_factory=list)
    content: str = ""
    reply: str | None = None
    replied_by: str | None = None
    replied_at: datetime | None = None
    created_at: datetime | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "reservation_id": self.reservation_id,
            "user_id": self.user_id,
            "seat_id": self.seat_id,
            "rating": self.rating,
            "tags": self.tags,
            "content": self.content,
            "reply": self.reply,
            "replied_by": self.replied_by,
            "created_at": _fmt(self.created_at) if self.created_at else None,
        }


@dataclass(slots=True)
class BlacklistRecord:
    id: str
    user_id: str
    reason: str
    source: str
    start_at: datetime
    end_at: datetime
    released_at: datetime | None = None
    released_by: str | None = None
    created_by: str | None = None
    created_at: datetime | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "user_id": self.user_id,
            "reason": self.reason,
            "source": self.source,
            "start_at": _fmt(self.start_at),
            "end_at": _fmt(self.end_at),
            "released_at": _fmt(self.released_at) if self.released_at else None,
            "released_by": self.released_by,
        }


@dataclass(slots=True)
class SeatBlock:
    id: str
    seat_id: str
    reason: str
    start_at: datetime
    end_at: datetime
    created_by: str
    created_at: datetime | None = None
    released_at: datetime | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "seat_id": self.seat_id,
            "reason": self.reason,
            "start_at": _fmt(self.start_at),
            "end_at": _fmt(self.end_at),
            "created_by": self.created_by,
            "released_at": _fmt(self.released_at) if self.released_at else None,
        }


@dataclass(slots=True)
class AuditLog:
    id: str
    actor_id: str
    actor_role: str
    action: str
    target_type: str
    target_id: str
    before: dict[str, Any] | None = None
    after: dict[str, Any] | None = None
    ip: str = ""
    created_at: datetime | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "actor_id": self.actor_id,
            "actor_role": self.actor_role,
            "action": self.action,
            "target_type": self.target_type,
            "target_id": self.target_id,
            "before": self.before,
            "after": self.after,
            "ip": self.ip,
            "created_at": _fmt(self.created_at) if self.created_at else None,
        }


# ---------------------------------------------------------------------------
# 上下文与返回结构
# ---------------------------------------------------------------------------
@dataclass(slots=True, frozen=True)
class Actor:
    id: str
    name: str
    role: Role
    class_name: str = ""
    department: str = ""

    @property
    def is_admin(self) -> bool:
        return self.role in {Role.ROOM_ADMIN, Role.SYS_ADMIN}


@dataclass(slots=True)
class Result:
    ok: bool
    code: str = "OK"
    message: str = ""
    rule: str | None = None
    data: Any = None

    @classmethod
    def success(cls, data: Any = None, message: str = "操作成功", code: str = "OK") -> "Result":
        return cls(True, code, message, None, data)

    @classmethod
    def failure(
        cls, code: str, message: str, rule: str | None = None, data: Any = None
    ) -> "Result":
        return cls(False, code, message, rule, data)


@dataclass(slots=True)
class Page:
    items: list[Any]
    total: int
    page: int
    page_size: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "items": [item.as_dict() if hasattr(item, "as_dict") else item for item in self.items],
            "total": self.total,
            "page": self.page,
            "page_size": self.page_size,
        }


def parse_slot_range(start: time, end: time) -> tuple[int, int]:
    from .timeutil import slot_of_time

    return slot_of_time(start), slot_of_time(end)
