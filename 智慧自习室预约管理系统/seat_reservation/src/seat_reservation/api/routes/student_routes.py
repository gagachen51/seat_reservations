"""学生端接口：查座位、预约、签到/暂离/签退、候补、信用与反馈。"""

from __future__ import annotations

from datetime import date, datetime, time

from fastapi import APIRouter, Depends, Header, Query, Request
from fastapi.responses import JSONResponse

from ...domain.engine import DomainEngine
from ...domain.models import Actor
from ...errors import Forbidden
from ...security import constant_time_equals
from ...service.container import AppContext
from ..deps import (
    get_actor,
    get_context,
    read_engine,
    render,
    render_page,
    success,
    write_engine,
)
from ..schemas import (
    AcsCheckInIn,
    AppealIn,
    CheckInIn,
    ComplaintIn,
    ComplaintRateIn,
    ReservationIn,
    ReviewIn,
    WaitlistIn,
)

router = APIRouter(tags=["student"])


@router.get("/seats", summary="查询座位")
def list_seats(
    day: date = Query(alias="date"),
    start: time = Query(),
    end: time = Query(),
    room_id: str | None = None,
    seat_type: str | None = None,
    has_power: bool | None = None,
    is_window: bool | None = None,
    only_available: bool = False,
    actor: Actor = Depends(get_actor),
    engine: DomainEngine = Depends(read_engine),
) -> JSONResponse:
    seats = engine.list_seats(
        actor,
        day,
        start,
        end,
        room_id=room_id,
        seat_type=seat_type,
        has_power=has_power,
        is_window=is_window,
        only_available=only_available,
    )
    return success({"items": seats, "total": len(seats), "date": day.isoformat()})


@router.get("/reservations", summary="预约列表")
def list_reservations(
    status: str | None = None,
    day: date | None = Query(default=None, alias="date"),
    date_from: date | None = None,
    date_to: date | None = None,
    seat_id: str | None = None,
    room_id: str | None = None,
    keyword: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    actor: Actor = Depends(get_actor),
    engine: DomainEngine = Depends(read_engine),
) -> JSONResponse:
    return render_page(
        engine.list_reservations(
            actor,
            status=status,
            day=day,
            date_from=date_from,
            date_to=date_to,
            seat_id=seat_id,
            room_id=room_id,
            keyword=keyword,
            page=page,
            page_size=page_size,
        )
    )


@router.get("/reservations/{reservation_id}", summary="预约详情")
def get_reservation(
    reservation_id: str,
    actor: Actor = Depends(get_actor),
    engine: DomainEngine = Depends(read_engine),
) -> JSONResponse:
    return success(engine.get_reservation(actor, reservation_id))


@router.post("/reservations", status_code=201, summary="创建预约")
def create_reservation(
    payload: ReservationIn,
    actor: Actor = Depends(get_actor),
    engine: DomainEngine = Depends(write_engine),
) -> JSONResponse:
    result = engine.create_reservation(
        actor, payload.seat_id, payload.date, payload.start, payload.end, datetime.now()
    )
    return render(result, status_code=201)


@router.post("/reservations/{reservation_id}/credential", summary="签发签到凭证")
def issue_credential(
    reservation_id: str,
    actor: Actor = Depends(get_actor),
    engine: DomainEngine = Depends(write_engine),
) -> JSONResponse:
    return render(engine.issue_credential(actor, reservation_id, datetime.now()))


@router.post("/reservations/{reservation_id}/check-in", summary="扫码签到")
def check_in(
    reservation_id: str,
    payload: CheckInIn,
    actor: Actor = Depends(get_actor),
    engine: DomainEngine = Depends(write_engine),
) -> JSONResponse:
    return render(engine.check_in(actor, reservation_id, payload.code, datetime.now()))


@router.post("/reservations/{reservation_id}/check-out", summary="签退")
def check_out(
    reservation_id: str,
    actor: Actor = Depends(get_actor),
    engine: DomainEngine = Depends(write_engine),
) -> JSONResponse:
    return render(engine.check_out(actor, reservation_id, datetime.now()))


@router.post("/reservations/{reservation_id}/away", summary="暂时离开")
def start_away(
    reservation_id: str,
    actor: Actor = Depends(get_actor),
    engine: DomainEngine = Depends(write_engine),
) -> JSONResponse:
    return render(engine.start_away(actor, reservation_id, datetime.now()))


@router.post("/reservations/{reservation_id}/resume", summary="返座")
def resume_use(
    reservation_id: str,
    actor: Actor = Depends(get_actor),
    engine: DomainEngine = Depends(write_engine),
) -> JSONResponse:
    return render(engine.resume_use(actor, reservation_id, datetime.now()))


@router.post("/reservations/{reservation_id}/cancel", summary="取消预约")
def cancel_reservation(
    reservation_id: str,
    actor: Actor = Depends(get_actor),
    engine: DomainEngine = Depends(write_engine),
) -> JSONResponse:
    return render(engine.cancel_reservation(actor, reservation_id, datetime.now()))


@router.get("/waitlist", summary="我的候补")
def list_waitlist(
    status: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    actor: Actor = Depends(get_actor),
    engine: DomainEngine = Depends(read_engine),
) -> JSONResponse:
    return render_page(engine.list_waitlist(actor, status=status, page=page, page_size=page_size))


@router.post("/waitlist", status_code=201, summary="加入候补")
def join_waitlist(
    payload: WaitlistIn,
    actor: Actor = Depends(get_actor),
    engine: DomainEngine = Depends(write_engine),
) -> JSONResponse:
    result = engine.join_waitlist(
        actor, payload.seat_id, payload.date, payload.start, payload.end, datetime.now()
    )
    return render(result, status_code=201)


@router.post("/waitlist/{entry_id}/confirm", summary="确认候补")
def confirm_waitlist(
    entry_id: str,
    actor: Actor = Depends(get_actor),
    engine: DomainEngine = Depends(write_engine),
) -> JSONResponse:
    return render(engine.confirm_waitlist(actor, entry_id, datetime.now()))


@router.get("/me/dashboard", summary="个人中心")
def student_dashboard(
    actor: Actor = Depends(get_actor),
    engine: DomainEngine = Depends(write_engine),
) -> JSONResponse:
    # _booking_limit 会在限制到期时顺手落库解除限制，因此这里必须用写事务，
    # 不能放在 read_engine 的只读事务里。
    return success(engine.student_dashboard(actor, datetime.now()))


@router.get("/me/credit-logs", summary="信用分流水")
def list_credit_logs(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    actor: Actor = Depends(get_actor),
    engine: DomainEngine = Depends(read_engine),
) -> JSONResponse:
    return render_page(engine.list_credit_logs(actor, page=page, page_size=page_size))


@router.get("/violations", summary="违规记录")
def list_violations(
    status: str | None = None,
    source: str | None = None,
    violation_type: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    keyword: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    actor: Actor = Depends(get_actor),
    engine: DomainEngine = Depends(read_engine),
) -> JSONResponse:
    return render_page(
        engine.list_violations(
            actor,
            status=status,
            source=source,
            violation_type=violation_type,
            date_from=date_from,
            date_to=date_to,
            keyword=keyword,
            page=page,
            page_size=page_size,
        )
    )


@router.post("/violations/{violation_id}/appeal", summary="提交申诉")
def submit_appeal(
    violation_id: str,
    payload: AppealIn,
    actor: Actor = Depends(get_actor),
    engine: DomainEngine = Depends(write_engine),
) -> JSONResponse:
    return render(engine.submit_appeal(actor, violation_id, payload.reason, datetime.now()))


@router.get("/complaints", summary="投诉列表")
def list_complaints(
    status: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    actor: Actor = Depends(get_actor),
    engine: DomainEngine = Depends(read_engine),
) -> JSONResponse:
    return render_page(engine.list_complaints(actor, status=status, page=page, page_size=page_size))


@router.post("/complaints", status_code=201, summary="提交投诉")
def submit_complaint(
    payload: ComplaintIn,
    actor: Actor = Depends(get_actor),
    engine: DomainEngine = Depends(write_engine),
) -> JSONResponse:
    result = engine.submit_complaint(
        actor, payload.type, payload.content, payload.seat_id, datetime.now()
    )
    return render(result, status_code=201)


@router.post("/complaints/{complaint_id}/rate", summary="评价投诉处理")
def rate_complaint(
    complaint_id: str,
    payload: ComplaintRateIn,
    actor: Actor = Depends(get_actor),
    engine: DomainEngine = Depends(write_engine),
) -> JSONResponse:
    return render(
        engine.rate_complaint(
            actor, complaint_id, payload.rating, payload.comment, datetime.now()
        )
    )


@router.get("/reviews", summary="座位评价")
def list_reviews(
    seat_id: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    actor: Actor = Depends(get_actor),
    engine: DomainEngine = Depends(read_engine),
) -> JSONResponse:
    return render_page(
        engine.list_reviews(actor, seat_id=seat_id, page=page, page_size=page_size)
    )


@router.post("/reviews", status_code=201, summary="提交评价")
def submit_review(
    payload: ReviewIn,
    actor: Actor = Depends(get_actor),
    engine: DomainEngine = Depends(write_engine),
) -> JSONResponse:
    result = engine.submit_review(
        actor,
        payload.reservation_id,
        payload.rating,
        payload.tags,
        payload.content,
        datetime.now(),
    )
    return render(result, status_code=201)


@router.post("/acs/check-in", summary="闸机/扫码设备签到")
def acs_check_in(
    payload: AcsCheckInIn,
    request: Request,
    x_acs_token: str | None = Header(default=None, alias="X-ACS-Token"),
    ctx: AppContext = Depends(get_context),
    engine: DomainEngine = Depends(write_engine),
) -> JSONResponse:
    token = ctx.settings.acs_token
    if not token or not constant_time_equals(token, x_acs_token):
        raise Forbidden("设备未授权", rule="IF-02")
    return render(engine.check_in_by_credential(payload.code, datetime.now(), method="acs"))
