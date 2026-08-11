from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.api.routes import (
    adapters,
    approvals,
    auth,
    automation,
    discovery,
    downloaders,
    executions,
    health,
    jobs,
    media,
    media_imports,
    workflow,
)
from app.core.config import get_settings
from app.core.security import sanitize_details
from app.errors import AppError
from app.schemas.common import ErrorResponse

settings = get_settings()
app = FastAPI(
    title=settings.app_name,
    version="0.7.0",
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
)


@app.exception_handler(AppError)
async def handle_app_error(_: Request, exc: AppError) -> JSONResponse:
    body = ErrorResponse(
        error_code=exc.error_code,
        message=exc.message,
        details=sanitize_details(exc.details),
    )
    return JSONResponse(status_code=exc.status_code, content=body.model_dump(mode="json"))


@app.exception_handler(RequestValidationError)
async def handle_validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
    fields = [
        {"location": ".".join(str(part) for part in error["loc"]), "type": error["type"]}
        for error in exc.errors()
    ]
    body = ErrorResponse(
        error_code="API_VALIDATION_ERROR",
        message="请求参数校验失败",
        details={"fields": fields},
    )
    return JSONResponse(status_code=422, content=body.model_dump(mode="json"))


app.include_router(health.router, prefix=settings.api_prefix)
app.include_router(auth.router, prefix=settings.api_prefix)
app.include_router(discovery.router, prefix=settings.api_prefix)
app.include_router(media.router, prefix=settings.api_prefix)
app.include_router(adapters.router, prefix=settings.api_prefix)
app.include_router(workflow.router, prefix=settings.api_prefix)
app.include_router(approvals.router, prefix=settings.api_prefix)
app.include_router(automation.router, prefix=settings.api_prefix)
app.include_router(downloaders.router, prefix=settings.api_prefix)
app.include_router(executions.router, prefix=settings.api_prefix)
app.include_router(jobs.router, prefix=settings.api_prefix)
app.include_router(media_imports.router, prefix=settings.api_prefix)
