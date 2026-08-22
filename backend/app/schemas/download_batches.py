from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field, model_validator

from app.models.enums import (
    DownloadBatchItemStatus,
    DownloadBatchMode,
    DownloadBatchStatus,
    DownloadLaunchMode,
)


class DownloadBatchCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    media_ids: list[str] = Field(min_length=1, max_length=200)
    mode: DownloadBatchMode = DownloadBatchMode.AUTO_SAFE
    launch_mode: DownloadLaunchMode = DownloadLaunchMode.SCHEDULED_START
    site_id: str = Field(min_length=1, max_length=50)
    preferred_resolutions: list[str] = Field(default_factory=list, max_length=10)
    preferred_sources: list[str] = Field(default_factory=list, max_length=10)
    preferred_audio: list[str] = Field(default_factory=list, max_length=10)
    preferred_subtitles: list[str] = Field(default_factory=list, max_length=10)
    max_candidate_size_bytes: int | None = Field(default=None, ge=1)
    max_total_size_bytes: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def unique_media_ids(self) -> DownloadBatchCreateRequest:
        if len(set(self.media_ids)) != len(self.media_ids):
            raise ValueError("media_ids must be unique")
        if self.mode == DownloadBatchMode.SEARCH_ONLY:
            self.launch_mode = DownloadLaunchMode.ADD_PAUSED
        return self


class DownloadBatchItemResponse(BaseModel):
    id: str
    media_item_id: str
    title: str
    media_type: str
    year: int | None
    status: DownloadBatchItemStatus
    error_code: str | None
    error_message: str | None
    updated_at: datetime


class DownloadBatchResponse(BaseModel):
    id: str
    name: str
    mode: DownloadBatchMode
    launch_mode: DownloadLaunchMode
    status: DownloadBatchStatus
    site_id: str
    preferences: dict[str, object]
    max_total_size_bytes: int | None
    max_items: int
    created_by: str
    created_at: datetime
    updated_at: datetime
    counts: dict[str, int]
    items: list[DownloadBatchItemResponse]


class DownloadBatchSummaryResponse(BaseModel):
    id: str
    name: str
    mode: DownloadBatchMode
    launch_mode: DownloadLaunchMode
    status: DownloadBatchStatus
    site_id: str
    max_items: int
    created_by: str
    created_at: datetime
    updated_at: datetime
    counts: dict[str, int]
