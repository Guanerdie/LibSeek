from __future__ import annotations

import re
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models.enums import ApprovalStatus, MediaType, PreflightStatus
from app.schemas.common import OrmModel


class PromotionSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    download_factor: float | None = Field(default=None, ge=0)
    upload_factor: float | None = Field(default=None, ge=0)


class ApprovalCandidateSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    media_item_id: str
    media_title: str
    media_type: MediaType
    tmdb_id: int | None
    year: int | None
    torrent_candidate_id: str
    site_id: str
    torrent_id: str
    torrent_ref: str
    release_title: str
    size_bytes: int | None = Field(default=None, ge=0)
    info_hash: str | None = None
    season: int | None = Field(default=None, ge=0)
    episodes: list[int] | None = None
    resolution: str | None = None
    source: str | None = None
    subtitles: list[str] | None = None
    seeders: int | None = Field(default=None, ge=0)
    promotion: PromotionSnapshot
    hit_and_run: bool | None
    match_score: float = Field(ge=0, le=1)
    match_reasons: list[str]
    warnings: list[str]
    requested_at: datetime
    expires_at: datetime

    @field_validator("torrent_ref")
    @classmethod
    def torrent_ref_is_internal(cls, value: str) -> str:
        return validate_internal_torrent_ref(value)

    @model_validator(mode="after")
    def torrent_ref_matches_site(self) -> ApprovalCandidateSnapshot:
        if not self.torrent_ref.casefold().startswith(f"{self.site_id.casefold()}:"):
            raise ValueError("torrent_ref must match site_id")
        return self

    @field_validator("info_hash")
    @classmethod
    def info_hash_is_valid(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not re.fullmatch(r"[0-9a-fA-F]{40}(?:[0-9a-fA-F]{24})?", value):
            raise ValueError("info_hash must be 40 or 64 hexadecimal characters")
        return value.lower()


class ApprovalCreateRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    expires_in_minutes: int | None = Field(default=None, ge=5, le=10_080)


class ApprovalDecisionRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")


class ApprovalApproveRequest(ApprovalDecisionRequest):
    acknowledges_hnr: bool
    acknowledges_seeding: bool
    acknowledges_plan_only: bool


class ApprovalRejectRequest(ApprovalDecisionRequest):
    reason: str | None = Field(default=None, max_length=1000)


class ApprovalRevokeRequest(ApprovalDecisionRequest):
    reason: str | None = Field(default=None, max_length=1000)


class PreflightCheck(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(pattern=r"^[A-Z][A-Z0-9_]{1,79}$")
    status: PreflightStatus
    message: str = Field(min_length=1, max_length=500)
    details: dict[str, object] = Field(default_factory=dict)


class PreflightResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    overall_status: PreflightStatus
    checks: list[PreflightCheck]
    checked_at: datetime
    policy_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_summary(self) -> PreflightResult:
        if not self.checks:
            raise ValueError("preflight checks must not be empty")
        codes = [check.code for check in self.checks]
        if len(codes) != len(set(codes)):
            raise ValueError("preflight check codes must be unique")
        expected = next(
            status
            for status in (
                PreflightStatus.BLOCKED,
                PreflightStatus.UNKNOWN,
                PreflightStatus.WARNING,
                PreflightStatus.PASS,
            )
            if any(check.status == status for check in self.checks)
        )
        if self.overall_status != expected:
            raise ValueError("preflight overall_status does not match checks")
        if self.checked_at.tzinfo is None:
            raise ValueError("preflight checked_at must include a timezone")
        return self


class MediaDestinationPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["PLAN_ONLY_NO_FILE_OPERATION"]
    media_item_id: str
    media_type: MediaType
    tmdb_id: int | None
    season: int | None = Field(default=None, ge=0)
    episodes: list[int] | None = None


class ApprovalEventResponse(OrmModel):
    id: str
    event_type: str
    from_status: str | None
    to_status: str
    actor: str
    reason: str | None
    sanitized_details: dict[str, object]
    created_at: datetime


class ApprovalResponse(BaseModel):
    id: str
    media_item_id: str
    torrent_candidate_id: str
    status: ApprovalStatus
    candidate: ApprovalCandidateSnapshot
    snapshot_hash: str
    requested_by: str
    requested_at: datetime
    expires_at: datetime
    decided_at: datetime | None
    preflight_result: PreflightResult | None
    preflight_checked_at: datetime | None
    events: list[ApprovalEventResponse] = Field(default_factory=list)


class DownloadPlanResponse(OrmModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")

    id: str
    approval_id: str
    approval_snapshot_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    preflight_policy_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    plan_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    site_id: str
    torrent_ref: str
    expected_info_hash: str | None
    release_title: str
    save_path_ref: str
    category: str
    tags: list[str]
    estimated_size_bytes: int | None
    media_destination_plan: MediaDestinationPlan
    preflight_result: PreflightResult
    warnings: list[str]
    created_at: datetime

    @field_validator("torrent_ref")
    @classmethod
    def torrent_ref_is_internal(cls, value: str) -> str:
        return validate_internal_torrent_ref(value)

    @model_validator(mode="after")
    def validate_bindings(self) -> DownloadPlanResponse:
        if not self.torrent_ref.casefold().startswith(f"{self.site_id.casefold()}:"):
            raise ValueError("torrent_ref must match site_id")
        if self.preflight_result.policy_fingerprint != self.preflight_policy_fingerprint:
            raise ValueError("preflight policy fingerprint mismatch")
        return self


_INTERNAL_TORRENT_REF = re.compile(
    r"^[a-z0-9][a-z0-9_-]{0,49}:[a-z0-9][a-z0-9_-]{0,49}:[A-Za-z0-9._~-]{1,128}$",
    re.IGNORECASE,
)


def validate_internal_torrent_ref(value: str) -> str:
    if len(value) > 180 or not _INTERNAL_TORRENT_REF.fullmatch(value):
        raise ValueError("torrent_ref must be a strict internal reference")
    return value
