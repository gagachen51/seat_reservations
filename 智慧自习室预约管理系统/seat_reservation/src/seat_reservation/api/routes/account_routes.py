"""A 模块：账号生命周期与个人中心。请求模型局部定义，注册由 B 集成。"""
from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

from ...domain.engine import DomainEngine
from ...domain.models import Actor, Role
from ...errors import Forbidden
from ...service.auth import AuthSession
from ...service.container import AppContext
from ..deps import (client_ip, get_actor, get_context, get_session, read_engine,
                    render, success, write_engine)

router = APIRouter(tags=["accounts"])
Text100 = Annotated[str, StringConstraints(strip_whitespace=True, max_length=100)]
Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]


class AccountIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=64,
                                       pattern=r"^[^\s/\\]+$")]
    name: Name
    role: Literal["student", "teacher", "room_admin", "sys_admin"]
    department: Text100 = ""
    class_name: Text100 = ""
    password: str | None = Field(default=None, min_length=8, max_length=128)


class StatusIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["active", "disabled"]
    reason: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]


class EmptyIn(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ProfileIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: Name | None = None
    department: Text100 | None = None

    @field_validator("name", "department", mode="before")
    @classmethod
    def reject_explicit_null(cls, value):
        if value is None:
            raise ValueError("字段不能为 null，请省略不修改的字段")
        return value


@router.post("/admin/users", status_code=201, summary="创建账号")
def create_user(payload: AccountIn, request: Request,
                actor: Actor = Depends(get_actor), engine: DomainEngine = Depends(write_engine)):
    values = payload.model_dump()
    values["user_id"] = values.pop("id")
    return render(engine.create_user(actor, datetime.now(), **values, ip=client_ip(request)), status_code=201)


@router.put("/admin/users/{user_id}/status", summary="启用或停用账号")
def set_status(user_id: str, payload: StatusIn, request: Request,
               actor: Actor = Depends(get_actor), engine: DomainEngine = Depends(write_engine)):
    return render(engine.set_user_status(actor, user_id, payload.status, payload.reason,
                                         datetime.now(), ip=client_ip(request)))


@router.post("/admin/users/{user_id}/reset-password", summary="重置初始密码")
def reset_password(user_id: str, payload: EmptyIn, request: Request,
                   actor: Actor = Depends(get_actor), engine: DomainEngine = Depends(write_engine)):
    return render(engine.reset_user_password(actor, user_id, datetime.now(), ip=client_ip(request)))


@router.get("/admin/users/{user_id}/sessions", summary="查看账号登录设备")
def admin_sessions(user_id: str, actor: Actor = Depends(get_actor),
                   session: AuthSession = Depends(get_session), ctx: AppContext = Depends(get_context),
                   engine: DomainEngine = Depends(read_engine)) -> JSONResponse:
    # 即使目标是本人，管理端路由也只允许系统管理员。
    if actor.role != Role.SYS_ADMIN:
        raise Forbidden("仅系统管理员可使用账号管理接口", rule="PR-14")
    return success(engine.list_user_sessions(actor, user_id, datetime.now(),
                    current_token_hash=session.token_hash, idle_minutes=ctx.settings.session_idle_minutes))


@router.post("/admin/users/{user_id}/sessions/revoke", summary="注销账号全部登录设备")
def revoke_sessions(user_id: str, payload: EmptyIn, request: Request,
                    actor: Actor = Depends(get_actor), engine: DomainEngine = Depends(write_engine)):
    return render(engine.revoke_user_sessions(actor, user_id, datetime.now(), ip=client_ip(request)))


@router.get("/me/sessions", summary="我的登录设备")
def my_sessions(actor: Actor = Depends(get_actor), session: AuthSession = Depends(get_session),
                ctx: AppContext = Depends(get_context), engine: DomainEngine = Depends(read_engine)):
    return success(engine.list_user_sessions(actor, actor.id, datetime.now(),
                    current_token_hash=session.token_hash, idle_minutes=ctx.settings.session_idle_minutes))


@router.post("/me/sessions/revoke-others", summary="退出其他设备")
def revoke_others(payload: EmptyIn, request: Request, actor: Actor = Depends(get_actor),
                   session: AuthSession = Depends(get_session), engine: DomainEngine = Depends(write_engine)):
    return render(engine.revoke_user_sessions(actor, actor.id, datetime.now(),
                    except_token_hash=session.token_hash, ip=client_ip(request)))


@router.put("/me/profile", summary="修改本人姓名或院系")
def update_profile(payload: ProfileIn, request: Request,
                    actor: Actor = Depends(get_actor), engine: DomainEngine = Depends(write_engine)):
    return render(engine.update_my_profile(actor, datetime.now(), **payload.model_dump(exclude_unset=True),
                                           ip=client_ip(request)))
