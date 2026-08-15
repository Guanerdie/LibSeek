from __future__ import annotations

from collections.abc import Collection
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import app.services.approvals as approval_services
from app.adapters.base import ReadOnlyDownloaderAdapter
from app.core.config import Settings
from app.errors import AppError
from app.models.entities import (
    ApprovalEvent,
    DownloadPlan,
    MediaItem,
    TorrentCandidateRecord,
    TorrentSearchRun,
)
from app.models.enums import (
    ApprovalStatus,
    IdentityConfidence,
    MediaType,
    MetadataStatus,
    PreflightStatus,
    WorkflowStatus,
)
from app.schemas.adapters import AdapterManifest, TorrentCandidate
from app.schemas.approvals import (
    ApprovalApproveRequest,
    ApprovalCandidateSnapshot,
    ApprovalCreateRequest,
    PreflightCheck,
    PreflightResult,
    validate_internal_torrent_ref,
)
from app.schemas.qbittorrent import QbCategory, QbTorrent, QbTorrentFile
from app.services.approvals import (
    approve_request,
    approve_request_automatically,
    build_qb_plan_tags,
    consume_approval,
    create_approval_request,
    download_plan_hash,
    media_title_qb_tag,
    preflight_is_manually_passable,
    snapshot_hash,
    verify_download_plan,
    verify_snapshot,
)
from app.services.preflight import (
    PREFLIGHT_CHECK_CODES,
    PREFLIGHT_RECENT_TORRENT_LIMIT,
    evaluate_preflight,
    preflight_policy_fingerprint,
)

INFO_HASH = "0123456789abcdef0123456789abcdef01234567"


def test_internal_torrent_ref_respects_download_plan_column_limit() -> None:
    maximum_ref = f"{'s' * 24}:{'d' * 50}:{'r' * 104}"
    assert len(maximum_ref) == 180
    assert validate_internal_torrent_ref(maximum_ref) == maximum_ref

    with pytest.raises(ValueError, match="strict internal reference"):
        validate_internal_torrent_ref(f"{'s' * 24}:{'d' * 50}:{'r' * 105}")


def test_qb_plan_tags_preserve_configured_tags_and_append_chinese_title() -> None:
    assert build_qb_plan_tags(
        ("unin-plan", "avistaz"),
        "庆余年 第二季",
    ) == ("unin-plan", "avistaz", "庆余年 第二季")


def test_qb_plan_tags_deduplicate_configured_tags_and_title_case_insensitively() -> None:
    assert build_qb_plan_tags(
        ("Unin-Plan", "unin-plan", "三体 SEASON ONE"),
        "三体 season one",
    ) == ("Unin-Plan", "三体 SEASON ONE")


def test_qb_media_title_tag_normalizes_commas_controls_and_length() -> None:
    assert media_title_qb_tag("  庆余年,\n第二季\x00  ") == "庆余年， 第二季"

    truncated = media_title_qb_tag("剧" * 101)
    assert truncated == "剧" * 100
    assert len(truncated) == 100


def test_qb_plan_tags_reject_more_than_twenty_unique_tags() -> None:
    configured = tuple(f"tag-{index}" for index in range(20))

    with pytest.raises(AppError) as caught:
        build_qb_plan_tags(configured, "庆余年")

    assert caught.value.error_code == "QB_PLAN_TAGS_INVALID"


class FakeReadOnlyQb(ReadOnlyDownloaderAdapter):
    def __init__(
        self,
        torrents: list[QbTorrent] | None = None,
        *,
        query_failures: Collection[str] = (),
    ) -> None:
        self.torrents = torrents or []
        self.query_failures = frozenset(query_failures)
        self.auth_calls = 0
        self.list_calls = 0
        self.target_calls: list[tuple[str, object]] = []

    def manifest(self) -> AdapterManifest:
        raise NotImplementedError

    async def authenticate(self) -> None:
        self.auth_calls += 1

    async def get_version(self) -> str:
        return "v5.0.4"

    async def get_web_api_version(self) -> str:
        return "2.11.4"

    async def list_torrents(self) -> list[QbTorrent]:
        self.list_calls += 1
        return self.torrents

    async def find_torrents_by_hashes(
        self, hashes: Collection[str]
    ) -> list[QbTorrent]:
        normalized = tuple(value.casefold() for value in hashes)
        self.target_calls.append(("hashes", normalized))
        if "hashes" in self.query_failures:
            raise AppError("QB_HASH_LOOKUP_FAILED", "test hash lookup failure")
        return [
            torrent
            for torrent in self.torrents
            if set(normalized).intersection(torrent.identity_hashes)
        ]

    async def list_recent_torrents(self, limit: int) -> list[QbTorrent]:
        self.target_calls.append(("recent", limit))
        if "recent" in self.query_failures:
            raise AppError("QB_RECENT_LOOKUP_FAILED", "test recent lookup failure")
        return sorted(
            self.torrents,
            key=lambda torrent: torrent.added_on,
            reverse=True,
        )[:limit]

    async def has_active_seeding(self) -> bool:
        self.target_calls.append(("active", None))
        if "active" in self.query_failures:
            raise AppError("QB_SEEDING_LOOKUP_FAILED", "test seeding lookup failure")
        return any(
            torrent.progress == 1
            and (
                torrent.upspeed > 0
                or torrent.state.casefold() in {"uploading", "forcedup"}
            )
            for torrent in self.torrents
        )

    async def get_torrent_files(self, info_hash: str) -> list[QbTorrentFile]:
        del info_hash
        return []

    async def get_categories(self) -> dict[str, QbCategory]:
        return {"movies": QbCategory(name="movies", savePath="/downloads/movies")}


def safe_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "_env_file": None,
        "avistaz_forbidden_qb_versions": ("4.3.*",),
        "qb_base_url": "https://qb.internal.test",
        "qb_allowed_hosts": ("qb.internal.test",),
        "qb_target_category": "movies",
        "qb_target_save_path": "/downloads/movies/incoming",
        "qb_allowed_save_paths": ("/downloads/movies",),
        "qb_save_path_ref": "movies-root",
        "qb_plan_tags": ("unin-plan",),
        "qb_target_instance_ref": "qb-primary",
        "max_candidate_size_bytes": 10_000,
        "approval_default_ttl_minutes": 60,
        "approval_preflight_max_age_seconds": 300,
    }
    values.update(overrides)
    return Settings(**values)  # type: ignore[arg-type]


def qb_torrent(*, info_hash: str = INFO_HASH, name: str = "Existing", size: int = 1) -> QbTorrent:
    return QbTorrent(
        hash=info_hash,
        name=name,
        size=size,
        progress=1,
        ratio=1,
        state="uploading",
        upspeed=1,
        save_path="/downloads/movies",
    )


def approval_snapshot(**overrides: object) -> ApprovalCandidateSnapshot:
    values: dict[str, object] = {
        "media_item_id": "media",
        "media_title": "Movie",
        "media_type": "movie",
        "tmdb_id": 1,
        "year": 2026,
        "torrent_candidate_id": "candidate",
        "site_id": "avistaz",
        "torrent_id": "torrent",
        "torrent_ref": "avistaz:details:safe",
        "release_title": "Movie 2026",
        "size_bytes": 5000,
        "info_hash": INFO_HASH,
        "season": None,
        "episodes": None,
        "resolution": "1080p",
        "source": "WEB-DL",
        "subtitles": ["Chinese"],
        "seeders": 3,
        "promotion": {"download_factor": 0, "upload_factor": 1},
        "hit_and_run": False,
        "match_score": 0.9,
        "match_reasons": ["TMDB_ID_EXACT"],
        "warnings": [],
        "requested_at": datetime.now(UTC),
        "expires_at": datetime.now(UTC) + timedelta(hours=1),
    }
    values.update(overrides)
    return ApprovalCandidateSnapshot.model_validate(values)


def preflight_result(
    settings: Settings,
    status: PreflightStatus = PreflightStatus.PASS,
) -> PreflightResult:
    checks = [
        PreflightCheck(
            code=code,
            status=status if index == 0 else PreflightStatus.PASS,
            message="test preflight",
        )
        for index, code in enumerate(PREFLIGHT_CHECK_CODES)
    ]
    return PreflightResult(
        overall_status=status,
        checked_at=datetime.now(UTC),
        policy_fingerprint=preflight_policy_fingerprint(settings),
        checks=checks,
    )


def test_preflight_policy_fingerprint_binds_normalized_qb_target_without_leaks() -> None:
    baseline = safe_settings(
        qb_base_url="HTTPS://QB-A.INTERNAL.TEST:443/api///",
        qb_allowed_hosts=("qb-a.internal.test", "qb-b.internal.test"),
        qb_username="private-user",
        qb_password="private-password",
    )
    equivalent = safe_settings(
        qb_base_url="https://qb-a.internal.test/api",
        qb_allowed_hosts=("QB-B.INTERNAL.TEST", "QB-A.INTERNAL.TEST"),
        qb_username="rotated-user",
        qb_password="rotated-password",
    )
    fingerprint = preflight_policy_fingerprint(baseline)

    assert fingerprint == preflight_policy_fingerprint(equivalent)
    assert len(fingerprint) == 64
    assert set(fingerprint) <= set("0123456789abcdef")
    assert all(
        secret not in fingerprint
        for secret in (
            "qb-a.internal.test",
            "/api",
            "private-user",
            "private-password",
        )
    )

    changed_targets = (
        safe_settings(
            qb_base_url="https://qb-b.internal.test/api",
            qb_allowed_hosts=("qb-a.internal.test", "qb-b.internal.test"),
        ),
        safe_settings(
            qb_base_url="https://qb-a.internal.test/api",
            qb_allowed_hosts=("qb-a.internal.test", "qb-b.internal.test"),
            qb_target_instance_ref="qb-secondary",
        ),
        safe_settings(
            qb_base_url="https://qb-a.internal.test/api",
            qb_allowed_hosts=("qb-a.internal.test",),
        ),
        safe_settings(
            qb_base_url="https://qb-a.internal.test/api",
            qb_allowed_hosts=("qb-a.internal.test", "qb-b.internal.test"),
            qb_allow_insecure_http=True,
        ),
        safe_settings(
            qb_base_url="https://qb-a.internal.test/api",
            qb_allowed_hosts=("qb-a.internal.test", "qb-b.internal.test"),
            qb_target_save_path="/downloads/movies/other",
        ),
        safe_settings(
            qb_base_url="https://qb-a.internal.test/api",
            qb_allowed_hosts=("qb-a.internal.test", "qb-b.internal.test"),
            qb_target_category="tv",
        ),
        safe_settings(
            qb_base_url="https://qb-a.internal.test/api",
            qb_allowed_hosts=("qb-a.internal.test", "qb-b.internal.test"),
            qb_plan_tags=("different-tag",),
        ),
    )
    assert all(
        preflight_policy_fingerprint(changed) != fingerprint
        for changed in changed_targets
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("secret_path_kind", ("missing", "unreadable"))
async def test_preflight_base_url_secret_failure_is_closed_before_adapter_use(
    secret_path_kind: str,
) -> None:
    tests_directory = Path(__file__).parent
    secret_path = tests_directory / "missing-qb-base-url.txt"
    if secret_path_kind == "unreadable":
        secret_path = tests_directory
    settings = safe_settings(qb_base_url=None, qb_base_url_file=secret_path)
    adapter = FakeReadOnlyQb()

    with pytest.raises(AppError) as caught:
        await evaluate_preflight(adapter, object(), settings)  # type: ignore[arg-type]

    assert caught.value.error_code == "PREFLIGHT_TARGET_CONFIG_UNAVAILABLE"
    assert caught.value.status_code == 409
    assert str(secret_path) not in caught.value.message
    assert adapter.auth_calls == 0


async def seed_candidate(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    info_hash: str | None = INFO_HASH,
    hit_and_run: bool | None = False,
    seeders: int | None = 5,
) -> tuple[MediaItem, TorrentCandidateRecord]:
    now = datetime.now(UTC)
    unique_tmdb = int(now.timestamp() * 1_000_000) % 2_000_000_000
    media = MediaItem(
        source="nextfind",
        source_item_id=f"approval:{now.timestamp()}",
        media_type=MediaType.MOVIE,
        tmdb_id=unique_tmdb,
        title="Approval Movie",
        year=2026,
        identity_confidence=IdentityConfidence.HIGH,
        metadata_status=MetadataStatus.RESOLVED,
        workflow_status=WorkflowStatus.TORRENT_REVIEW,
        discovered_at=now,
        updated_at=now,
    )
    candidate = TorrentCandidate(
        site_id="avistaz",
        torrent_id=f"candidate-{now.timestamp()}",
        release_title="Approval Movie 2026 1080p",
        details_ref="avistaz:details:immutable-reference",
        media_type=MediaType.MOVIE,
        tmdb_id=unique_tmdb,
        year=2026,
        resolution="1080p",
        source="WEB-DL",
        subtitles=["Chinese"],
        size_bytes=5000,
        seeders=seeders,
        hit_and_run=hit_and_run,
        info_hash=info_hash,
        download_factor=0,
        upload_factor=1,
        match_score=0.95,
        match_reasons=["TMDB_ID_EXACT", "ACTIVE_SEEDERS"],
        warnings=[],
    )
    async with session_factory() as session:
        session.add(media)
        await session.flush()
        run = TorrentSearchRun(
            media_id=media.id,
            site_id="avistaz",
            status=WorkflowStatus.TORRENT_REVIEW,
            candidate_count=1,
        )
        session.add(run)
        await session.flush()
        record = TorrentCandidateRecord(
            search_run_id=run.id,
            site_id="avistaz",
            torrent_id=candidate.torrent_id,
            candidate_snapshot=candidate.model_dump(mode="json"),
            match_score=0.95,
            match_reasons=candidate.match_reasons,
            warnings=[],
        )
        session.add(record)
        await session.commit()
    return media, record


@pytest.mark.asyncio
async def test_same_info_hash_is_blocked_and_unknown_is_not_pass() -> None:
    settings = safe_settings()
    snapshot = approval_snapshot()
    adapter = FakeReadOnlyQb([qb_torrent()])
    blocked = await evaluate_preflight(adapter, snapshot, settings)
    assert blocked.overall_status == PreflightStatus.BLOCKED
    duplicate_check = next(
        check for check in blocked.checks if check.code == "DUPLICATE_INFO_HASH"
    )
    assert duplicate_check.status == PreflightStatus.BLOCKED
    assert adapter.list_calls == 0
    assert adapter.target_calls == [
        ("hashes", (INFO_HASH,)),
        ("recent", PREFLIGHT_RECENT_TORRENT_LIMIT),
        ("active", None),
    ]

    v2_hash = "b" * 64
    v2_torrent = qb_torrent(info_hash=v2_hash[:40]).model_copy(
        update={"infohash_v2": v2_hash}
    )
    v2_snapshot = snapshot.model_copy(update={"info_hash": v2_hash})
    v2_blocked = await evaluate_preflight(
        FakeReadOnlyQb([v2_torrent]), v2_snapshot, settings
    )
    v2_duplicate_check = next(
        check for check in v2_blocked.checks if check.code == "DUPLICATE_INFO_HASH"
    )
    assert v2_duplicate_check.status == PreflightStatus.BLOCKED

    avistaz_default_snapshot = snapshot.model_copy(
        update={"info_hash": "f" * 40, "hit_and_run": None}
    )
    avistaz_default = await evaluate_preflight(
        FakeReadOnlyQb([qb_torrent(info_hash="e" * 40)]),
        avistaz_default_snapshot,
        settings,
    )
    hnr_check = next(
        check for check in avistaz_default.checks if check.code == "HNR_KNOWN"
    )
    assert hnr_check.status == PreflightStatus.PASS
    assert hnr_check.details == {
        "hit_and_run": True,
        "source": "SITE_DEFAULT",
        "minimum_seeding_days": 7,
    }

    unknown_snapshot = snapshot.model_copy(
        update={
            "site_id": "nexus-test",
            "torrent_ref": "nexus-test:details:safe",
            "info_hash": None,
            "hit_and_run": None,
        }
    )
    unknown = await evaluate_preflight(
        FakeReadOnlyQb([qb_torrent(info_hash="f" * 40)]), unknown_snapshot, settings
    )
    assert unknown.overall_status == PreflightStatus.UNKNOWN


@pytest.mark.asyncio
async def test_preflight_uses_bounded_queries_and_marks_recent_no_match_as_warning() -> None:
    settings = safe_settings()
    existing = qb_torrent(info_hash="e" * 40, name="Other release", size=100)
    adapter = FakeReadOnlyQb([existing])

    result = await evaluate_preflight(adapter, approval_snapshot(), settings)

    assert result.overall_status == PreflightStatus.WARNING
    checks = {check.code: check for check in result.checks}
    assert checks["DUPLICATE_INFO_HASH"].status == PreflightStatus.PASS
    assert checks["POSSIBLE_DUPLICATE_RELEASE"].status == PreflightStatus.WARNING
    assert checks["POSSIBLE_DUPLICATE_RELEASE"].details == {
        "sample_limit": PREFLIGHT_RECENT_TORRENT_LIMIT,
        "sample_count": 1,
    }
    assert checks["ACTIVE_SEEDING"].status == PreflightStatus.PASS
    assert checks["ACTIVE_SEEDING"].details == {"count_at_least": 1}
    assert adapter.list_calls == 0
    assert adapter.target_calls == [
        ("hashes", (INFO_HASH,)),
        ("recent", PREFLIGHT_RECENT_TORRENT_LIMIT),
        ("active", None),
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("snapshot_updates", "deferred_checks", "expected_queries"),
    (
        pytest.param(
            {"info_hash": None},
            {
                "DUPLICATE_INFO_HASH": {
                    "deferred_validation": "TORRENT_FILE",
                    "duplicate_check_deferred": True,
                }
            },
            [
                ("recent", PREFLIGHT_RECENT_TORRENT_LIMIT),
                ("active", None),
            ],
            id="missing-info-hash",
        ),
        pytest.param(
            {"size_bytes": None},
            {
                "POSSIBLE_DUPLICATE_RELEASE": {
                    "deferred_validation": "TORRENT_FILE",
                    "duplicate_check_deferred": True,
                },
                "SIZE_LIMIT": {
                    "limit_bytes": 10_000,
                    "deferred_validation": "TORRENT_FILE",
                },
            },
            [
                ("hashes", (INFO_HASH,)),
                ("active", None),
            ],
            id="missing-size",
        ),
        pytest.param(
            {"seeders": None},
            {
                "CANDIDATE_SEEDERS": {
                    "deferred_validation": "PT_RESEARCH",
                }
            },
            [
                ("hashes", (INFO_HASH,)),
                ("recent", PREFLIGHT_RECENT_TORRENT_LIMIT),
                ("active", None),
            ],
            id="missing-seeders",
        ),
        pytest.param(
            {"info_hash": None, "size_bytes": None, "seeders": None},
            {
                "DUPLICATE_INFO_HASH": {
                    "deferred_validation": "TORRENT_FILE",
                    "duplicate_check_deferred": True,
                },
                "POSSIBLE_DUPLICATE_RELEASE": {
                    "deferred_validation": "TORRENT_FILE",
                    "duplicate_check_deferred": True,
                },
                "SIZE_LIMIT": {
                    "limit_bytes": 10_000,
                    "deferred_validation": "TORRENT_FILE",
                },
                "CANDIDATE_SEEDERS": {
                    "deferred_validation": "PT_RESEARCH",
                },
            },
            [("active", None)],
            id="all-recoverable-fields-missing",
        ),
    ),
)
async def test_recoverable_candidate_metadata_gaps_are_manual_warnings(
    snapshot_updates: dict[str, object],
    deferred_checks: dict[str, dict[str, object]],
    expected_queries: list[tuple[str, object]],
) -> None:
    existing = qb_torrent(info_hash="e" * 40, name="Other release", size=100)
    adapter = FakeReadOnlyQb([existing])

    result = await evaluate_preflight(
        adapter,
        approval_snapshot(**snapshot_updates),
        safe_settings(),
    )

    checks = {check.code: check for check in result.checks}
    assert result.overall_status == PreflightStatus.WARNING
    assert preflight_is_manually_passable(result) is True
    for code, expected_details in deferred_checks.items():
        assert checks[code].status == PreflightStatus.WARNING
        assert checks[code].details == expected_details
    assert adapter.target_calls == expected_queries


@pytest.mark.asyncio
async def test_recoverable_metadata_warnings_do_not_mask_environment_unknowns() -> None:
    class ConnectionFailureQb(FakeReadOnlyQb):
        async def authenticate(self) -> None:
            raise AppError("QB_CONNECTION_FAILED", "test connection failure")

    snapshot = approval_snapshot(info_hash=None, size_bytes=None, seeders=None)
    existing = qb_torrent(info_hash="e" * 40, name="Other release", size=100)

    active_read_failed = await evaluate_preflight(
        FakeReadOnlyQb([existing], query_failures=("active",)),
        snapshot,
        safe_settings(),
    )
    save_path_unknown = await evaluate_preflight(
        FakeReadOnlyQb([existing]),
        snapshot,
        safe_settings(qb_target_save_path=None),
    )
    size_policy_unknown = await evaluate_preflight(
        FakeReadOnlyQb([existing]),
        snapshot,
        safe_settings(max_candidate_size_bytes=None),
    )

    for result, unknown_code in (
        (active_read_failed, "ACTIVE_SEEDING"),
        (save_path_unknown, "SAVE_PATH_ALLOWED"),
        (size_policy_unknown, "SIZE_LIMIT"),
    ):
        checks = {check.code: check for check in result.checks}
        assert checks[unknown_code].status == PreflightStatus.UNKNOWN
        assert result.overall_status == PreflightStatus.UNKNOWN
        assert preflight_is_manually_passable(result) is False

    connection_failed = await evaluate_preflight(
        ConnectionFailureQb([existing]),
        snapshot,
        safe_settings(),
    )
    connection_check = next(
        check for check in connection_failed.checks if check.code == "QB_CONNECTION"
    )
    assert connection_check.status == PreflightStatus.BLOCKED
    assert connection_failed.overall_status == PreflightStatus.BLOCKED
    assert preflight_is_manually_passable(connection_failed) is False


@pytest.mark.asyncio
async def test_target_category_missing_is_warning_but_configuration_failures_stay_unknown() -> None:
    class MissingCategoryQb(FakeReadOnlyQb):
        async def get_categories(self) -> dict[str, QbCategory]:
            return {}

    class CategoryReadFailureQb(FakeReadOnlyQb):
        async def get_categories(self) -> dict[str, QbCategory]:
            raise AppError("QB_CATEGORY_LOOKUP_FAILED", "test category lookup failure")

    snapshot = approval_snapshot()
    existing = qb_torrent(info_hash="e" * 40, name="Other release", size=100)

    present = await evaluate_preflight(
        FakeReadOnlyQb([existing]), snapshot, safe_settings()
    )
    missing = await evaluate_preflight(
        MissingCategoryQb([existing]), snapshot, safe_settings()
    )
    unconfigured = await evaluate_preflight(
        FakeReadOnlyQb([existing]),
        snapshot,
        safe_settings(qb_target_category=None),
    )
    unavailable = await evaluate_preflight(
        CategoryReadFailureQb([existing]), snapshot, safe_settings()
    )

    present_check = next(check for check in present.checks if check.code == "TARGET_CATEGORY")
    missing_check = next(check for check in missing.checks if check.code == "TARGET_CATEGORY")
    unconfigured_check = next(
        check for check in unconfigured.checks if check.code == "TARGET_CATEGORY"
    )
    unavailable_check = next(
        check for check in unavailable.checks if check.code == "TARGET_CATEGORY"
    )

    assert present_check.status == PreflightStatus.PASS
    assert missing_check.status == PreflightStatus.WARNING
    assert "自动创建" in missing_check.message
    assert missing_check.details == {
        "category": "movies",
        "will_create_on_submit": True,
    }
    assert unconfigured_check.status == PreflightStatus.UNKNOWN
    assert unavailable_check.status == PreflightStatus.UNKNOWN


@pytest.mark.asyncio
async def test_preflight_reports_no_active_seeding_without_claiming_a_full_count() -> None:
    inactive = qb_torrent(info_hash="e" * 40).model_copy(
        update={"progress": 0.5, "state": "downloading", "upspeed": 0}
    )

    result = await evaluate_preflight(
        FakeReadOnlyQb([inactive]),
        approval_snapshot(),
        safe_settings(),
    )

    active_check = next(
        check for check in result.checks if check.code == "ACTIVE_SEEDING"
    )
    assert active_check.status == PreflightStatus.WARNING
    assert active_check.details == {"count": 0}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("failed_query", "check_code", "error_code"),
    (
        ("hashes", "DUPLICATE_INFO_HASH", "QB_HASH_LOOKUP_FAILED"),
        ("recent", "POSSIBLE_DUPLICATE_RELEASE", "QB_RECENT_LOOKUP_FAILED"),
        ("active", "ACTIVE_SEEDING", "QB_SEEDING_LOOKUP_FAILED"),
    ),
)
async def test_targeted_preflight_query_failure_is_unknown(
    failed_query: str,
    check_code: str,
    error_code: str,
) -> None:
    adapter = FakeReadOnlyQb(
        [qb_torrent(info_hash="e" * 40)],
        query_failures=(failed_query,),
    )

    result = await evaluate_preflight(adapter, approval_snapshot(), safe_settings())

    check = next(check for check in result.checks if check.code == check_code)
    assert check.status == PreflightStatus.UNKNOWN
    assert check.details == {"error_code": error_code}
    assert result.overall_status == PreflightStatus.UNKNOWN
    assert adapter.list_calls == 0


@pytest.mark.asyncio
async def test_avistaz_uses_seven_day_hnr_default_without_manual_acknowledgement(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    _, record = await seed_candidate(
        session_factory,
        info_hash="a" * 40,
        hit_and_run=None,
    )
    settings = safe_settings()
    async with session_factory() as session:
        approval = await create_approval_request(
            session,
            record.id,
            ApprovalCreateRequest(),
            settings,
            actor="operator",
        )
        snapshot = verify_snapshot(approval)
        assert snapshot.hit_and_run is True
        assert "HNR_UNKNOWN" not in snapshot.warnings

        passed = preflight_result(settings)
        approval.preflight_result = passed.model_dump(mode="json")
        approval.preflight_checked_at = passed.checked_at
        approved, plan = await approve_request(
            session,
            approval,
            ApprovalApproveRequest(
                acknowledges_hnr=False,
                acknowledges_seeding=True,
                acknowledges_plan_only=True,
            ),
            settings,
            actor="operator",
        )

        assert approved.status == ApprovalStatus.APPROVED
        assert "HNR_UNKNOWN" not in plan.warnings


@pytest.mark.asyncio
async def test_bounded_duplicate_warning_allows_manual_but_not_automatic_approval(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    _, record = await seed_candidate(session_factory, info_hash="a" * 40)
    settings = safe_settings()
    async with session_factory() as session:
        approval = await create_approval_request(
            session,
            record.id,
            ApprovalCreateRequest(),
            settings,
            actor="operator",
        )
        result = await evaluate_preflight(
            FakeReadOnlyQb(
                [qb_torrent(info_hash="b" * 40, name="Other release", size=100)]
            ),
            verify_snapshot(approval),
            settings,
        )
        assert result.overall_status == PreflightStatus.WARNING
        approval.preflight_result = result.model_dump(mode="json")
        approval.preflight_checked_at = result.checked_at

        with pytest.raises(AppError) as automatic:
            await approve_request_automatically(
                session,
                approval,
                settings,
                actor="automation",
                policy_revision_id="policy-revision",
                decision_id="decision",
            )
        assert automatic.value.error_code == "AUTOMATION_PREFLIGHT_NOT_PASS"
        assert approval.status == ApprovalStatus.PENDING

        approved_result, plan = await approve_request(
            session,
            approval,
            ApprovalApproveRequest(
                acknowledges_hnr=False,
                acknowledges_seeding=True,
                acknowledges_plan_only=True,
            ),
            settings,
            actor="operator",
        )
        assert approved_result.status.value == ApprovalStatus.APPROVED.value
        assert "POSSIBLE_DUPLICATE_RELEASE" in plan.warnings


@pytest.mark.asyncio
async def test_candidate_snapshot_is_immutable_and_duplicate_request_is_rejected(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    _, record = await seed_candidate(session_factory)
    settings = safe_settings()
    async with session_factory() as session:
        approval = await create_approval_request(
            session,
            record.id,
            ApprovalCreateRequest(),
            settings,
            actor="operator-a",
        )
        await session.commit()
        original_title = verify_snapshot(approval).release_title
        digest = approval.snapshot_hash

        stored_candidate = await session.get(TorrentCandidateRecord, record.id)
        assert stored_candidate is not None
        changed = dict(stored_candidate.candidate_snapshot)
        changed["release_title"] = "Changed after approval"
        stored_candidate.candidate_snapshot = changed
        await session.commit()

        assert verify_snapshot(approval).release_title == original_title
        assert approval.snapshot_hash == digest == snapshot_hash(approval.candidate_snapshot)
        with pytest.raises(AppError) as duplicate:
            await create_approval_request(
                session,
                record.id,
                ApprovalCreateRequest(),
                settings,
                actor="operator-b",
            )
        assert duplicate.value.error_code == "APPROVAL_REQUEST_DUPLICATE"


@pytest.mark.asyncio
async def test_approval_binding_and_orm_immutable_fields_are_enforced(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    _, record = await seed_candidate(session_factory)
    async with session_factory() as session:
        approval = await create_approval_request(
            session,
            record.id,
            ApprovalCreateRequest(),
            safe_settings(),
            actor="operator-a",
        )
        await session.commit()
        approval.expires_at += timedelta(minutes=1)
        with pytest.raises(AppError) as binding_error:
            verify_snapshot(approval)
        assert binding_error.value.error_code == "APPROVAL_SNAPSHOT_BINDING_INVALID"
        with pytest.raises(ValueError, match="immutable fields"):
            await session.commit()
        await session.rollback()


@pytest.mark.asyncio
async def test_expired_and_consumed_approvals_cannot_be_reused(
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, record = await seed_candidate(session_factory)
    settings = safe_settings()
    async with session_factory() as session:
        approval = await create_approval_request(
            session,
            record.id,
            ApprovalCreateRequest(),
            settings,
            actor="operator-a",
        )
        await session.flush()
        with monkeypatch.context() as expiry_clock:
            expiry_clock.setattr(
                approval_services,
                "utc_now",
                lambda: approval.expires_at + timedelta(seconds=1),
            )
            with pytest.raises(AppError) as expired:
                await approve_request(
                    session,
                    approval,
                    ApprovalApproveRequest(
                        acknowledges_hnr=True,
                        acknowledges_seeding=True,
                        acknowledges_plan_only=True,
                    ),
                    settings,
                    actor="operator-a",
                )
        assert expired.value.error_code == "APPROVAL_EXPIRED"
        assert approval.status == ApprovalStatus.EXPIRED

    _, second_record = await seed_candidate(session_factory, info_hash="e" * 40)
    async with session_factory() as session:
        approval = await create_approval_request(
            session,
            second_record.id,
            ApprovalCreateRequest(),
            settings,
            actor="operator-b",
        )
        pass_result = preflight_result(settings)
        approval.preflight_result = pass_result.model_dump(mode="json")
        approval.preflight_checked_at = pass_result.checked_at
        _, plan = await approve_request(
            session,
            approval,
            ApprovalApproveRequest(
                acknowledges_hnr=True,
                acknowledges_seeding=True,
                acknowledges_plan_only=True,
            ),
            settings,
            actor="operator-b",
        )
        await session.flush()
        assert isinstance(plan, DownloadPlan)
        assert plan.plan_hash == download_plan_hash(plan)
        assert verify_download_plan(plan, approval) is plan
        approval.status = ApprovalStatus.EXECUTING
        await session.flush()
        await consume_approval(session, approval, actor="future-executor-test")
        assert approval.status == ApprovalStatus.CONSUMED
        with pytest.raises(AppError) as consumed:
            await consume_approval(session, approval, actor="future-executor-test")
        assert consumed.value.error_code == "APPROVAL_ALREADY_CONSUMED"
        events = list(
            (
                await session.scalars(
                    select(ApprovalEvent).where(
                        ApprovalEvent.approval_request_id == approval.id
                    )
                )
            ).all()
        )
        assert {event.event_type for event in events} >= {"REQUESTED", "APPROVED", "CONSUMED"}


@pytest.mark.asyncio
async def test_unknown_or_blocked_preflight_cannot_generate_plan(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    _, record = await seed_candidate(session_factory)
    settings = safe_settings()
    async with session_factory() as session:
        approval = await create_approval_request(
            session,
            record.id,
            ApprovalCreateRequest(),
            settings,
            actor="operator",
        )
        for status in (PreflightStatus.UNKNOWN, PreflightStatus.BLOCKED):
            result = preflight_result(settings, status)
            approval.preflight_result = result.model_dump(mode="json")
            approval.preflight_checked_at = result.checked_at
            with pytest.raises(AppError) as caught:
                await approve_request(
                    session,
                    approval,
                    ApprovalApproveRequest(
                        acknowledges_hnr=True,
                        acknowledges_seeding=True,
                        acknowledges_plan_only=True,
                    ),
                    settings,
                    actor="operator",
                )
            assert caught.value.error_code == "PREFLIGHT_NOT_PASSABLE"
        assert await session.scalar(select(DownloadPlan)) is None


@pytest.mark.asyncio
async def test_preflight_requires_consistent_complete_checks_and_stable_policy(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    settings = safe_settings()
    with pytest.raises(ValueError, match="overall_status"):
        PreflightResult(
            overall_status=PreflightStatus.PASS,
            checked_at=datetime.now(UTC),
            policy_fingerprint=preflight_policy_fingerprint(settings),
            checks=[
                PreflightCheck(
                    code="QB_CONNECTION",
                    status=PreflightStatus.BLOCKED,
                    message="blocked",
                )
            ],
        )

    _, record = await seed_candidate(session_factory, info_hash="d" * 40)
    async with session_factory() as session:
        approval = await create_approval_request(
            session,
            record.id,
            ApprovalCreateRequest(),
            settings,
            actor="operator",
        )
        incomplete = PreflightResult(
            overall_status=PreflightStatus.PASS,
            checked_at=datetime.now(UTC),
            policy_fingerprint=preflight_policy_fingerprint(settings),
            checks=[
                PreflightCheck(
                    code="QB_CONNECTION", status=PreflightStatus.PASS, message="connected"
                )
            ],
        )
        approval.preflight_result = incomplete.model_dump(mode="json")
        approval.preflight_checked_at = incomplete.checked_at
        with pytest.raises(AppError) as incomplete_error:
            await approve_request(
                session,
                approval,
                ApprovalApproveRequest(
                    acknowledges_hnr=True,
                    acknowledges_seeding=True,
                    acknowledges_plan_only=True,
                ),
                settings,
                actor="operator",
            )
        assert incomplete_error.value.error_code == "PREFLIGHT_CHECKS_INCOMPLETE"

        passed = preflight_result(settings)
        approval.preflight_result = passed.model_dump(mode="json")
        approval.preflight_checked_at = passed.checked_at
        changed_settings = settings.model_copy(update={"qb_target_category": "changed"})
        with pytest.raises(AppError) as changed:
            await approve_request(
                session,
                approval,
                ApprovalApproveRequest(
                    acknowledges_hnr=True,
                    acknowledges_seeding=True,
                    acknowledges_plan_only=True,
                ),
                changed_settings,
                actor="operator",
            )
        assert changed.value.error_code == "PREFLIGHT_CONFIG_CHANGED"
        assert await session.scalar(select(DownloadPlan)) is None


@pytest.mark.asyncio
async def test_download_plan_hash_and_closed_content_detect_tampering(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    settings = safe_settings()
    _, record = await seed_candidate(session_factory, info_hash="c" * 40)
    async with session_factory() as session:
        approval = await create_approval_request(
            session,
            record.id,
            ApprovalCreateRequest(),
            settings,
            actor="operator",
        )
        passed = preflight_result(settings)
        approval.preflight_result = passed.model_dump(mode="json")
        approval.preflight_checked_at = passed.checked_at
        _, plan = await approve_request(
            session,
            approval,
            ApprovalApproveRequest(
                acknowledges_hnr=True,
                acknowledges_seeding=True,
                acknowledges_plan_only=True,
            ),
            settings,
            actor="operator",
        )
        original_ref = plan.torrent_ref
        plan.torrent_ref = "avistaz:details:changed"
        with pytest.raises(AppError) as tampered:
            verify_download_plan(plan, approval)
        assert tampered.value.error_code == "DOWNLOAD_PLAN_TAMPERED"

        plan.torrent_ref = "https://avistaz.to/download/secret"
        plan.plan_hash = download_plan_hash(plan)
        with pytest.raises(AppError) as unsafe:
            verify_download_plan(plan, approval)
        assert unsafe.value.error_code == "DOWNLOAD_PLAN_CONTENT_INVALID"

        plan.torrent_ref = original_ref
        plan.plan_hash = download_plan_hash(plan)
        plan.category = "mutated-after-creation"
        with pytest.raises(ValueError, match="immutable approval audit"):
            await session.commit()
        await session.rollback()
