from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import (
    DownloadExecutionStatus,
    DownloadLaunchMode,
    ExecutionIntentStatus,
)


class ExecutionIntentCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    launch_mode: DownloadLaunchMode = DownloadLaunchMode.ADD_PAUSED
    expires_in_seconds: int | None = Field(default=None, ge=30, le=3600)


class ExecutionIntentCreateResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    approval_id: str
    nonce: str = Field(pattern=r"^ei1_[A-Za-z0-9_-]{32,200}$")
    status: ExecutionIntentStatus
    approval_snapshot_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    plan_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    qb_target_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    launch_mode: DownloadLaunchMode
    expires_at: datetime
    created_at: datetime


class DownloadExecutionCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intent_id: str = Field(min_length=1, max_length=80)
    nonce: str = Field(pattern=r"^ei1_[A-Za-z0-9_-]{32,200}$")


class DownloadExecutionReconcileRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str | None = Field(default=None, max_length=1000)


class DownloadExecutionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    approval_id: str
    intent_id: str
    status: DownloadExecutionStatus
    requires_reconciliation: bool
    approval_snapshot_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    plan_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    qb_target_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    launch_mode: DownloadLaunchMode
    attempts: int = Field(ge=0)
    max_attempts: int = Field(ge=1)
    next_retry_at: datetime | None
    locked_at: datetime | None
    locked_by: str | None
    actual_info_hash: str | None
    error_code: str | None
    error_message: str | None
    requested_by: str
    requested_at: datetime
    reconciliation_requested_by: str | None
    reconciliation_requested_at: datetime | None
    reconciliation_reason: str | None
    created_at: datetime
    updated_at: datetime
