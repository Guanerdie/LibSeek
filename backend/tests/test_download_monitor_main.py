from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

import app.workers.download_monitor_main as monitor_main
from app.adapters.downloaders import QbittorrentReadOnlyAdapter
from app.core.config import Settings, apply_runtime_configuration
from app.core.runtime_config import runtime_store
from app.errors import AppError


def monitor_settings(
    runtime_config_dir: Path,
    *,
    enable_download_monitor: bool = True,
    enable_qb_read_only: bool = True,
) -> Settings:
    return Settings(
        _env_file=None,
        runtime_config_dir=runtime_config_dir,
        enable_download_monitor=enable_download_monitor,
        enable_qb_read_only=enable_qb_read_only,
        qb_base_url_file=None,
        qb_username_file=None,
        qb_password_file=None,
    )


def save_qb_runtime_configuration(
    settings: Settings,
    *,
    host: str,
    save_path: str,
    category: str,
) -> None:
    runtime_store(settings).save_configuration(
        {
            "qbittorrent": {
                "url": f"https://{host}",
                "username": f"user-{category}",
                "password": f"password-{category}",
                "save_path": save_path,
                "category": category,
                "allow_insecure_http": False,
            }
        }
    )


@pytest.mark.asyncio
async def test_run_reloads_page_saved_qb_configuration_before_every_cycle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    base = monitor_settings(tmp_path)
    save_qb_runtime_configuration(
        base,
        host="qb-one.example.test",
        save_path="/downloads/one",
        category="one",
    )
    built_snapshots: list[tuple[str | None, tuple[str, ...], str | None]] = []
    cycle_snapshots: list[tuple[str | None, str | None, object]] = []
    initial_snapshots: list[str | None] = []

    class StopLoop(Exception):
        pass

    class FakeMonitor:
        def __init__(
            self,
            _session_factory: object,
            _monitor_id: str,
            qb_factory: Callable[[], object],
            settings: Settings,
        ) -> None:
            self.qb_factory = qb_factory
            self.settings = settings
            initial_snapshots.append(settings.qb_base_url_value())

        def reconfigure(
            self,
            settings: Settings,
            qb_factory: Callable[[], object],
        ) -> None:
            self.settings = settings
            self.qb_factory = qb_factory

        async def run_once(self) -> int:
            cycle_snapshots.append(
                (
                    self.settings.qb_base_url_value(),
                    self.settings.qb_target_save_path,
                    self.qb_factory(),
                )
            )
            return 1

    def build_adapter(settings: Settings) -> object:
        snapshot = (
            settings.qb_base_url_value(),
            settings.qb_allowed_hosts,
            settings.qb_target_save_path,
        )
        built_snapshots.append(snapshot)
        return snapshot

    sleep_calls = 0

    async def update_after_first_cycle(_seconds: float) -> None:
        nonlocal sleep_calls
        sleep_calls += 1
        if sleep_calls == 1:
            save_qb_runtime_configuration(
                base,
                host="qb-two.example.test",
                save_path="/downloads/two",
                category="two",
            )
            return
        raise StopLoop

    monkeypatch.setattr(
        monitor_main,
        "get_settings",
        lambda: apply_runtime_configuration(base),
    )
    monkeypatch.setattr(monitor_main, "DownloadMonitor", FakeMonitor)
    monkeypatch.setattr(monitor_main, "build_qb_monitor", build_adapter)
    monkeypatch.setattr(monitor_main.asyncio, "sleep", update_after_first_cycle)
    monkeypatch.setenv("DOWNLOAD_MONITOR_ID", "download-monitor:test")

    with pytest.raises(StopLoop):
        await monitor_main.run()

    assert initial_snapshots == ["https://qb-one.example.test"]
    assert built_snapshots == [
        ("https://qb-one.example.test", ("qb-one.example.test",), "/downloads/one"),
        ("https://qb-two.example.test", ("qb-two.example.test",), "/downloads/two"),
    ]
    assert cycle_snapshots == [
        ("https://qb-one.example.test", "/downloads/one", built_snapshots[0]),
        ("https://qb-two.example.test", "/downloads/two", built_snapshots[1]),
    ]


@pytest.mark.asyncio
async def test_runtime_monitor_builder_remains_read_only(tmp_path: Path) -> None:
    base = monitor_settings(tmp_path)
    save_qb_runtime_configuration(
        base,
        host="qb.example.test",
        save_path="/downloads",
        category="monitor",
    )
    adapter = monitor_main.build_qb_monitor(apply_runtime_configuration(base))
    try:
        assert type(adapter) is QbittorrentReadOnlyAdapter
        assert adapter.manifest().capabilities["write_operations"] is False
        assert ("POST", "/api/v2/torrents/add") not in adapter._ALLOWED_REQUESTS
        assert ("POST", "/api/v2/torrents/createCategory") not in adapter._ALLOWED_REQUESTS
    finally:
        await adapter.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("enable_download_monitor", "enable_qb_read_only"),
    ((False, True), (True, False)),
)
async def test_run_keeps_static_monitor_gates(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    enable_download_monitor: bool,
    enable_qb_read_only: bool,
) -> None:
    settings = monitor_settings(
        tmp_path,
        enable_download_monitor=enable_download_monitor,
        enable_qb_read_only=enable_qb_read_only,
    )
    save_qb_runtime_configuration(
        settings,
        host="qb.example.test",
        save_path="/downloads",
        category="monitor",
    )
    monkeypatch.setattr(
        monitor_main,
        "get_settings",
        lambda: apply_runtime_configuration(settings),
    )

    with pytest.raises(AppError) as caught:
        await monitor_main.run()

    assert caught.value.error_code == "DOWNLOAD_MONITOR_DISABLED"
