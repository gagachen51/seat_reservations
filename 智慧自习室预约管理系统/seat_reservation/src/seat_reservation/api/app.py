"""FastAPI 应用工厂：中间件、异常处理、静态资源与路由装配。"""

from __future__ import annotations

import sqlite3
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware

from .. import __version__
from ..config import Settings
from ..errors import AppError
from ..logging_setup import configure_logging, get_logger, request_id_var
from ..security import constant_time_equals
from ..service.container import AppContext
from .routes import admin_routes, auth_routes, common_routes, student_routes

logger = get_logger("seat_reservation.http")
WEB_DIR = Path(__file__).resolve().parents[1] / "web"

_MUTATING = {"POST", "PUT", "PATCH", "DELETE"}
_CSRF_EXEMPT = {"/api/auth/login"}


class RequestMiddleware(BaseHTTPMiddleware):
    """请求 ID、CSRF 双提交校验、安全响应头与访问日志。"""

    def __init__(self, app, settings: Settings) -> None:  # type: ignore[no-untyped-def]
        super().__init__(app)
        self.settings = settings

    async def dispatch(self, request: Request, call_next):  # type: ignore[no-untyped-def]
        request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex[:12]
        token = request_id_var.set(request_id)
        started = time.perf_counter()
        try:
            if self._csrf_failed(request):
                return JSONResponse(
                    {
                        "ok": False,
                        "error": {
                            "code": "CSRF_FAILED",
                            "message": "请求校验失败，请刷新页面后重试",
                        },
                    },
                    status_code=403,
                )
            response = await call_next(request)
        finally:
            request_id_var.reset(token)

        elapsed_ms = round((time.perf_counter() - started) * 1000, 1)
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
            "script-src 'self'; connect-src 'self'; base-uri 'none'; form-action 'self'"
        )
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"

        if not request.url.path.startswith("/static/"):
            logger.info(
                "%s %s %s %sms",
                request.method,
                request.url.path,
                response.status_code,
                elapsed_ms,
                extra={
                    "method": request.method,
                    "path": request.url.path,
                    "status": response.status_code,
                    "duration_ms": elapsed_ms,
                },
            )
        return response

    def _csrf_failed(self, request: Request) -> bool:
        if request.method not in _MUTATING:
            return False
        path = request.url.path
        if not path.startswith("/api/") or path in _CSRF_EXEMPT or path.startswith("/api/acs/"):
            return False
        cookie = request.cookies.get(self.settings.csrf_cookie_name)
        header = request.headers.get("X-CSRF-Token")
        return not constant_time_equals(cookie, header)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    configure_logging(settings.log_level, settings.log_json)
    context = AppContext(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI):  # type: ignore[no-untyped-def]
        context.initialize(demo=settings.demo_mode)
        context.start_background_jobs()
        logger.info("service started env=%s db=%s", settings.env, settings.db_path)
        try:
            yield
        finally:
            context.stop_background_jobs()
            logger.info("service stopped")

    app = FastAPI(
        title="智慧自习室预约管理系统",
        version=__version__,
        description="预约、签到、暂离、签退、违约处置与运营看板的一体化服务。",
        docs_url=None if settings.is_production else "/docs",
        redoc_url=None,
        openapi_url=None if settings.is_production else "/openapi.json",
        lifespan=lifespan,
    )
    app.state.ctx = context
    app.state.settings = settings
    app.add_middleware(RequestMiddleware, settings=settings)

    @app.exception_handler(AppError)
    async def handle_app_error(request: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(exc.to_payload(), status_code=exc.status_code)

    @app.exception_handler(RequestValidationError)
    async def handle_validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        details = [
            {"field": ".".join(str(part) for part in item["loc"][1:]), "message": item["msg"]}
            for item in exc.errors()
        ]
        return JSONResponse(
            {
                "ok": False,
                "error": {
                    "code": "VALIDATION_ERROR",
                    "message": "提交的数据不合法，请检查后重试",
                    "details": details,
                },
            },
            status_code=422,
        )

    @app.exception_handler(sqlite3.OperationalError)
    async def handle_storage(request: Request, exc: sqlite3.OperationalError) -> JSONResponse:
        logger.error("storage unavailable path=%s: %s", request.url.path, exc)
        return JSONResponse(
            {
                "ok": False,
                "error": {
                    "code": "STORAGE_UNAVAILABLE",
                    "message": "存储暂时不可用，请稍后重试",
                    "request_id": request_id_var.get(),
                },
            },
            status_code=503,
        )

    @app.exception_handler(Exception)
    async def handle_unexpected(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("unhandled error path=%s", request.url.path)
        return JSONResponse(
            {
                "ok": False,
                "error": {
                    "code": "INTERNAL_ERROR",
                    "message": "服务器处理失败，请稍后重试",
                    "request_id": request_id_var.get(),
                },
            },
            status_code=500,
        )

    app.include_router(common_routes.router, prefix="/api")
    app.include_router(auth_routes.router, prefix="/api")
    app.include_router(student_routes.router, prefix="/api")
    app.include_router(admin_routes.router, prefix="/api")

    if WEB_DIR.is_dir():
        app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")

        @app.get("/", include_in_schema=False)
        async def index() -> FileResponse:
            return FileResponse(WEB_DIR / "index.html")

        @app.get("/favicon.svg", include_in_schema=False)
        async def favicon() -> FileResponse:
            return FileResponse(WEB_DIR / "favicon.svg")

    return app


app = create_app()
