from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models.enums import MediaType
from app.simple.models import (
    AutomationJobState,
    AutomationRunState,
    DownloadState,
    EpisodeState,
    MediaState,
    SearchState,
)
from app.simple.regions import NextFindRegion


class Page(BaseModel):
    items: list[object]
    total: int
    page: int
    page_size: int


class EpisodeView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    season_number: int
    episode_number: int
    title: str | None
    air_date: datetime | None
    state: EpisodeState


class MediaSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    source: str
    source_item_id: str
    media_type: MediaType
    tmdb_id: int | None
    title: str
    original_title: str | None
    country_codes: list[str]
    original_language: str | None
    regions: list[NextFindRegion]
    year: int | None
    poster_path: str | None
    state: MediaState
    attention_reason: str | None
    discovered_at: datetime
    updated_at: datetime


class MediaFilterOptions(BaseModel):
    media_types: list[MediaType]
    regions: list[NextFindRegion]
    states: list[MediaState]
    years: list[int]


class MediaPage(BaseModel):
    items: list[MediaSummary]
    total: int
    page: int
    page_size: int
    filter_options: MediaFilterOptions


class SearchCreate(BaseModel):
    site_ids: list[str] = Field(min_length=1, max_length=10)

    @field_validator("site_ids")
    @classmethod
    def normalize_sites(cls, values: list[str]) -> list[str]:
        normalized = list(dict.fromkeys(value.strip().lower() for value in values if value.strip()))
        if not normalized:
            raise ValueError("at least one site is required")
        return normalized


class SearchView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    media_id: str
    site_ids: list[str]
    state: SearchState
    error_message: str | None
    created_at: datetime
    finished_at: datetime | None


class CandidateView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    search_id: str
    site_id: str
    torrent_id: str
    title: str
    details_url: str | None = None
    size_bytes: int | None
    seeders: int | None
    resolution: str | None
    source: str | None
    codec: str | None
    download_factor: float | None
    collection_type: str | None
    file_count: int | None
    season_coverage: list[int]
    episode_coverage: list[str]
    score: float
    reasons: list[str]
    warnings: list[str]


class SearchDetail(SearchView):
    candidates: list[CandidateView] = Field(default_factory=list)


class MediaDetail(MediaSummary):
    episodes: list[EpisodeView] = Field(default_factory=list)
    latest_search: SearchView | None = None


class DownloadCreate(BaseModel):
    confirm_warnings: bool = False


class DownloadView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    media_id: str
    candidate_id: str
    info_hash: str | None
    name: str
    state: DownloadState
    progress: float
    download_speed: int
    upload_speed: int
    ratio: float
    error_message: str | None
    created_at: datetime
    updated_at: datetime


class DownloadPage(BaseModel):
    items: list[DownloadView]
    total: int
    page: int
    page_size: int


class SyncResult(BaseModel):
    created: int = 0
    updated: int = 0


class IdentityRequest(BaseModel):
    tmdb_id: int | None = Field(default=None, gt=0)


class AutomationPolicyUpdate(BaseModel):
    enabled: bool = False
    dry_run: bool = True
    auto_identify: bool = True
    scope_mode: Literal["filters", "selected"] = "filters"
    regions: list[NextFindRegion] = Field(default_factory=list, max_length=6)
    selected_media_ids: list[str] = Field(default_factory=list, max_length=500)
    site_ids: list[str] = Field(default_factory=lambda: ["avistaz"], min_length=1, max_length=10)
    media_types: list[MediaType] = Field(
        default_factory=lambda: [MediaType.MOVIE, MediaType.TV], min_length=1
    )
    minimum_score: float = Field(default=0.7, ge=0, le=1)
    minimum_seeders: int = Field(default=1, ge=0)
    max_size_bytes: int | None = Field(default=None, gt=0)
    allow_warnings: bool = False
    interval_minutes: int = Field(default=60, ge=5, le=24 * 60)
    retry_delay_minutes: int = Field(default=30, ge=1, le=24 * 60)
    max_attempts: int = Field(default=3, ge=1, le=10)
    daily_download_limit: int = Field(default=3, ge=1, le=100)
    daily_download_bytes: int | None = Field(default=None, gt=0)

    @field_validator("site_ids")
    @classmethod
    def normalize_policy_sites(cls, values: list[str]) -> list[str]:
        normalized = list(dict.fromkeys(value.strip().lower() for value in values if value.strip()))
        if not normalized:
            raise ValueError("at least one site is required")
        return normalized

    @field_validator("selected_media_ids")
    @classmethod
    def normalize_selected_media_ids(cls, values: list[str]) -> list[str]:
        return list(dict.fromkeys(value.strip() for value in values if value.strip()))

    @model_validator(mode="after")
    def require_manual_selection(self) -> AutomationPolicyUpdate:
        if self.scope_mode == "selected" and not self.selected_media_ids:
            raise ValueError("manual selection requires at least one media item")
        return self


class AutomationPolicyView(AutomationPolicyUpdate):
    model_config = ConfigDict(from_attributes=True)

    updated_at: datetime
    last_run_at: datetime | None


class AutomationJobView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    run_id: str
    media_id: str
    media_title: str
    state: AutomationJobState
    search_id: str | None
    selected_candidate_id: str | None
    download_id: str | None
    retry_of_job_id: str | None
    trigger: str
    attempt_count: int
    next_attempt_at: datetime | None
    decision: dict[str, object]
    error_message: str | None
    created_at: datetime
    finished_at: datetime | None
    superseded_at: datetime | None


class AutomationJobPage(BaseModel):
    items: list[AutomationJobView]
    total: int
    page: int
    page_size: int


class AutomationRunView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    trigger: str
    state: AutomationRunState
    created: int = Field(validation_alias="created_count")
    succeeded: int = Field(validation_alias="succeeded_count")
    failed: int = Field(validation_alias="failed_count")
    deferred: int = Field(validation_alias="deferred_count")
    error_message: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class AutomationRunPage(BaseModel):
    """A paginated list of automation executions.

    A run is the unit shown in the automation history (rather than an
    individual media job).  Keep the same compact run representation used by
    the existing status endpoints so clients can use either endpoint without
    a translation layer.
    """

    items: list[AutomationRunView]
    total: int
    page: int
    page_size: int
