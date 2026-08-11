from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.models.entities import WorkerHeartbeat
from app.services.automation_capability import (
    automation_preflight_is_ready,
    automation_preflight_ready_worker_id,
    publish_automation_preflight_ready,
)


def _settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "enable_automation_engine": True,
        "enable_qb_read_only": True,
        "qb_base_url": "https://qb.internal",
        "qb_allowed_hosts": ("qb.internal",),
        "qb_target_category": "movies",
        "qb_target_save_path": "/downloads/movies",
        "qb_allowed_save_paths": ("/downloads",),
        "qb_save_path_ref": "movies-root",
        "qb_plan_tags": ("unin",),
        "qb_target_instance_ref": "qb-primary",
        "approval_preflight_max_age_seconds": 300,
        "automation_preflight_ready_ttl_seconds": 30,
    }
    values.update(overrides)
    return Settings(**values)  # type: ignore[arg-type]


def _worker_settings(**overrides: object) -> Settings:
    return _settings(
        qb_base_url="https://qb.internal",
        qb_username="reader",
        qb_password="secret",
        qb_allowed_hosts=("qb.internal",),
        **overrides,
    )


@pytest.mark.asyncio
async def test_preflight_capability_is_absent_without_ready_worker(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        assert not await automation_preflight_is_ready(session, _settings())


@pytest.mark.asyncio
async def test_preflight_capability_accepts_matching_worker_without_producer_secrets(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    worker_settings = _worker_settings()
    await publish_automation_preflight_ready(
        session_factory, worker_settings, "worker-one"
    )

    producer_settings = _settings()
    assert producer_settings.qb_credentials() is None
    async with session_factory() as session:
        assert await automation_preflight_is_ready(session, producer_settings)


@pytest.mark.asyncio
async def test_preflight_capability_rejects_stale_worker(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    settings = _worker_settings()
    await publish_automation_preflight_ready(session_factory, settings, "worker-one")
    worker_id = automation_preflight_ready_worker_id(settings, "worker-one")
    async with session_factory() as session:
        heartbeat = await session.get(WorkerHeartbeat, worker_id)
        assert heartbeat is not None
        heartbeat.last_seen_at = datetime.now(UTC) - timedelta(minutes=5)
        await session.commit()

    async with session_factory() as session:
        assert not await automation_preflight_is_ready(session, _settings())


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "producer_settings",
    (
        _settings(qb_target_instance_ref="qb-secondary"),
        _settings(max_candidate_size_bytes=1024),
    ),
)
async def test_preflight_capability_rejects_target_or_policy_mismatch(
    session_factory: async_sessionmaker[AsyncSession],
    producer_settings: Settings,
) -> None:
    await publish_automation_preflight_ready(
        session_factory, _worker_settings(), "worker-one"
    )

    async with session_factory() as session:
        assert not await automation_preflight_is_ready(session, producer_settings)
