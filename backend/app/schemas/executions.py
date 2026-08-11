from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.security import sanitize_public_text
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
    actual_info_hash: str | None
    actual_info_hash_v1: str | None
    actual_info_hash_v2: str | None
    actual_size_bytes: int | None
    actual_file_count: int | None
    validated_at: datetime | None
    submitted_at: datetime | None
    verified_at: datetime | None
    error_code: str | None
    error_message: str | None
    requested_by: str
    requested_at: datetime
    reconciliation_requested_by: str | None
    reconciliation_requested_at: datetime | None
    reconciliation_reason: str | None
    created_at: datetime
    updated_at: datetime

    @field_validator("error_message")
    @classmethod
    def replace_internal_error_message(cls, value: str | None) -> str | None:
        return "下载执行异常，请根据错误代码查看审计记录" if value is not None else None

    @field_validator("reconciliation_reason")
    @classmethod
    def redact_reconciliation_reason(cls, value: str | None) -> str | None:
        return sanitize_public_text(value) if value is not None else None
