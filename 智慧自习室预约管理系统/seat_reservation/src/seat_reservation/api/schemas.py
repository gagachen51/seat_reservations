"""API 请求模型（Pydantic v2）。

只做“形状/类型”校验；业务规则一律留给领域引擎，避免规则散落到路由层。
"""

from __future__ import annotations

from datetime import date, datetime, time

from pydantic import BaseModel, Field


class LoginIn(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=128)


class ChangePasswordIn(BaseModel):
    old_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=8, max_length=128)


class ReservationIn(BaseModel):
    seat_id: str
    date: date
    start: time
    end: time


class CheckInIn(BaseModel):
    code: str | None = None


class WaitlistIn(BaseModel):
    seat_id: str
    date: date
    start: time
    end: time


class AppealIn(BaseModel):
    reason: str = Field(min_length=2, max_length=500)


class ComplaintIn(BaseModel):
    type: str
    content: str = Field(min_length=5, max_length=1000)
    seat_id: str | None = None


class ComplaintResolveIn(BaseModel):
    feedback: str = Field(min_length=2, max_length=1000)
    linked_reservation_id: str | None = None
    linked_mode: str | None = None


class ComplaintRateIn(BaseModel):
    rating: int = Field(ge=1, le=5)
    comment: str = Field(default="", max_length=500)


class ReviewIn(BaseModel):
    reservation_id: str
    rating: int = Field(ge=1, le=5)
    tags: list[str] = Field(default_factory=list, max_length=5)
    content: str = Field(default="", max_length=500)


class ReviewReplyIn(BaseModel):
    reply: str = Field(min_length=2, max_length=500)
    block_seat: bool = False


class AppealReviewIn(BaseModel):
    """管理员审核违规申诉：decision=approve 撤销并回退信用分，reject 维持原判。"""

    decision: str = Field(pattern="^(approve|reject)$")
    note: str = Field(min_length=2, max_length=500)


class ManualViolationIn(BaseModel):
    reservation_id: str
    mode: str = Field(pattern="^(warn|release)$")
    note: str = Field(min_length=4, max_length=500)


class SeatBlockIn(BaseModel):
    seat_id: str
    reason: str = Field(min_length=2, max_length=200)
    start_at: datetime
    end_at: datetime


class RoomIn(BaseModel):
    id: str = Field(min_length=1, max_length=32)
    name: str = Field(min_length=1, max_length=64)
    location: str = Field(default="", max_length=128)
    open_time: time = time(7, 0)
    close_time: time = time(23, 0)
    status: str = Field(default="open", pattern="^(open|closed)$")


class RoomUpdateIn(BaseModel):
    name: str | None = Field(default=None, max_length=64)
    location: str | None = Field(default=None, max_length=128)
    open_time: time | None = None
    close_time: time | None = None
    status: str | None = Field(default=None, pattern="^(open|closed)$")


class SeatIn(BaseModel):
    id: str = Field(min_length=1, max_length=32)
    room_id: str = Field(min_length=1, max_length=32)
    type: str = Field(default="standard", pattern="^(standard|power|quiet|discussion)$")
    capacity: int = Field(default=1, ge=1, le=20)
    has_power: bool = False
    is_window: bool = False
    note: str = Field(default="", max_length=200)


class SeatUpdateIn(BaseModel):
    room_id: str | None = Field(default=None, max_length=32)
    type: str | None = Field(default=None, pattern="^(standard|power|quiet|discussion)$")
    capacity: int | None = Field(default=None, ge=1, le=20)
    admin_status: str | None = Field(default=None, pattern="^(available|maintenance|disabled)$")
    has_power: bool | None = None
    is_window: bool | None = None
    note: str | None = Field(default=None, max_length=200)


class ReasonIn(BaseModel):
    reason: str = Field(min_length=2, max_length=500)


class BlacklistIn(BaseModel):
    user_id: str
    reason: str = Field(min_length=2, max_length=500)
    days: int | None = Field(default=None, ge=1, le=365)


class ParamIn(BaseModel):
    value: str


class RoleIn(BaseModel):
    role: str


class AcsCheckInIn(BaseModel):
    code: str = Field(min_length=4, max_length=32)
