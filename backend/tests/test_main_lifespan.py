from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app import main as main_module
from app.models.enums import MediaType
from app.simple.models import (
    AutomationJob,
    AutomationJobState,
    LibraryMediaItem,
    MediaState,
)


@pytest.mark.asyncio
async def test_disabled_scheduler_still_recovers_running_jobs(
    session_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with session_factory() as session:
        media = LibraryMediaItem(
            source_item_id="lifespan-recovery",
            media_type=MediaType.MOVIE,
            tmdb_id=9001,
            title="Lifespan Recovery",
            state=MediaState.READY,
        )
        session.add(media)
        await session.flush()
        job = AutomationJob(
            run_id="lifespan-recovery-run",
            media_id=media.id,
            state=AutomationJobState.RUNNING,
        )
        session.add(job)
        await session.commit()
        job_id = job.id

    scheduler = AsyncMock()
    monkeypatch.setattr(main_module, "SessionFactory", session_factory)
    monkeypatch.setattr(main_module, "automation_scheduler_loop", scheduler)
    monkeypatch.setattr(
        main_module,
        "settings",
        main_module.settings.model_copy(update={"automation_scheduler_enabled": False}),
    )

    async with main_module.lifespan(main_module.app):
        pass

    scheduler.assert_not_awaited()
    async with session_factory() as session:
        recovered = await session.get(AutomationJob, job_id)
        assert recovered is not None
        assert recovered.state == AutomationJobState.RETRY_WAIT
        assert recovered.next_attempt_at is not None
        assert recovered.error_message == "上次执行被应用重启中断，等待重试"
