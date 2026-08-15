from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.entities import DownloadJob


async def find_download_job_by_execution_id(
    session: AsyncSession, execution_id: str
) -> DownloadJob | None:
    result = await session.execute(
        select(DownloadJob).where(DownloadJob.execution_id == execution_id)
    )
    return result.scalar_one_or_none()
