from __future__ import annotations

import pytest

from app.adapters.pt_sites.avistaz import AvistaZMockAdapter, example_candidate
from app.errors import AppError
from app.schemas.adapters import TorrentSearchRequest


@pytest.mark.asyncio
async def test_avistaz_capabilities_and_contract() -> None:
    adapter = AvistaZMockAdapter([example_candidate()])
    manifest = adapter.manifest()
    assert manifest.enabled is False
    assert manifest.mode == "MOCK_ONLY"
    assert manifest.capabilities["tmdb_search"] is True
    assert manifest.capabilities["direct_torrent_download"] is True
    assert manifest.capabilities["fetch_torrent_enabled"] is False

    results = await adapter.search(TorrentSearchRequest(tmdb=1))
    assert len(results) == 1
    serialized = results[0].model_dump_json().lower()
    for forbidden in ("download_url", "passkey", "cookie", "token", "pid"):
        assert forbidden not in serialized
    details = await adapter.get_torrent_details("mock-1")
    assert details.candidate.torrent_id == "mock-1"


@pytest.mark.asyncio
async def test_fetch_torrent_is_hard_disabled() -> None:
    adapter = AvistaZMockAdapter()
    with pytest.raises(AppError) as caught:
        await adapter.fetch_torrent("anything")
    assert caught.value.error_code == "PHASE_NOT_ENABLED"

