from __future__ import annotations

from datetime import UTC, datetime, timedelta

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
    ApprovalCreateRequest,
    PreflightCheck,
    PreflightResult,
    validate_internal_torrent_ref,
)
from app.schemas.qbittorrent import QbCategory, QbTorrent, QbTorrentFile
from app.services.approvals import (
    approve_request,
    consume_approval,
    create_approval_request,
    download_plan_hash,
    snapshot_hash,
    verify_download_plan,
    verify_snapshot,
)
from app.services.preflight import (
    PREFLIGHT_CHECK_CODES,
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


class FakeReadOnlyQb(ReadOnlyDownloaderAdapter):
    def __init__(self, torrents: list[QbTorrent] | None = None) -> None:
        self.torrents = torrents or []
        self.auth_calls = 0

    def manifest(self) -> AdapterManifest:
        raise NotImplementedError

    async def authenticate(self) -> None:
        self.auth_calls += 1

    async def get_version(self) -> str:
        return "v5.0.4"

    async def get_web_api_version(self) -> str:
        return "2.11.4"

    async def list_torrents(self) -> list[QbTorrent]:
        return self.torrents

    async def get_torrent_files(self, info_hash: str) -> list[QbTorrentFile]:
        del info_hash
        return []

    async def get_categories(self) -> dict[str, QbCategory]:
        return {"movies": QbCategory(name="movies", savePath="/downloads/movies")}


def safe_settings() -> Settings:
    return Settings(
        _env_file=None,
        avistaz_forbidden_qb_versions=("4.3.*",),
        qb_target_category="movies",
        qb_target_save_path="/downloads/movies/incoming",
        qb_allowed_save_paths=("/downloads/movies",),
        qb_save_path_ref="movies-root",
        qb_plan_tags=("unin-plan",),
        max_candidate_size_bytes=10_000,
        approval_default_ttl_minutes=60,
        approval_preflight_max_age_seconds=300,
    )


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
    snapshot_data = {
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
    from app.schemas.approvals import ApprovalCandidateSnapshot

    snapshot = ApprovalCandidateSnapshot.model_validate(snapshot_data)
    blocked = await evaluate_preflight(FakeReadOnlyQb([qb_torrent()]), snapshot, settings)
    assert blocked.overall_status == PreflightStatus.BLOCKED
    duplicate_check = next(
        check for check in blocked.checks if check.code == "DUPLICATE_INFO_HASH"
    )
    assert duplicate_check.status == PreflightStatus.BLOCKED

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

    unknown_snapshot = snapshot.model_copy(update={"info_hash": None, "hit_and_run": None})
    unknown = await evaluate_preflight(
        FakeReadOnlyQb([qb_torrent(info_hash="f" * 40)]), unknown_snapshot, settings
    )
    assert unknown.overall_status == PreflightStatus.UNKNOWN
    assert unknown.overall_status != PreflightStatus.PASS


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
