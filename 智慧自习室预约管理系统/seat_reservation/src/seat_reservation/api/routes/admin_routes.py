"""管理端接口：运营、处置、信用、参数、审计与导出。"""

from __future__ import annotations

import csv
import io
from datetime import date, datetime

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse, Response

from ...domain.engine import DomainEngine
from ...domain.models import Actor, Page, Role
from ...errors import AppError, Forbidden, ValidationFailed
from ...service.container import AppContext
from ...storage.seed import resolve_demo_password
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
    DemoInitIn,
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

# 演示数据初始化必须回传的确认短语：前端二次确认后才会发送。
DEMO_INIT_CONFIRM = "INITIALIZE_DEMO_DATA"

# 与 storage/seed.py 中的标准演示账号保持一致；仅用于成功提示展示。
# 系统管理员是特例：已有活跃管理员时保留原密码，不随演示账号统一密码重置。
DEMO_ACCOUNT_CARDS = (
    {"username": "S2023001", "name": "张伟", "role": "student", "role_label": "学生"},
    {"username": "T7001", "name": "刘明", "role": "teacher", "role_label": "教师"},
    {"username": "A9001", "name": "王丽", "role": "room_admin", "role_label": "自习室管理员"},
)
DEFAULT_ADMIN_CARD = {
    "username": "Y0001",
    "name": "陈刚",
    "role": "sys_admin",
    "role_label": "系统管理员",
}


@router.get("/admin/stats", summary="运营看板")
def admin_stats(
    room_id: str | None = None,
    actor: Actor = Depends(get_actor),
    engine: DomainEngine = Depends(read_engine),
) -> JSONResponse:
    return success(engine.admin_stats(actor, room_id, datetime.now()))


@router.get("/admin/analytics", summary="运营分析")
def admin_analytics(
    date_from: date | None = None,
    date_to: date | None = None,
    room_id: str | None = None,
    actor: Actor = Depends(get_actor),
    engine: DomainEngine = Depends(read_engine),
) -> JSONResponse:
    # 日期默认值、90 天上限、权限和房间存在性都由 DomainEngine 处理。
    return success(
        engine.admin_analytics(
            actor,
            date_from=date_from,
            date_to=date_to,
            room_id=room_id,
            now=datetime.now(),
        )
    )


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
    """修改自习室；关闭时由引擎在同一写事务内取消仍有效的预约（M-04）。"""
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
            cascade_release=payload.status == "closed",
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
    """修改座位；改为停用/维护时由引擎在同一写事务内取消仍有效的预约（M-04）。"""
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
            cascade_release=payload.admin_status in {"disabled", "maintenance"},
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


@router.delete("/admin/seat-blocks/{seat_id}", summary="解封座位（关闭全部未释放封禁）")
def unblock_seat(
    seat_id: str,
    request: Request,
    actor: Actor = Depends(get_actor),
    engine: DomainEngine = Depends(write_engine),
) -> JSONResponse:
    """按座位解封：关闭该座位全部未释放封禁（B 负责的资源区，行为 v1.2 调整）。

    只有“维护状态由封禁流程设置（``maintenance_by_block=1``）”的座位恢复
    ``available``；手工维护保持维护、停用座位保持停用。按单条记录解封走
    ``POST /admin/seat-blocks/{block_id}/release``（`resource_routes.py`）。
    """
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
    result = engine.list_users(
        actor, role=role, keyword=keyword, page=page, page_size=page_size
    )
    if actor.role == Role.ROOM_ADMIN:
        # L-01：room_admin 只返回学生账号的管理必需字段，不暴露密码/会话/
        # 强制改密等账号安全状态；完整账号管理仍由 sys_admin 使用。
        visible = (
            "id", "name", "role", "role_label",
            "department", "class_name", "status",
        )
        items = [
            {key: value for key, value in item.as_dict().items() if key in visible}
            for item in result.items
        ]
        result = Page(items, result.total, result.page, result.page_size)
    return render_page(result)


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


@router.get("/admin/system", summary="系统运行状态")
def system_status(
    actor: Actor = Depends(get_actor),
    ctx: AppContext = Depends(get_context),
) -> JSONResponse:
    """管理端完整运行状态。

    ``/api/health`` 按 L-02 只返回可用性；本接口带 sysadmin 权限校验，返回
    版本、Schema、库大小、运行时长与调度器详情，供「系统」页面渲染。
    """
    if actor.role != Role.SYS_ADMIN:
        raise Forbidden("仅系统管理员可查看系统状态", rule="PR-15")
    return success(ctx.system_status())


@router.post("/admin/system/demo-data", summary="初始化演示数据")
def initialize_demo_data(
    payload: DemoInitIn,
    request: Request,
    actor: Actor = Depends(get_actor),
    ctx: AppContext = Depends(get_context),
) -> JSONResponse:
    """清空业务数据并写入标准演示数据（课程演示/测试专用）。

    * 仅系统管理员、仅非生产环境、必须回传固定确认短语；
    * 统一演示密码只在本响应中返回一次，供操作者当场记录，不写入审计或日志；
    * 审计表 append-only，重复初始化不会删除历史审计（见 ``seed._reset``）。
    """
    if actor.role != Role.SYS_ADMIN:
        raise Forbidden("仅系统管理员可初始化演示数据", rule="PR-15")
    if ctx.settings.is_production:
        raise Forbidden("生产环境不允许写入演示数据", rule="PR-15")
    if payload.confirm != DEMO_INIT_CONFIRM:
        raise ValidationFailed(
            "确认短语不正确，未写入任何数据", code="DEMO_CONFIRM_REQUIRED"
        )

    now = datetime.now()
    password = resolve_demo_password()
    try:
        counts = ctx.seed_demo(now, password=password)
    except Exception as exc:  # noqa: BLE001 - 失败也要留审计
        with ctx.engine() as engine:
            engine.audit_event(
                actor,
                "initialize_demo_data",
                "system",
                "demo",
                result="failed",
                details={"error": type(exc).__name__},
                now=now,
                ip=client_ip(request),
            )
        raise

    preserved_admins = list(counts.pop("preserved_admins", []) or [])
    report_counts = {
        k: v
        for k, v in counts.items()
        if isinstance(v, int) and k != "admin_preserved"
    }
    admin_password_preserved = bool(preserved_admins)
    accounts = [dict(item) for item in DEMO_ACCOUNT_CARDS]
    if admin_password_preserved:
        accounts.extend(dict(item) for item in preserved_admins)
    else:
        accounts.append({**DEFAULT_ADMIN_CARD, "password_preserved": False})

    with ctx.engine() as engine:
        engine.audit_event(
            actor,
            "initialize_demo_data",
            "system",
            "demo",
            details={"counts": report_counts, "admin_password_preserved": admin_password_preserved},
            now=now,
            ip=client_ip(request),
        )

    # 演示数据写入会清空 sessions：当前会话已失效，必须提示重新登录。
    return success(
        {
            "counts": report_counts,
            "accounts": accounts,
            "demo_password": password,
            "admin_password_preserved": admin_password_preserved,
            "preserved_admin_ids": [item["username"] for item in preserved_admins],
            "must_relogin": True,
        },
        "演示数据已初始化",
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
    """L-04：统一清洗 7 类导出的 CSV 单元格。

    先移除 NUL/BOM，再对首个可见字符为公式前缀的文本加单引号；保留正常
    中文、日期、数字以及普通文本。换行由 csv.writer 负责引用。
    """
    if not isinstance(value, str):
        return value
    cleaned = value.replace("\x00", "").replace("\ufeff", "")
    probe = cleaned.lstrip(" \t\r\n")
    if probe.startswith(("=", "+", "-", "@")) or cleaned.startswith(("\t", "\r", "\n")):
        return "'" + cleaned
    return cleaned


@router.get("/admin/export/{kind}.csv", summary="CSV 导出")
def export_csv(
    kind: str,
    request: Request,
    date_from: date | None = None,
    date_to: date | None = None,
    actor: Actor = Depends(get_actor),
    engine: DomainEngine = Depends(write_engine),
) -> Response:
    now = datetime.now()
    try:
        headers, rows = engine.export_rows(
            actor,
            kind,
            date_from=date_from,
            date_to=date_to,
            audit=True,
            now=now,
        )
    except AppError as exc:
        # 拒绝也必须留痕；捕获后正常返回，使 write_engine 提交该审计记录。
        engine.audit_event(
            actor,
            "export_csv",
            "export",
            kind,
            result="denied",
            details={
                "kind": kind,
                "date_from": date_from.isoformat() if date_from else None,
                "date_to": date_to.isoformat() if date_to else None,
                "error": exc.code,
                "status": exc.status_code,
            },
            now=now,
            ip=client_ip(request),
        )
        return JSONResponse(exc.to_payload(), status_code=exc.status_code)
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\r\n")
    writer.writerow([_csv_safe(cell) for cell in headers])
    writer.writerows([[_csv_safe(cell) for cell in row] for row in rows])
    payload = "\ufeff" + buffer.getvalue()
    filename = f"{kind}-{now:%Y%m%d-%H%M}.csv"
    return Response(
        content=payload.encode("utf-8"),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{kind}.csv"; filename*=UTF-8\'\'{filename}'
        },
    )
