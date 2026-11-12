"""FastAPI 依赖：上下文、会话、引擎事务与响应封装。

关键约定：路由**不抛业务异常**来表达“结果失败”。``Result.failure`` 直接渲染为
409 响应并正常结束事务，从而保留引擎已经写入的后果（如超窗签到判违约）。
只有真正的参数/权限错误才抛 ``AppError``，事务回滚，保证“拒绝无副作用”。
"""

from __future__ import annotations

from typing import Iterator

from fastapi import Depends, Request
from fastapi.responses import JSONResponse

from ..domain.engine import DomainEngine
from ..domain.models import Actor, Page, Result
from ..errors import Unauthenticated
from ..service.auth import AuthSession, actor_of
from ..service.container import AppContext


def get_context(request: Request) -> AppContext:
    return request.app.state.ctx


def get_session(request: Request, ctx: AppContext = Depends(get_context)) -> AuthSession:
    token = request.cookies.get(ctx.settings.cookie_name)
    session = ctx.auth.authenticate(token)
    if session is None:
        raise Unauthenticated("登录状态已失效，请重新登录")
    return session


def get_actor(session: AuthSession = Depends(get_session)) -> Actor:
    return actor_of(session.user)


def client_ip(request: Request) -> str:
    """来源 IP：默认取直连地址；只有显式开启 SEAT_TRUST_PROXY 时才信任 XFF。"""
    settings = request.app.state.ctx.settings
    if settings.trust_proxy:
        forwarded = request.headers.get("X-Forwarded-For", "")
        if forwarded:
            return forwarded.split(",")[0].strip()
    return request.client.host if request.client else ""


def write_engine(request: Request) -> Iterator[DomainEngine]:
    ctx: AppContext = request.app.state.ctx
    with ctx.engine(write=True) as engine:
        yield engine


def read_engine(request: Request) -> Iterator[DomainEngine]:
    ctx: AppContext = request.app.state.ctx
    with ctx.engine(write=False) as engine:
        yield engine


def success(data: object = None, message: str = "OK") -> JSONResponse:
    return JSONResponse({"ok": True, "data": data, "message": message})


def render(result: Result, *, status_code: int = 200) -> JSONResponse:
    """成功渲染 data；业务拒绝渲染 409 并携带规则编号。"""
    if result.ok:
        return JSONResponse(
            {"ok": True, "data": result.data, "message": result.message},
            status_code=status_code,
        )
    return JSONResponse(
        {
            "ok": False,
            "error": {
                "code": result.code,
                "message": result.message,
                "rule": result.rule,
                "details": result.data,
            },
        },
        status_code=409,
    )


def render_page(page: Page) -> JSONResponse:
    return JSONResponse({"ok": True, "data": page.as_dict()})
