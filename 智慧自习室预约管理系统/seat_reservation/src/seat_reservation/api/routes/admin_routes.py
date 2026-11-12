"""管理端接口：运营、处置、信用、参数、审计与导出。"""

from __future__ import annotations

import csv
import io
from datetime import date, datetime

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse, Response

from ...domain.engine import DomainEngine
from ...domain.models import Actor, Role
from ...errors import Forbidden
from ...service.container import AppContext
from ..deps import (
    client_ip,
    get_actor,
    get_context,
    read_engine,
    render,
    render_page,
    success,
    write_engine,
)
from ..schemas import (
    AppealReviewIn,
    BlacklistIn,
    ComplaintResolveIn,
    ManualViolationIn,
    ParamIn,
    ReasonIn,
    ReviewReplyIn,
    RoleIn,
    RoomIn,
    RoomUpdateIn,
    SeatBlockIn,
    SeatIn,
    SeatUpdateIn,
)

router = APIRouter(tags=["admin"])


@router.get("/admin/stats", summary="运营看板")
def admin_stats(
    room_id: str | None = None,
    actor: Actor = Depends(get_actor),
    engine: DomainEngine = Depends(read_engine),
) -> JSONResponse:
    return success(engine.admin_stats(actor, room_id, datetime.now()))


@router.get("/teacher/dashboard", summary="教师本班汇总")
def teacher_dashboard(
    actor: Actor = Depends(get_actor),
    engine: DomainEngine = Depends(read_engine),
) -> JSONResponse:
    return success(engine.teacher_dashboard(actor, datetime.now()))


@router.get("/admin/reservations", summary="预约综合查询")
def search_reservations(
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


@router.post("/admin/reservations/{reservation_id}/force-release", summary="强制释放")
def force_release(
    reservation_id: str,
    payload: ReasonIn,
    request: Request,
    actor: Actor = Depends(get_actor),
    engine: DomainEngine = Depends(write_engine),
) -> JSONResponse:
    return render(
        engine.force_release(
            actor, reservation_id, payload.reason, datetime.now(), ip=client_ip(request)
        )
    )


@router.get("/admin/rooms", summary="自习室列表")
def list_rooms_admin(
    actor: Actor = Depends(get_actor),
    engine: DomainEngine = Depends(read_engine),
) -> JSONResponse:
    return success({"items": engine.list_rooms_admin(actor)})


@router.post("/admin/rooms", status_code=201, summary="新增自习室")
def create_room(
    payload: RoomIn,
    request: Request,
    actor: Actor = Depends(get_actor),
    engine: DomainEngine = Depends(write_engine),
) -> JSONResponse:
    result = engine.create_room(
        actor,
        payload.id,
        payload.name,
        payload.location,
        payload.open_time,
        payload.close_time,
        payload.status,
        datetime.now(),
        ip=client_ip(request),
    )
    return render(result, status_code=201)


@router.put("/admin/rooms/{room_id}", summary="修改自习室")
def update_room(
    room_id: str,
    payload: RoomUpdateIn,
    request: Request,
    actor: Actor = Depends(get_actor),
    engine: DomainEngine = Depends(write_engine),
) -> JSONResponse:
    return render(
        engine.update_room(
            actor,
            room_id,
            name=payload.name,
            location=payload.location,
            status=payload.status,
            open_time=payload.open_time,
            close_time=payload.close_time,
            now=datetime.now(),
            ip=client_ip(request),
        )
    )


@router.get("/admin/seats", summary="座位列表")
def list_seats_admin(
    room_id: str | None = None,
    actor: Actor = Depends(get_actor),
    engine: DomainEngine = Depends(read_engine),
) -> JSONResponse:
    return success({"items": engine.list_seats_admin(actor, room_id=room_id)})


@router.post("/admin/seats", status_code=201, summary="新增座位")
def create_seat(
    payload: SeatIn,
    request: Request,
    actor: Actor = Depends(get_actor),
    engine: DomainEngine = Depends(write_engine),
) -> JSONResponse:
    result = engine.create_seat(
        actor,
        payload.id,
        payload.room_id,
        payload.type,
        payload.capacity,
        payload.has_power,
        payload.is_window,
        payload.note,
        datetime.now(),
        ip=client_ip(request),
    )
    return render(result, status_code=201)


@router.put("/admin/seats/{seat_id}", summary="修改座位")
def update_seat(
    seat_id: str,
    payload: SeatUpdateIn,
    request: Request,
    actor: Actor = Depends(get_actor),
    engine: DomainEngine = Depends(write_engine),
) -> JSONResponse:
    return render(
        engine.update_seat(
            actor,
            seat_id,
            room_id=payload.room_id,
            seat_type=payload.type,
            capacity=payload.capacity,
            admin_status=payload.admin_status,
            has_power=payload.has_power,
            is_window=payload.is_window,
            note=payload.note,
            now=datetime.now(),
            ip=client_ip(request),
        )
    )


@router.post("/admin/manual-violation", summary="现场巡检登记违约")
def manual_violation(
    payload: ManualViolationIn,
    request: Request,
    actor: Actor = Depends(get_actor),
    engine: DomainEngine = Depends(write_engine),
) -> JSONResponse:
    return render(
        engine.manual_violation(
            actor,
            payload.reservation_id,
            payload.mode,
            payload.note,
            datetime.now(),
            ip=client_ip(request),
        )
    )


@router.post("/admin/seat-blocks", status_code=201, summary="封禁座位")
def block_seat(
    payload: SeatBlockIn,
    request: Request,
    actor: Actor = Depends(get_actor),
    engine: DomainEngine = Depends(write_engine),
) -> JSONResponse:
    result = engine.block_seat(
        actor,
        payload.seat_id,
        payload.reason,
        payload.start_at,
        payload.end_at,
        datetime.now(),
        ip=client_ip(request),
    )
    return render(result, status_code=201)


@router.delete("/admin/seat-blocks/{seat_id}", summary="解封座位")
def unblock_seat(
    seat_id: str,
    request: Request,
    actor: Actor = Depends(get_actor),
    engine: DomainEngine = Depends(write_engine),
) -> JSONResponse:
    return render(
        engine.unblock_seat(actor, seat_id, datetime.now(), ip=client_ip(request))
    )


@router.get("/admin/complaints", summary="投诉处理列表")
def list_complaints(
    status: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    actor: Actor = Depends(get_actor),
    engine: DomainEngine = Depends(read_engine),
) -> JSONResponse:
    return render_page(engine.list_complaints(actor, status=status, page=page, page_size=page_size))


@router.post("/admin/complaints/{complaint_id}/resolve", summary="办结投诉")
def resolve_complaint(
    complaint_id: str,
    payload: ComplaintResolveIn,
    request: Request,
    actor: Actor = Depends(get_actor),
    engine: DomainEngine = Depends(write_engine),
) -> JSONResponse:
    return render(
        engine.resolve_complaint(
            actor,
            complaint_id,
            payload.feedback,
            datetime.now(),
            linked_reservation_id=payload.linked_reservation_id,
            linked_mode=payload.linked_mode,
            ip=client_ip(request),
        )
    )


@router.get("/admin/students", summary="学生列表")
def list_students(
    keyword: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    actor: Actor = Depends(get_actor),
    engine: DomainEngine = Depends(read_engine),
) -> JSONResponse:
    return render_page(
        engine.list_users(
            actor, role=Role.STUDENT.value, keyword=keyword, page=page, page_size=page_size
        )
    )


@router.get("/admin/users", summary="账号列表")
def list_users(
    role: str | None = None,
    keyword: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    actor: Actor = Depends(get_actor),
    engine: DomainEngine = Depends(read_engine),
) -> JSONResponse:
    return render_page(
        engine.list_users(actor, role=role, keyword=keyword, page=page, page_size=page_size)
    )


@router.put("/admin/users/{user_id}/role", summary="调整账号角色")
def set_role(
    user_id: str,
    payload: RoleIn,
    request: Request,
    actor: Actor = Depends(get_actor),
    engine: DomainEngine = Depends(write_engine),
) -> JSONResponse:
    return render(
        engine.set_user_role(
            actor, user_id, payload.role, datetime.now(), ip=client_ip(request)
        )
    )


@router.get("/admin/blacklist", summary="黑名单")
def list_blacklist(
    active_only: bool = False,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    actor: Actor = Depends(get_actor),
    engine: DomainEngine = Depends(read_engine),
) -> JSONResponse:
    return render_page(
        engine.list_blacklist(
            actor, active_only=active_only, page=page, page_size=page_size, now=datetime.now()
        )
    )


@router.post("/admin/blacklist", status_code=201, summary="人工拉黑")
def add_blacklist(
    payload: BlacklistIn,
    request: Request,
    actor: Actor = Depends(get_actor),
    engine: DomainEngine = Depends(write_engine),
) -> JSONResponse:
    result = engine.manual_blacklist(
        actor,
        payload.user_id,
        payload.reason,
        payload.days,
        datetime.now(),
        ip=client_ip(request),
    )
    return render(result, status_code=201)


@router.post("/admin/blacklist/{user_id}/release", summary="解除黑名单")
def release_blacklist(
    user_id: str,
    payload: ReasonIn,
    request: Request,
    actor: Actor = Depends(get_actor),
    engine: DomainEngine = Depends(write_engine),
) -> JSONResponse:
    return render(
        engine.release_blacklist(
            actor, user_id, payload.reason, datetime.now(), ip=client_ip(request)
        )
    )


@router.get("/admin/reviews", summary="评价管理")
def list_reviews(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    actor: Actor = Depends(get_actor),
    engine: DomainEngine = Depends(read_engine),
) -> JSONResponse:
    # 评价管理归自习室管理员（PR-18）；学生看自己的评价走 GET /api/reviews，
    # 教师与系统管理员一律拒绝，避免管理端前缀被非管理角色复用。
    if actor.role != Role.ROOM_ADMIN:
        raise Forbidden("评价管理仅自习室管理员可用", rule="PR-18")
    return render_page(engine.list_reviews(actor, page=page, page_size=page_size))


@router.post("/admin/reviews/{review_id}/reply", summary="回复评价")
def reply_review(
    review_id: str,
    payload: ReviewReplyIn,
    request: Request,
    actor: Actor = Depends(get_actor),
    engine: DomainEngine = Depends(write_engine),
) -> JSONResponse:
    return render(
        engine.reply_review(
            actor,
            review_id,
            payload.reply,
            datetime.now(),
            block_seat=payload.block_seat,
            ip=client_ip(request),
        )
    )


@router.post("/violations/{violation_id}/review", summary="审核违规申诉")
def review_appeal(
    violation_id: str,
    payload: AppealReviewIn,
    request: Request,
    actor: Actor = Depends(get_actor),
    engine: DomainEngine = Depends(write_engine),
) -> JSONResponse:
    """审核学生对违规记录的申诉（PR-10，仅自习室管理员）。

    路径与学生的 ``POST /violations/{id}/appeal`` 对称，故不放在 /admin 前缀下。
    engine.review_appeal 早已实现（权限、信用分回退、站内通知、审计），
    原先只是缺少这层 HTTP 暴露，导致学生提交申诉后无人可审、流程卡死。
    """
    return render(
        engine.review_appeal(
            actor,
            violation_id,
            payload.decision,
            payload.note,
            datetime.now(),
            ip=client_ip(request),
        )
    )


@router.get("/admin/audit", summary="审计日志")
def list_audit(
    actor_id: str | None = None,
    action: str | None = None,
    target_type: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    actor: Actor = Depends(get_actor),
    engine: DomainEngine = Depends(read_engine),
) -> JSONResponse:
    return render_page(
        engine.list_audit(
            actor,
            actor_id=actor_id,
            action=action,
            target_type=target_type,
            date_from=date_from,
            date_to=date_to,
            page=page,
            page_size=page_size,
        )
    )


@router.get("/admin/params", summary="系统参数")
def list_params(
    actor: Actor = Depends(get_actor),
    engine: DomainEngine = Depends(read_engine),
) -> JSONResponse:
    return success({"items": engine.list_params(actor)})


@router.put("/admin/params/{code}", summary="修改系统参数")
def set_param(
    code: str,
    payload: ParamIn,
    request: Request,
    actor: Actor = Depends(get_actor),
    engine: DomainEngine = Depends(write_engine),
) -> JSONResponse:
    return render(
        engine.set_param(
            actor, code, payload.value, datetime.now(), ip=client_ip(request)
        )
    )


@router.get("/admin/scheduler", summary="调度器状态")
def scheduler_status(
    actor: Actor = Depends(get_actor),
    ctx: AppContext = Depends(get_context),
) -> JSONResponse:
    if actor.role != Role.SYS_ADMIN:
        raise Forbidden("仅系统管理员可查看调度器状态", rule="PR-15")
    if ctx.scheduler is None:
        return success({"running": False, "enabled": False})
    return success({**ctx.scheduler.status(), "enabled": True})


def _csv_safe(value):
    """防止 CSV 公式注入：以公式起始符开头的文本加前导单引号。"""
    if isinstance(value, str) and value.startswith(("=", "+", "-", "@", "\t", "\r")):
        return "'" + value
    return value


@router.get("/admin/export/{kind}.csv", summary="CSV 导出")
def export_csv(
    kind: str,
    date_from: date | None = None,
    date_to: date | None = None,
    actor: Actor = Depends(get_actor),
    engine: DomainEngine = Depends(read_engine),
) -> Response:
    headers, rows = engine.export_rows(actor, kind, date_from=date_from, date_to=date_to)
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\r\n")
    writer.writerow(headers)
    writer.writerows([[_csv_safe(cell) for cell in row] for row in rows])
    payload = "\ufeff" + buffer.getvalue()
    filename = f"{kind}-{datetime.now():%Y%m%d-%H%M}.csv"
    return Response(
        content=payload.encode("utf-8"),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{kind}.csv"; filename*=UTF-8\'\'{filename}'
        },
    )
