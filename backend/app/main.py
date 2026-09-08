from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.api.routes import auth, configuration, health
from app.core.automation_runner import (
    automation_scheduler_loop,
    cancel_manual_automation_runs,
    recover_interrupted_jobs,
    rss_matcher_loop,
)
from app.core.config import get_settings
from app.core.logging import configure_logging
from app.core.security import sanitize_details
from app.db.session import SessionFactory
from app.errors import AppError
from app.schemas.common import ErrorResponse
from app.simple import routes as daily
from app.simple.integrations import build_pt_site

settings = get_settings()


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    configure_logging()
    async with SessionFactory() as session:
        await recover_interrupted_jobs(session)

    stop = asyncio.Event()
    tasks: list[asyncio.Task[None]] = []
    if settings.automation_scheduler_enabled:
        tasks.append(asyncio.create_task(automation_scheduler_loop(stop)))
    if settings.rss_matcher_enabled:
        tasks.append(
            asyncio.create_task(
                rss_matcher_loop(
                    stop,
                    lambda: build_pt_site("avistaz", allow_torrent_fetch=False),
                )
            )
        )
    try:
        yield
    finally:
        await cancel_manual_automation_runs()
        if tasks:
            stop.set()
            await asyncio.gather(*tasks)


app = FastAPI(
    title=settings.app_name,
    version="0.9.0",
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
    lifespan=lifespan,
)


@app.exception_handler(AppError)
async def handle_app_error(request: Request, exc: AppError) -> JSONResponse:
    body = ErrorResponse(
        error_code=exc.error_code,
        message=exc.message,
        details=sanitize_details(exc.details),
    )
    return JSONResponse(
        status_code=exc.status_code,
        content=body.model_dump(mode="json"),
        headers=_configuration_no_store_headers(request),
    )


@app.exception_handler(RequestValidationError)
async def handle_validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    errors = exc.errors()
    fields = [
        {"location": ".".join(str(part) for part in error["loc"]), "type": error["type"]}
        for error in errors
    ]
    body = ErrorResponse(
        error_code="API_VALIDATION_ERROR",
        message=_validation_error_message(request, exc),
        details={"fields": fields},
    )
    return JSONResponse(
        status_code=422,
        content=body.model_dump(mode="json"),
        headers=_configuration_no_store_headers(request),
    )


def _validation_error_message(request: Request, exc: RequestValidationError) -> str:
    if request.url.path == f"{settings.api_prefix}/auth/setup":
        for error in exc.errors():
            location = error.get("loc")
            error_type = error.get("type")
            if location == ("body", "password"):
                if error_type == "missing":
                    return "请输入管理员密码"
                if error_type in {"string_too_short", "too_short"}:
                    return "管理员密码至少需要 6 位"
                if error_type in {"string_too_long", "too_long"}:
                    return "管理员密码不能超过 1024 位"
            if location == ("body", "username"):
                if error_type in {"missing", "string_too_short"}:
                    return "请输入管理员用户名"
                if error_type == "string_too_long":
                    return "管理员用户名不能超过 120 位"
        return "管理员信息格式不正确"
    return "请求参数校验失败"


def _configuration_no_store_headers(request: Request) -> dict[str, str] | None:
    if request.url.path.startswith(f"{settings.api_prefix}/configuration"):
        return {"Cache-Control": "no-store", "Pragma": "no-cache"}
    return None


app.include_router(health.router, prefix=settings.api_prefix)
app.include_router(auth.router, prefix=settings.api_prefix)
app.include_router(configuration.router, prefix=settings.api_prefix)
app.include_router(daily.router, prefix=settings.api_prefix)
