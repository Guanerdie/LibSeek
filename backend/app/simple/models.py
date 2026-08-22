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
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    original_title: Mapped[str | None] = mapped_column(String(500))
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
