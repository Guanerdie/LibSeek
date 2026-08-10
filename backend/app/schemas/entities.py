from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import (
    IdentityConfidence,
    JobStatus,
    MediaType,
    MetadataStatus,
    WorkflowStatus,
)
from app.schemas.adapters import MetadataRecord, TorrentCandidate
from app.schemas.common import OrmModel


class AuditEventResponse(OrmModel):
    id: str
    event_type: str
    entity_type: str
    entity_id: str
    sanitized_details: dict[str, object]
    created_at: datetime


class DiscoveryRunResponse(OrmModel):
    id: str
    source: str
    status: JobStatus
    started_at: datetime | None
    finished_at: datetime | None
    discovered_count: int
    created_count: int
    updated_count: int
    error_code: str | None
    error_message: str | None
    created_at: datetime


class DiscoveryRunCreated(DiscoveryRunResponse):
    deduplicated: bool


class DiscoveryRunDetail(DiscoveryRunResponse):
    audit_events: list[AuditEventResponse] = Field(default_factory=list)


class MediaItemResponse(OrmModel):
    id: str
    source: str
    source_item_id: str
    media_type: MediaType
    tmdb_id: int | None
    title: str
    original_title: str | None
    year: int | None
    poster_path: str | None
    raw_type: str | None
    local_episodes: int | None
    local_episode_matrix: dict[int, list[int]] | None
    total_episodes: int | None
    aired_episodes: int | None
    missing_episodes: list[str] | None
    discovery_status: str
    identity_confidence: IdentityConfidence
    metadata_status: MetadataStatus
    workflow_status: WorkflowStatus
    discovered_at: datetime
    updated_at: datetime


class ComponentStatus(OrmModel):
    healthy: bool
    message: str
    checked_at: datetime


class SystemStatusResponse(OrmModel):
    api: ComponentStatus
    worker: ComponentStatus
    postgres: ComponentStatus
    nextfind_configured: bool
    tmdb_configured: bool
    tmdb_live_enabled: bool
    avistaz_configured: bool
    avistaz_live_enabled: bool
    avistaz_status: str
    qb_configured: bool
    qb_read_only_enabled: bool
    qb_status: str


class ResolveAccepted(BaseModel):
    media_id: str
    job_id: str
    status: WorkflowStatus
    deduplicated: bool


class MetadataMatchResponse(OrmModel):
    id: str
    media_id: str
    tmdb_id: int
    rank: int
    score: float
    match_reasons: list[str]
    conflicts: list[str]
    candidate: MetadataRecord
    created_at: datetime


class IdentityConfirmationRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    metadata_match_id: str


class IdentityReviewResponse(OrmModel):
    id: str
    media_id: str
    metadata_match_id: str
    status: str
    confirmed_by: str
    candidate: MetadataRecord
    created_at: datetime


class TorrentSearchCreateRequest(BaseModel):
    preferred_resolutions: list[str] = Field(default_factory=list, max_length=10)
    preferred_sources: list[str] = Field(default_factory=list, max_length=10)
    preferred_audio: list[str] = Field(default_factory=list, max_length=10)
    preferred_subtitles: list[str] = Field(default_factory=list, max_length=10)
    max_size_bytes: int | None = Field(default=None, ge=1)


class TorrentSearchRunResponse(OrmModel):
    id: str
    media_id: str
    site_id: str
    status: WorkflowStatus
    strategy_log: list[dict[str, object]]
    sanitized_request: dict[str, object]
    candidate_count: int
    error_code: str | None
    error_message: str | None
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime


class TorrentSearchAccepted(TorrentSearchRunResponse):
    job_id: str
    deduplicated: bool


class TorrentCandidateResponse(OrmModel):
    id: str
    search_run_id: str
    candidate: TorrentCandidate
    match_score: float
    match_reasons: list[str]
    warnings: list[str]
    created_at: datetime
