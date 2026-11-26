"""认证接口：登录、登出、当前用户、修改密码。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from ...errors import NotFound
from ...service.auth import AuthSession
from ...service.container import AppContext
from ..deps import client_ip, get_context, get_session, success
from ..schemas import ChangePasswordIn, LoginIn

router = APIRouter(prefix="/auth", tags=["auth"])


def _set_auth_cookies(response: JSONResponse, ctx: AppContext, token: str, csrf: str) -> None:
    max_age = ctx.settings.session_absolute_hours * 3600
    response.set_cookie(
        ctx.settings.cookie_name,
        token,
        max_age=max_age,
        httponly=True,
        samesite="lax",
        secure=ctx.settings.cookie_secure,
        path="/",
    )
    response.set_cookie(
        ctx.settings.csrf_cookie_name,
        csrf,
        max_age=max_age,
        httponly=False,
        samesite="lax",
        secure=ctx.settings.cookie_secure,
        path="/",
    )


def _clear_auth_cookies(response: JSONResponse, ctx: AppContext) -> None:
    response.delete_cookie(ctx.settings.cookie_name, path="/")
    response.delete_cookie(ctx.settings.csrf_cookie_name, path="/")


@router.post("/login", summary="登录")
def login(
    payload: LoginIn,
    request: Request,
    ctx: AppContext = Depends(get_context),
) -> JSONResponse:
    token, csrf, user = ctx.auth.login(
        payload.username,
        payload.password,
        ip=client_ip(request),
        user_agent=request.headers.get("User-Agent", ""),
    )
    response = success(
        {
            "user": user.as_dict(),
            "csrf_token": csrf,
            "expires_in": ctx.settings.session_absolute_hours * 3600,
        },
        "登录成功",
    )
    _set_auth_cookies(response, ctx, token, csrf)
    return response


@router.post("/logout", summary="登出")
def logout(request: Request, ctx: AppContext = Depends(get_context)) -> JSONResponse:
    ctx.auth.logout(request.cookies.get(ctx.settings.cookie_name))
    response = success(None, "已退出登录")
    _clear_auth_cookies(response, ctx)
    return response


@router.get("/demo-accounts", summary="演示账号（仅演示数据可用时）")
def demo_accounts(ctx: AppContext = Depends(get_context)) -> JSONResponse:
    """只返回演示账号的公开信息。

    * 演示模式，或桌面版在「系统」页初始化过演示数据后可用；
    * 生产环境始终不可用；
    * 演示密码属于部署凭据，只能由初始化渠道一次性展示，
      **不得**通过 HTTP 接口返回，避免任何访问者直接获得登录凭据。
    """
    if ctx.settings.is_production or not (
        ctx.settings.demo_mode or ctx.demo_data_loaded()
    ):
        raise NotFound("演示账号不可用")
    with ctx.db.read() as conn:
        rows = conn.execute(
            "SELECT id, name, role, class_name FROM users "
            "WHERE id IN (SELECT MIN(id) FROM users GROUP BY role) ORDER BY CASE role "
            "WHEN 'student' THEN 1 WHEN 'teacher' THEN 2 WHEN 'room_admin' THEN 3 ELSE 4 END, id"
        ).fetchall()
    return success(
        [
            {
                "username": row["id"],
                "name": row["name"],
                "role": row["role"],
                "class_name": row["class_name"],
            }
            for row in rows
        ]
    )


@router.get("/me", summary="当前登录用户")
def me(
    session: AuthSession = Depends(get_session),
) -> JSONResponse:
    return success(
        {
            "user": session.user.as_dict(),
            "csrf_token": session.csrf_token,
            "expires_at": session.expires_at.isoformat(sep=" ", timespec="seconds"),
        }
    )


@router.post("/change-password", summary="修改密码")
def change_password(
    payload: ChangePasswordIn,
    session: AuthSession = Depends(get_session),
    ctx: AppContext = Depends(get_context),
) -> JSONResponse:
    ctx.auth.change_password(session.user.id, payload.old_password, payload.new_password)
    response = success(None, "密码已修改，请使用新密码重新登录")
    _clear_auth_cookies(response, ctx)
    return response
