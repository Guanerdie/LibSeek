from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from uuid import uuid4

from sqlalchemy import (
    JSON,
    BigInteger,
    CheckConstraint,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.time import utc_now
from app.db.base import Base
from app.models.enums import MediaType
from app.simple.regions import NextFindRegion, nextfind_regions


def new_id() -> str:
    return str(uuid4())


class MediaState(StrEnum):
    MISSING = "MISSING"
    IDENTIFYING = "IDENTIFYING"
    READY = "READY"
    SEARCHING = "SEARCHING"
    CANDIDATES = "CANDIDATES"
    DOWNLOADING = "DOWNLOADING"
    COMPLETE = "COMPLETE"
    NEEDS_ATTENTION = "NEEDS_ATTENTION"


class EpisodeState(StrEnum):
    MISSING = "MISSING"
    AVAILABLE = "AVAILABLE"
    DOWNLOADING = "DOWNLOADING"


class SearchState(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


class DownloadState(StrEnum):
    SUBMITTING = "SUBMITTING"
    QUEUED = "QUEUED"
    DOWNLOADING = "DOWNLOADING"
    PAUSED = "PAUSED"
    SEEDING = "SEEDING"
    COMPLETED = "COMPLETED"
    ERROR = "ERROR"
    OUTCOME_UNKNOWN = "OUTCOME_UNKNOWN"


class AutomationJobState(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    RETRY_WAIT = "RETRY_WAIT"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


class AutomationRunState(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


def enum_column(enum_type: type[StrEnum], length: int) -> Enum:
    return Enum(
        enum_type,
        native_enum=False,
        length=length,
        values_callable=lambda members: [member.value for member in members],
    )


class LibraryMediaItem(Base):
    __tablename__ = "library_media"
    __table_args__ = (
        Index("uq_library_media_source_item", "source", "source_item_id", unique=True),
        Index(
            "uq_library_media_tmdb_type",
            "tmdb_id",
            "media_type",
            unique=True,
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    source: Mapped[str] = mapped_column(String(50), nullable=False, default="nextfind")
    source_item_id: Mapped[str] = mapped_column(String(180), nullable=False)
    media_type: Mapped[MediaType] = mapped_column(enum_column(MediaType, 10), nullable=False)
    tmdb_id: Mapped[int | None] = mapped_column(Integer)
    imdb_id: Mapped[str | None] = mapped_column(String(20))
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    original_title: Mapped[str | None] = mapped_column(String(500))
    search_titles: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    country_codes: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    original_language: Mapped[str | None] = mapped_column(String(16))
    year: Mapped[int | None] = mapped_column(Integer)
    poster_path: Mapped[str | None] = mapped_column(Text)
    state: Mapped[MediaState] = mapped_column(
        enum_column(MediaState, 24), default=MediaState.MISSING, nullable=False, index=True
    )
    attention_reason: Mapped[str | None] = mapped_column(Text)
    discovered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )

    @property
    def regions(self) -> list[NextFindRegion]:
        return list(nextfind_regions(self.country_codes, self.original_language))


class Episode(Base):
    __tablename__ = "episodes"
    __table_args__ = (
        Index(
            "uq_media_episode_number",
            "media_id",
            "season_number",
            "episode_number",
            unique=True,
        ),
        CheckConstraint("season_number >= 0", name="ck_media_episode_season_nonnegative"),
        CheckConstraint("episode_number > 0", name="ck_media_episode_number_positive"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    media_id: Mapped[str] = mapped_column(
        ForeignKey("library_media.id", ondelete="CASCADE"), nullable=False, index=True
    )
    season_number: Mapped[int] = mapped_column(Integer, nullable=False)
    episode_number: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str | None] = mapped_column(String(500))
    air_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    state: Mapped[EpisodeState] = mapped_column(
        enum_column(EpisodeState, 20), default=EpisodeState.MISSING, nullable=False, index=True
    )


class ReleaseSearch(Base):
    __tablename__ = "searches"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    media_id: Mapped[str] = mapped_column(
        ForeignKey("library_media.id", ondelete="CASCADE"), nullable=False, index=True
    )
    site_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    state: Mapped[SearchState] = mapped_column(
        enum_column(SearchState, 16), default=SearchState.PENDING, nullable=False, index=True
    )
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False, index=True
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ReleaseCandidate(Base):
    __tablename__ = "release_candidates"
    __table_args__ = (
        Index(
            "uq_release_candidate_site_torrent",
            "search_id",
            "site_id",
            "torrent_id",
            unique=True,
        ),
        CheckConstraint("score >= 0 AND score <= 1", name="ck_release_candidate_score_range"),
        CheckConstraint(
            "size_bytes IS NULL OR size_bytes > 0",
            name="ck_release_candidate_size_positive",
        ),
        CheckConstraint(
            "seeders IS NULL OR seeders >= 0",
            name="ck_release_candidate_seeders_nonnegative",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    search_id: Mapped[str] = mapped_column(
        ForeignKey("searches.id", ondelete="CASCADE"), nullable=False, index=True
    )
    site_id: Mapped[str] = mapped_column(String(50), nullable=False)
    torrent_id: Mapped[str] = mapped_column(String(180), nullable=False)
    title: Mapped[str] = mapped_column(String(1000), nullable=False)
    size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    seeders: Mapped[int | None] = mapped_column(Integer)
    resolution: Mapped[str | None] = mapped_column(String(40))
    source: Mapped[str | None] = mapped_column(String(80))
    codec: Mapped[str | None] = mapped_column(String(80))
    download_factor: Mapped[float | None] = mapped_column(Float)
    season_coverage: Mapped[list[int]] = mapped_column(JSON, default=list, nullable=False)
    episode_coverage: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    score: Mapped[float] = mapped_column(Float, nullable=False)
    reasons: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    warnings: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    info_hash: Mapped[str | None] = mapped_column(String(64), index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )


class Download(Base):
    __tablename__ = "downloads"
    __table_args__ = (
        Index("uq_download_candidate", "candidate_id", unique=True),
        Index("uq_download_info_hash", "info_hash", unique=True),
        CheckConstraint("progress >= 0 AND progress <= 1", name="ck_download_progress_range"),
        CheckConstraint("download_speed >= 0", name="ck_download_speed_nonnegative"),
        CheckConstraint("upload_speed >= 0", name="ck_upload_speed_nonnegative"),
        CheckConstraint("ratio >= 0", name="ck_download_ratio_nonnegative"),
        CheckConstraint(
            "content_size_bytes IS NULL OR content_size_bytes > 0",
            name="ck_download_content_size_positive",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    media_id: Mapped[str] = mapped_column(
        ForeignKey("library_media.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    candidate_id: Mapped[str] = mapped_column(
        ForeignKey("release_candidates.id", ondelete="RESTRICT"), nullable=False
    )
    info_hash: Mapped[str | None] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(1000), nullable=False)
    content_size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    state: Mapped[DownloadState] = mapped_column(
        enum_column(DownloadState, 24), default=DownloadState.SUBMITTING, nullable=False, index=True
    )
    progress: Mapped[float] = mapped_column(Float, default=0, nullable=False)
    download_speed: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    upload_speed: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    ratio: Mapped[float] = mapped_column(Float, default=0, nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False, index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )


class AutomationPolicy(Base):
    __tablename__ = "automation_policy"
    __table_args__ = (
        CheckConstraint("minimum_score >= 0 AND minimum_score <= 1", name="ck_automation_score"),
        CheckConstraint("minimum_seeders >= 0", name="ck_automation_seeders"),
        CheckConstraint(
            "max_size_bytes IS NULL OR max_size_bytes > 0", name="ck_automation_max_size"
        ),
        CheckConstraint("interval_minutes >= 5", name="ck_automation_interval"),
        CheckConstraint("retry_delay_minutes >= 1", name="ck_automation_retry_delay"),
        CheckConstraint("max_attempts >= 1", name="ck_automation_attempts"),
        CheckConstraint("daily_download_limit >= 1", name="ck_automation_daily_limit"),
        CheckConstraint(
            "daily_download_bytes IS NULL OR daily_download_bytes > 0",
            name="ck_automation_daily_bytes",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default="default")
    enabled: Mapped[bool] = mapped_column(default=False, nullable=False)
    dry_run: Mapped[bool] = mapped_column(default=True, nullable=False)
    auto_identify: Mapped[bool] = mapped_column(default=True, nullable=False)
    scope_mode: Mapped[str] = mapped_column(String(16), default="filters", nullable=False)
    regions: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    selected_media_ids: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    site_ids: Mapped[list[str]] = mapped_column(JSON, default=lambda: ["avistaz"], nullable=False)
    media_types: Mapped[list[str]] = mapped_column(
        JSON, default=lambda: [MediaType.MOVIE.value, MediaType.TV.value], nullable=False
    )
    minimum_score: Mapped[float] = mapped_column(Float, default=0.7, nullable=False)
    minimum_seeders: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    max_size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    allow_warnings: Mapped[bool] = mapped_column(default=False, nullable=False)
    interval_minutes: Mapped[int] = mapped_column(Integer, default=60, nullable=False)
    retry_delay_minutes: Mapped[int] = mapped_column(Integer, default=30, nullable=False)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
    daily_download_limit: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
    daily_download_bytes: Mapped[int | None] = mapped_column(BigInteger)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )


class AutomationRun(Base):
    __tablename__ = "automation_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    trigger: Mapped[str] = mapped_column(String(20), default="manual", nullable=False)
    state: Mapped[AutomationRunState] = mapped_column(
        enum_column(AutomationRunState, 16),
        default=AutomationRunState.PENDING,
        nullable=False,
        index=True,
    )
    created_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    succeeded_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    failed_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    deferred_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False, index=True
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AutomationJob(Base):
    __tablename__ = "automation_jobs"
    __table_args__ = (
        Index("uq_automation_job_run_media", "run_id", "media_id", unique=True),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    run_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    media_id: Mapped[str] = mapped_column(
        ForeignKey("library_media.id", ondelete="CASCADE"), nullable=False, index=True
    )
    state: Mapped[AutomationJobState] = mapped_column(
        enum_column(AutomationJobState, 16),
        default=AutomationJobState.PENDING,
        nullable=False,
        index=True,
    )
    search_id: Mapped[str | None] = mapped_column(
        ForeignKey("searches.id", ondelete="SET NULL"), index=True
    )
    selected_candidate_id: Mapped[str | None] = mapped_column(
        ForeignKey("release_candidates.id", ondelete="SET NULL")
    )
    download_id: Mapped[str | None] = mapped_column(
        ForeignKey("downloads.id", ondelete="SET NULL"), index=True
    )
    trigger: Mapped[str] = mapped_column(String(20), default="manual", nullable=False)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    decision: Mapped[dict[str, object]] = mapped_column(JSON, default=dict, nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False, index=True
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ActivityLog(Base):
    __tablename__ = "activity_log"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    media_id: Mapped[str | None] = mapped_column(
        ForeignKey("library_media.id", ondelete="CASCADE"), index=True
    )
    event: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    details: Mapped[dict[str, object]] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False, index=True
    )
