from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.enums import MediaType
from app.simple.models import DownloadState, EpisodeState, MediaState, SearchState
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
    size_bytes: int | None
    seeders: int | None
    resolution: str | None
    source: str | None
    codec: str | None
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
