from __future__ import annotations

import asyncio

import pytest

import app.workers.download_executor_main as executor_main
from app.core.config import Settings


@pytest.mark.asyncio
async def test_run_reloads_runtime_settings_before_each_claim_cycle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    initial = Settings(_env_file=None, qb_target_category="initial")
    refreshed = Settings(_env_file=None, qb_target_category="refreshed")
    snapshots = iter((initial, refreshed))
    built_with: list[Settings] = []

    class FakeExecutor:
        async def run_once(self) -> bool:
            return False

    class StopLoop(Exception):
        pass

    async def idle_heartbeat(_instance_id: str) -> None:
        await asyncio.Event().wait()

    async def stop_after_cycle(_seconds: float) -> None:
        raise StopLoop

    def build(settings: Settings, _worker_id: str) -> FakeExecutor:
        built_with.append(settings)
        return FakeExecutor()

    monkeypatch.setattr(executor_main, "get_settings", lambda: next(snapshots))
    monkeypatch.setattr(executor_main, "require_download_executor_enabled", lambda _: None)
    monkeypatch.setattr(
        executor_main,
        "require_download_executor_ready_configuration",
        lambda _: None,
    )
    monkeypatch.setattr(executor_main, "build_download_executor", build)
    monkeypatch.setattr(executor_main, "_ready_heartbeat_loop", idle_heartbeat)
    monkeypatch.setattr(executor_main.asyncio, "sleep", stop_after_cycle)

    with pytest.raises(StopLoop):
        await executor_main.run()

    assert built_with == [refreshed]
