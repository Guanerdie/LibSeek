from __future__ import annotations

from fastapi import APIRouter, HTTPException
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from app.api.dependencies import DbSession
from app.simple.models import AutomationRun

router = APIRouter(tags=["system"])


@router.get("/health")
async def health(session: DbSession) -> dict[str, str]:
    try:
        # SELECT 1 does not touch a SQLite database file and can succeed while
        # business queries are locked out. Read a real application table.
        await session.scalar(select(AutomationRun.id).limit(1))
    except SQLAlchemyError:
        raise HTTPException(status_code=503, detail="数据库暂时不可用") from None
    return {"status": "ok"}
