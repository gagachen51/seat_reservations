"""公共接口：健康检查、元数据、消息中心。"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from ...domain.models import (
    COMPLAINT_TYPE_LABELS,
    RESERVATION_STATUS_LABELS,
    ROLE_LABELS,
    SEAT_TYPE_LABELS,
    VIOLATION_TYPE_LABELS,
    Actor,
)
from ...storage.repository import SqliteRepository
from ..deps import get_actor, get_context, get_session, read_engine, render, render_page, success, write_engine
from ...service.auth import AuthSession
from ...service.container import AppContext
from ...domain.engine import DomainEngine

router = APIRouter(tags=["common"])


class NotificationsReadIn(BaseModel):
    ids: list[str] | None = None


@router.get("/health", summary="健康检查")
def health(ctx: AppContext = Depends(get_context)) -> JSONResponse:
    data = ctx.health()
    status_code = 200 if data["status"] == "ok" else 503
    return JSONResponse({"ok": data["status"] == "ok", "data": data}, status_code=status_code)


@router.get("/meta", summary="界面元数据")
def meta(
    session: AuthSession = Depends(get_session),
    ctx: AppContext = Depends(get_context),
) -> JSONResponse:
    with ctx.db.read() as conn:
        repo = SqliteRepository(conn)
        rooms = [room.as_dict() for room in repo.list_rooms()]
        params = repo.load_params()
    user = session.user
    return success(
        {
            "server_time": datetime.now().isoformat(sep=" ", timespec="seconds"),
            "today": datetime.now().date().isoformat(),
            "user": user.as_dict(),
            "roles": ROLE_LABELS,
            "rooms": rooms,
            "seat_types": SEAT_TYPE_LABELS,
            "reservation_statuses": RESERVATION_STATUS_LABELS,
            "violation_types": VIOLATION_TYPE_LABELS,
            "complaint_types": COMPLAINT_TYPE_LABELS,
            "rules": {
                "booking_window_days": params.int("booking.window_days"),
                "min_minutes": params.int("booking.min_minutes"),
                "max_minutes": params.int("booking.max_minutes"),
                "daily_max_count": params.int("booking.daily_max_count"),
                "daily_max_minutes": params.int("booking.daily_max_minutes"),
                "checkin_before_minutes": params.int("checkin.before_minutes"),
                "checkin_after_minutes": params.int("checkin.after_minutes"),
                "away_max_minutes": params.int("away.max_minutes"),
                "cancel_free_minutes": params.int("cancel.free_minutes"),
                "waitlist_confirm_minutes": params.int("waitlist.confirm_minutes"),
                "credit_threshold": params.int("credit.threshold"),
                "credential_ttl_seconds": params.int("checkin.credential_ttl_seconds"),
            },
        }
    )


@router.get("/notifications", summary="我的消息")
def notifications(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    actor: Actor = Depends(get_actor),
    engine: DomainEngine = Depends(read_engine),
) -> JSONResponse:
    return render_page(engine.list_notifications(actor, page=page, page_size=page_size))


@router.get("/notifications/unread", summary="未读消息数")
def unread_notifications(
    actor: Actor = Depends(get_actor),
    engine: DomainEngine = Depends(read_engine),
) -> JSONResponse:
    return success({"count": engine.unread_notification_count(actor)})


@router.post("/notifications/read", summary="标记消息已读")
def read_notifications(
    payload: NotificationsReadIn,
    actor: Actor = Depends(get_actor),
    engine: DomainEngine = Depends(write_engine),
) -> JSONResponse:
    count = engine.mark_notifications_read(actor, payload.ids, datetime.now())
    return success({"updated": count}, f"已标记 {count} 条为已读")
