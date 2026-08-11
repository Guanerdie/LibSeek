from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.enums import (
    ApprovalStatus,
    DownloadExecutionStatus,
    DownloadJobStatus,
    DownloadLaunchMode,
    HnrStatus,
    MediaType,
)


class DownloadJobResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")

    id: str
    execution_id: str
    approval_id: str
    media_item_id: str
    status: DownloadJobStatus
    release_title: str
    info_hash_v1: str | None = Field(default=None, pattern=r"^[0-9a-f]{40}$")
    info_hash_v2: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    save_path_ref: str
    category: str
    size_bytes: int = Field(ge=1)
    file_count: int = Field(ge=1)
    progress: float = Field(ge=0, le=1, allow_inf_nan=False)
    download_speed_bps: int = Field(ge=0)
    upload_speed_bps: int = Field(ge=0)
    downloaded_bytes: int = Field(ge=0)
    uploaded_bytes: int = Field(ge=0)
    ratio: float = Field(ge=-1, allow_inf_nan=False)
    hnr_status: HnrStatus
    started_at: datetime
    completed_at: datetime | None
    last_seen_at: datetime | None
    error_code: str | None
    error_message: str | None
    created_at: datetime
    updated_at: datetime

    @field_validator("error_message")
    @classmethod
    def replace_internal_error_message(cls, value: str | None) -> str | None:
        return "下载任务异常，请根据错误代码查看审计记录" if value is not None else None


class DownloadJobTimelineItemResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: Literal["approval", "execution", "job"]
    event_type: str
    from_status: str | None
    to_status: str
    actor: str
    sanitized_details: dict[str, object]
    created_at: datetime


class DownloadJobTimelineResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_id: str
    items: list[DownloadJobTimelineItemResponse]


class DownloadJobSummaryMediaResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    title: str
    media_type: MediaType
    tmdb_id: int | None
    year: int | None
    season: int | None = Field(default=None, ge=0)
    episodes: list[int] | None


class DownloadJobSummaryApprovalResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    status: ApprovalStatus
    snapshot_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    expires_at: datetime


class DownloadJobSummaryExecutionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    status: DownloadExecutionStatus
    launch_mode: DownloadLaunchMode
    requires_reconciliation: bool
    actual_info_hash_v1: str | None = Field(default=None, pattern=r"^[0-9a-f]{40}$")
    actual_info_hash_v2: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    validated_at: datetime | None
    submitted_at: datetime | None
    verified_at: datetime | None


class DownloadJobSummaryResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job: DownloadJobResponse
    media: DownloadJobSummaryMediaResponse
    approval: DownloadJobSummaryApprovalResponse
    execution: DownloadJobSummaryExecutionResponse
    warnings: list[str]
