"""管理端资源治理接口：封禁记录、按记录解封、批量建座、停用影响预览。

设计约定（与 ``api/schemas.py`` 的“本期不修改”一致）
----------------------------------------------------
* 本模块的请求模型**定义在文件内**，不往 ``api/schemas.py`` 集中添加；
* 路由只做参数形状与 HTTP 映射，业务规则全部在 ``domain/engine.py``
  与 ``domain/resource_rules.py``；
* 所有写请求仍需 CSRF（由 ``api/app.py`` 的中间件统一校验）。

本模块只提供 3 个新路由，``DELETE /admin/seat-blocks/{seat_id}`` 仍是
``admin_routes.py`` 的既有接口（行为已在引擎内调整）：

* ``GET  /admin/seat-blocks``                     封禁记录列表
* ``POST /admin/seat-blocks/{block_id}/release``  按记录解封
* ``POST /admin/seats/bulk``                      批量建座
* ``GET  /admin/rooms/{room_id}/impact``          停用影响预览
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from ...domain.engine import DomainEngine
from ...domain.models import Actor
from ..deps import client_ip, get_actor, read_engine, render, write_engine

router = APIRouter(tags=["admin-resource"])


class SeatBlockReleaseIn(BaseModel):
    """按记录解封。

    ``restore_seat`` 默认 ``False``：解封只关闭封禁记录、不改座位状态；
    需要恢复必须由管理员显式勾选（避免把手工维护/停用座位误恢复）。
    """

    reason: str = Field(min_length=2, max_length=500)
    restore_seat: bool = False


class BulkSeatItemIn(BaseModel):
    id: str = Field(min_length=1, max_length=32)
    type: str = Field(default="standard", pattern="^(standard|power|quiet|discussion)$")
    capacity: int = Field(default=1, ge=1, le=20)
    has_power: bool = False
    is_window: bool = False
    note: str = Field(default="", max_length=200)


class BulkSeatIn(BaseModel):
    """批量建座：单次最多 100 条；任一非法则整批回滚（不做部分成功）。"""

    room_id: str = Field(min_length=1, max_length=32)
    seats: list[BulkSeatItemIn] = Field(min_length=1, max_length=100)


@router.get("/admin/seat-blocks", summary="封禁记录列表")
def list_seat_blocks(
    seat_id: str | None = None,
    room_id: str | None = None,
    active_only: bool = False,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    actor: Actor = Depends(get_actor),
    engine: DomainEngine = Depends(read_engine),
) -> JSONResponse:
    return render(
        engine.list_seat_blocks(
            actor,
            seat_id=seat_id,
            room_id=room_id,
            active_only=active_only,
            page=page,
            page_size=page_size,
        )
    )


@router.post("/admin/seat-blocks/{block_id}/release", summary="按记录解封")
def release_seat_block(
    block_id: str,
    payload: SeatBlockReleaseIn,
    request: Request,
    actor: Actor = Depends(get_actor),
    engine: DomainEngine = Depends(write_engine),
) -> JSONResponse:
    return render(
        engine.release_seat_block(
            actor,
            block_id,
            payload.reason,
            datetime.now(),
            restore_seat=payload.restore_seat,
            ip=client_ip(request),
        )
    )


@router.post("/admin/seats/bulk", status_code=201, summary="批量新增座位")
def bulk_create_seats(
    payload: BulkSeatIn,
    request: Request,
    actor: Actor = Depends(get_actor),
    engine: DomainEngine = Depends(write_engine),
) -> JSONResponse:
    result = engine.bulk_create_seats(
        actor,
        payload.room_id,
        [item.model_dump() for item in payload.seats],
        datetime.now(),
        ip=client_ip(request),
    )
    return render(result, status_code=201)


@router.get("/admin/rooms/{room_id}/impact", summary="停用影响预览")
def room_impact(
    room_id: str,
    status: str | None = None,
    actor: Actor = Depends(get_actor),
    engine: DomainEngine = Depends(read_engine),
) -> JSONResponse:
    return render(engine.room_impact(actor, room_id, status))
