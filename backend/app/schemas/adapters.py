from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from app.core.episodes import EpisodeMatrix, normalize_episode_codes, normalize_episode_matrix
from app.models.enums import IdentityConfidence, MediaType, MetadataStatus

EpisodeCode = Annotated[str, StringConstraints(pattern=r"^S\d{2}E\d{2,5}$")]
SiteId = Annotated[
    str,
    StringConstraints(
        strict=True,
        min_length=1,
        max_length=24,
        pattern=r"^[a-z0-9](?:[a-z0-9-]{0,22}[a-z0-9])?$",
    ),
]


def normalize_country_codes(value: object) -> list[str] | None:
    if value is None:
        return None
    values = value if isinstance(value, (list, tuple, set)) else [value]
    result: list[str] = []
    for item in values:
        if isinstance(item, dict):
            item = item.get("iso_3166_1")
        if not isinstance(item, str):
            continue
        code = item.strip().upper()
        if len(code) != 2 or not code.isascii() or not code.isalpha():
            continue
        if code not in result:
            result.append(code)
    return result or None


def normalize_language_code(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    code = value.strip().lower()
    if len(code) != 2 or not code.isascii() or not code.isalpha():
        return None
    return code


class PtSearchMode(StrEnum):
    TMDB_ID = "TMDB_ID"
    IMDB_ID = "IMDB_ID"
    TEXT = "TEXT"


class PtSiteCatalogEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    site_id: SiteId
    display_name: str = Field(min_length=1, max_length=100)
    description: str = Field(min_length=1, max_length=300)
    available_for_search: bool
    unavailable_reason_code: str | None
    unavailable_reason_message: str | None
    mode: Literal["LIVE_READ_ONLY_SEARCH", "DISABLED_BY_DEFAULT"]
    search_modes: tuple[PtSearchMode, ...]
    media_types: tuple[MediaType, ...]
    manual_only: bool
    promotion_metadata: bool
    hit_and_run_metadata: bool
    torrent_fetch_enabled: bool


class PtSiteCatalogResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    default_site_id: SiteId | None
    sites: tuple[PtSiteCatalogEntry, ...]


class AdapterManifest(BaseModel):
    id: str
    name: str
    adapter_type: str
    version: str
    enabled: bool
    mode: str
    description: str
    capabilities: dict[str, bool] = Field(default_factory=dict)


class ProbeResult(BaseModel):
    healthy: bool
    error_code: str | None = None
    message: str


class DiscoveryWarning(BaseModel):
    line_number: int | None = None
    error_code: str
    message: str


class MediaItemData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: str
    source_item_id: str
    media_type: MediaType
    tmdb_id: int | None = None
    title: str
    original_title: str | None = None
    country_codes: list[str] | None = None
    original_language: str | None = None
    year: int | None = Field(default=None, ge=1870, le=2200)
    poster_path: str | None = None
    raw_type: str | None = None
    local_episodes: int | None = Field(default=None, ge=0)
    local_episode_matrix: EpisodeMatrix | None = None
    total_episodes: int | None = Field(default=None, ge=0)
    aired_episodes: int | None = Field(default=None, ge=0)
    missing_episodes: list[EpisodeCode] | None = None
    identity_confidence: IdentityConfidence
    metadata_status: MetadataStatus
    discovered_at: datetime
    updated_at: datetime

    @field_validator("local_episode_matrix", mode="before")
    @classmethod
    def validate_local_episode_matrix(cls, value: object) -> EpisodeMatrix | None:
        return normalize_episode_matrix(value)

    @field_validator("missing_episodes", mode="before")
    @classmethod
    def validate_missing_episodes(cls, value: object) -> list[str] | None:
        return normalize_episode_codes(value)

    @field_validator("country_codes", mode="before")
    @classmethod
    def validate_country_codes(cls, value: object) -> list[str] | None:
        return normalize_country_codes(value)

    @field_validator("original_language", mode="before")
    @classmethod
    def validate_original_language(cls, value: object) -> str | None:
        return normalize_language_code(value)


class MediaDiscoveryResult(BaseModel):
    items: list[MediaItemData]
    warnings: list[DiscoveryWarning] = Field(default_factory=list)


class LibraryDetails(BaseModel):
    tmdb_id: int
    media_type: MediaType
    local_episodes: int | None = Field(default=None, ge=0)
    local_episode_matrix: EpisodeMatrix | None = None
    total_episodes: int | None = Field(default=None, ge=0)
    aired_episodes: int | None = Field(default=None, ge=0)
    missing_episodes: list[EpisodeCode] | None = None

    @field_validator("local_episode_matrix", mode="before")
    @classmethod
    def validate_local_episode_matrix(cls, value: object) -> EpisodeMatrix | None:
        return normalize_episode_matrix(value)

    @field_validator("missing_episodes", mode="before")
    @classmethod
    def validate_missing_episodes(cls, value: object) -> list[str] | None:
        return normalize_episode_codes(value)


class SeasonRecord(BaseModel):
    """One season's airing status, as the metadata provider reports it.

    ``is_complete`` is what makes an airing series automatable: a season whose
    every episode has already aired can be replaced by a season pack, while the
    season currently going out cannot -- a "complete" pack for it would be
    either mislabelled or short.
    """

    model_config = ConfigDict(extra="ignore")

    season_number: int = Field(ge=0)
    episode_count: int = Field(default=0, ge=0)
    aired_episode_count: int = Field(default=0, ge=0)
    last_air_date: date | None = None
    is_complete: bool = False


class MetadataRecord(BaseModel):
    tmdb_id: int
    imdb_id: str | None = None
    media_type: MediaType
    title: str
    chinese_title: str | None = None
    english_title: str | None = None
    original_title: str | None = None
    original_language: str | None = None
    country_codes: list[str] | None = None
    aliases: list[str] = Field(default_factory=list)
    year: int | None = None
    number_of_seasons: int | None = Field(default=None, ge=0)
    number_of_episodes: int | None = Field(default=None, ge=0)
    episode_matrix: dict[int, list[int]] | None = None
    seasons: list[SeasonRecord] = Field(default_factory=list)
    poster_path: str | None = None
    backdrop_path: str | None = None
    status: str | None = None
    confidence: float = Field(default=0, ge=0, le=1)
    external_ids: dict[str, str] = Field(default_factory=dict)

    @field_validator("episode_matrix", mode="before")
    @classmethod
    def validate_episode_matrix(cls, value: object) -> EpisodeMatrix | None:
        return normalize_episode_matrix(value)

    @field_validator("country_codes", mode="before")
    @classmethod
    def validate_country_codes(cls, value: object) -> list[str] | None:
        return normalize_country_codes(value)


class TorrentCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    site_id: SiteId
    torrent_id: str = Field(
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._~-]{0,127}$",
    )
    release_title: str = Field(validation_alias=AliasChoices("release_title", "title"))
    details_ref: str = Field(
        min_length=5,
        max_length=180,
        pattern=(
            r"^[a-z0-9][a-z0-9-]{0,23}:"
            r"[a-z0-9][a-z0-9_-]{0,49}:"
            r"[A-Za-z0-9][A-Za-z0-9._~-]{0,127}$"
        ),
    )
    media_type: MediaType
    tmdb_id: int | None = None
    imdb_id: str | None = None
    year: int | None = Field(default=None, ge=1870, le=2200)
    season: int | None = None
    episodes: list[int] | None = None
    collection_type: str | None = None
    resolution: str | None = None
    source: str | None = None
    codec: str | None = None
    hdr: list[str] | None = None
    audio: list[str] | None = None
    subtitles: list[str] | None = None
    size_bytes: int | None = Field(default=None, ge=0)
    file_count: int | None = Field(default=None, ge=0)
    seeders: int | None = Field(default=None, ge=0)
    leechers: int | None = Field(default=None, ge=0)
    completed: int | None = Field(default=None, ge=0)
    download_factor: float | None = Field(default=None, ge=0)
    upload_factor: float | None = Field(default=None, ge=0)
    hit_and_run: bool | None = None
    info_hash: str | None = None
    published_at: datetime | None = None
    match_score: float | None = Field(default=None, ge=0, le=1)
    match_reasons: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

    @property
    def title(self) -> str:
        return self.release_title

    @model_validator(mode="after")
    def details_ref_must_match_site(self) -> TorrentCandidate:
        if not self.details_ref.startswith(f"{self.site_id}:"):
            raise ValueError("details_ref must match site_id")
        return self


class TorrentSearchRequest(BaseModel):
    tmdb: int | None = None
    imdb: str | None = None
    tvdb: int | None = None
    search: str | None = None
    type: MediaType | None = None
    page: int = Field(default=1, ge=1, le=1000)
    limit: int = Field(default=50, ge=1, le=100)
    video_quality: list[str] = Field(default_factory=list)
    language: list[str] = Field(default_factory=list)
    subtitle: list[str] = Field(default_factory=list)
    discount: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)


class TorrentDetails(BaseModel):
    candidate: TorrentCandidate
    description: str | None = None
    files: list[dict[str, Any]] = Field(default_factory=list)
