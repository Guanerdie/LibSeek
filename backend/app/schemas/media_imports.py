from __future__ import annotations

import re
import unicodedata
from datetime import datetime
from itertools import pairwise
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.core.security import sanitize_public_text
from app.models.enums import (
    DownloadExecutionStatus,
    DownloadJobStatus,
    HnrStatus,
    MediaImportOperation,
    MediaImportStatus,
    MediaType,
    PreflightStatus,
)

MAX_MEDIA_IMPORT_FILES = 20_000
MAX_MEDIA_IMPORT_PATH_LENGTH = 4096
MAX_MEDIA_IMPORT_AGGREGATE_PATH_BYTES = 4 * 1024 * 1024
MAX_MEDIA_IMPORT_REASON_LENGTH = 500
MEDIA_IMPORT_PLAN_MODE = "PLAN_ONLY_NO_FILE_OPERATION"
_ROOT_REF = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,179}$")
_DRIVE_PATH = re.compile(r"^[A-Za-z]:")
_WINDOWS_RESERVED = {
    "con",
    "prn",
    "aux",
    "nul",
    *(f"com{number}" for number in range(1, 10)),
    *(f"lpt{number}" for number in range(1, 10)),
}
_FORBIDDEN_UNICODE_CATEGORIES = {"Cc", "Cf"}


def validate_internal_root_ref(value: str) -> str:
    if not _ROOT_REF.fullmatch(value):
        raise ValueError("root ref must be an opaque internal reference")
    return value


def validate_normalized_relative_path(value: str) -> str:
    if not value or len(value) > MAX_MEDIA_IMPORT_PATH_LENGTH:
        raise ValueError("relative path length is invalid")
    if value != value.strip() or unicodedata.normalize("NFC", value) != value:
        raise ValueError("relative path must use canonical text")
    if (
        value.startswith(("/", "\\"))
        or _DRIVE_PATH.match(value)
        or "\\" in value
        or any(
            unicodedata.category(character) in _FORBIDDEN_UNICODE_CATEGORIES
            for character in value
        )
    ):
        raise ValueError("absolute, UNC, drive, backslash, or control paths are forbidden")
    parts = value.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise ValueError("relative path must not contain empty, dot, or parent segments")
    for part in parts:
        if part.endswith((" ", ".")) or ":" in part:
            raise ValueError("relative path contains a non-portable segment")
        if part.split(".", 1)[0].casefold() in _WINDOWS_RESERVED:
            raise ValueError("relative path contains a reserved device segment")
    return value


def path_collision_key(value: str) -> str:
    return unicodedata.normalize("NFKC", value).casefold()


def _validate_unique_paths(
    values: list[object],
    *,
    field: str,
    label: str,
    reject_prefix_collisions: bool,
) -> None:
    paths = [str(getattr(value, field)) for value in values]
    keys = [path_collision_key(path) for path in paths]
    if len(set(paths)) != len(paths) or len(set(keys)) != len(keys):
        raise ValueError(f"{label} contains duplicate or case-colliding paths")
    if reject_prefix_collisions:
        segmented_keys = sorted(tuple(key.split("/")) for key in keys)
        for parent, child in pairwise(segmented_keys):
            if len(parent) < len(child) and child[: len(parent)] == parent:
                raise ValueError(f"{label} contains file/directory prefix collisions")


def _validate_aggregate_path_bytes(paths: list[str], *, label: str) -> None:
    if sum(len(path.encode("utf-8")) for path in paths) > (
        MAX_MEDIA_IMPORT_AGGREGATE_PATH_BYTES
    ):
        raise ValueError(f"{label} aggregate path data exceeds the safety limit")


def _require_timezone(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("timestamp must include a timezone")
    return value


def normalize_media_import_reason(value: str | None) -> str | None:
    if value is None:
        return None
    if any(
        unicodedata.category(character) in _FORBIDDEN_UNICODE_CATEGORIES
        for character in value
    ):
        raise ValueError("decision reason must not contain control or format characters")
    normalized = value.strip()
    if not normalized:
        return None
    sanitized = sanitize_public_text(normalized)
    if len(sanitized) > MAX_MEDIA_IMPORT_REASON_LENGTH:
        raise ValueError("sanitized decision reason exceeds the storage limit")
    return sanitized


class MediaImportManifestFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    relative_path: str = Field(min_length=1, max_length=MAX_MEDIA_IMPORT_PATH_LENGTH)
    size_bytes: int = Field(ge=0)

    @field_validator("relative_path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        return validate_normalized_relative_path(value)


class MediaImportSourceManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_root_ref: str = Field(min_length=1, max_length=180)
    files: list[MediaImportManifestFile] = Field(
        min_length=1, max_length=MAX_MEDIA_IMPORT_FILES
    )

    @field_validator("source_root_ref")
    @classmethod
    def validate_ref(cls, value: str) -> str:
        return validate_internal_root_ref(value)

    @model_validator(mode="after")
    def validate_files(self) -> MediaImportSourceManifest:
        _validate_aggregate_path_bytes(
            [item.relative_path for item in self.files], label="source manifest"
        )
        _validate_unique_paths(
            list(self.files),
            field="relative_path",
            label="source manifest",
            reject_prefix_collisions=True,
        )
        return self


class MediaImportTargetEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_relative_path: str = Field(
        min_length=1, max_length=MAX_MEDIA_IMPORT_PATH_LENGTH
    )
    target_relative_path: str = Field(
        min_length=1, max_length=MAX_MEDIA_IMPORT_PATH_LENGTH
    )

    @field_validator("source_relative_path", "target_relative_path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        return validate_normalized_relative_path(value)


class MediaImportTargetMapping(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_root_ref: str = Field(min_length=1, max_length=180)
    files: list[MediaImportTargetEntry] = Field(
        min_length=1, max_length=MAX_MEDIA_IMPORT_FILES
    )
    source_retention: Literal[True] = True
    overwrite: Literal[False] = False

    @field_validator("target_root_ref")
    @classmethod
    def validate_ref(cls, value: str) -> str:
        return validate_internal_root_ref(value)

    @model_validator(mode="after")
    def validate_files(self) -> MediaImportTargetMapping:
        _validate_aggregate_path_bytes(
            [
                path
                for item in self.files
                for path in (item.source_relative_path, item.target_relative_path)
            ],
            label="target mapping",
        )
        _validate_unique_paths(
            list(self.files),
            field="source_relative_path",
            label="target mapping sources",
            reject_prefix_collisions=False,
        )
        _validate_unique_paths(
            list(self.files),
            field="target_relative_path",
            label="target mapping destinations",
            reject_prefix_collisions=True,
        )
        return self


class MediaImportCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    download_job_id: str = Field(min_length=1, max_length=36)
    proposed_operation: MediaImportOperation
    source_manifest: MediaImportSourceManifest
    target_mapping: MediaImportTargetMapping


class MediaImportJobSummarySnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    download_job_id: str
    media_item_id: str
    execution_id: str
    approval_id: str
    job_status: DownloadJobStatus
    progress: float = Field(ge=0, le=1, allow_inf_nan=False)
    info_hash_v1: str | None = Field(default=None, pattern=r"^[0-9a-f]{40}$")
    info_hash_v2: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    size_bytes: int = Field(ge=1)
    file_count: int = Field(ge=1)
    completed_at: datetime | None
    hnr_status: HnrStatus
    media_type: MediaType
    tmdb_id: int | None = Field(default=None, ge=1)
    media_title: str = Field(min_length=1, max_length=500)
    media_year: int | None = Field(default=None, ge=1870, le=2200)
    execution_status: DownloadExecutionStatus
    actual_info_hash_v1: str | None = Field(default=None, pattern=r"^[0-9a-f]{40}$")
    actual_info_hash_v2: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    actual_size_bytes: int = Field(ge=1)
    actual_file_count: int = Field(ge=1)
    verified_at: datetime

    @field_validator("completed_at", "verified_at")
    @classmethod
    def validate_timestamp(cls, value: datetime | None) -> datetime | None:
        return _require_timezone(value) if value is not None else None

    @model_validator(mode="after")
    def require_hashes(self) -> MediaImportJobSummarySnapshot:
        if self.info_hash_v1 is None and self.info_hash_v2 is None:
            raise ValueError("download job summary requires an info hash")
        if self.actual_info_hash_v1 is None and self.actual_info_hash_v2 is None:
            raise ValueError("execution summary requires an actual info hash")
        return self


class MediaImportInspectedSourceFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    relative_path: str = Field(min_length=1, max_length=MAX_MEDIA_IMPORT_PATH_LENGTH)
    size_bytes: int = Field(ge=0)
    exists: bool
    is_regular_file: bool
    is_symlink: bool
    complete: bool

    @field_validator("relative_path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        return validate_normalized_relative_path(value)


class MediaImportInspectedTargetFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    relative_path: str = Field(min_length=1, max_length=MAX_MEDIA_IMPORT_PATH_LENGTH)
    exists: bool

    @field_validator("relative_path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        return validate_normalized_relative_path(value)


class MediaImportInspectionSnapshot(BaseModel):
    """Trusted, read-only observation accepted only by the internal service boundary."""

    model_config = ConfigDict(extra="forbid")

    download_job_id: str = Field(min_length=1, max_length=36)
    source_root_ref: str = Field(min_length=1, max_length=180)
    target_root_ref: str = Field(min_length=1, max_length=180)
    info_hash_v1: str | None = Field(default=None, pattern=r"^[0-9a-f]{40}$")
    info_hash_v2: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    source_files: list[MediaImportInspectedSourceFile] = Field(
        min_length=1, max_length=MAX_MEDIA_IMPORT_FILES
    )
    target_files: list[MediaImportInspectedTargetFile] = Field(
        min_length=1, max_length=MAX_MEDIA_IMPORT_FILES
    )
    same_filesystem: bool | None = None
    available_bytes: int | None = Field(default=None, ge=0)
    inspected_at: datetime

    @field_validator("source_root_ref", "target_root_ref")
    @classmethod
    def validate_ref(cls, value: str) -> str:
        return validate_internal_root_ref(value)

    @field_validator("inspected_at")
    @classmethod
    def validate_timestamp(cls, value: datetime) -> datetime:
        return _require_timezone(value)

    @model_validator(mode="after")
    def validate_file_sets(self) -> MediaImportInspectionSnapshot:
        _validate_aggregate_path_bytes(
            [item.relative_path for item in self.source_files],
            label="inspected source files",
        )
        _validate_aggregate_path_bytes(
            [item.relative_path for item in self.target_files],
            label="inspected target files",
        )
        _validate_unique_paths(
            list(self.source_files),
            field="relative_path",
            label="inspected source files",
            reject_prefix_collisions=True,
        )
        _validate_unique_paths(
            list(self.target_files),
            field="relative_path",
            label="inspected target files",
            reject_prefix_collisions=True,
        )
        if self.info_hash_v1 is None and self.info_hash_v2 is None:
            raise ValueError("inspection requires an info hash")
        return self


class MediaImportPreflightCheck(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(pattern=r"^[A-Z0-9_]{3,80}$")
    status: PreflightStatus
    message: str = Field(min_length=1, max_length=300)


class MediaImportPreflightResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    overall_status: PreflightStatus
    checked_at: datetime
    config_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    checks: list[MediaImportPreflightCheck] = Field(min_length=1, max_length=50)

    @field_validator("checked_at")
    @classmethod
    def validate_timestamp(cls, value: datetime) -> datetime:
        return _require_timezone(value)


class MediaImportApproveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    acknowledges_plan_only: bool = False
    acknowledges_source_retention: bool = False
    acknowledges_no_overwrite: bool = False
    acknowledges_hnr: bool = False


class MediaImportDecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str | None = Field(
        default=None,
        max_length=MAX_MEDIA_IMPORT_REASON_LENGTH,
    )

    @field_validator("reason")
    @classmethod
    def normalize_reason(cls, value: str | None) -> str | None:
        return normalize_media_import_reason(value)


class MediaImportDecisionAcknowledgements(BaseModel):
    model_config = ConfigDict(extra="forbid")

    acknowledges_plan_only: Literal[True]
    acknowledges_source_retention: Literal[True]
    acknowledges_no_overwrite: Literal[True]
    acknowledges_hnr: bool


class MediaImportPlanResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")

    id: str
    request_id: str
    download_job_id: str
    media_item_id: str
    execution_id: str
    mode: Literal["PLAN_ONLY_NO_FILE_OPERATION"]
    proposed_operation: MediaImportOperation
    source_manifest: MediaImportSourceManifest
    source_manifest_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    target_mapping: MediaImportTargetMapping
    target_mapping_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    job_summary_snapshot: MediaImportJobSummarySnapshot
    summary_snapshot_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    config_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    plan_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    created_by: str
    created_at: datetime


class MediaImportPreflightResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    request_id: str
    plan_id: str
    overall_status: PreflightStatus
    inspection_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    result_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    preflight_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    config_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    result: MediaImportPreflightResult
    checked_by: str
    checked_at: datetime
    created_at: datetime


class MediaImportEventResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")

    id: str
    request_id: str
    event_type: str
    from_status: str | None
    to_status: str
    actor: str
    sanitized_details: dict[str, object]
    created_at: datetime


class MediaImportRequestResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    download_job_id: str
    media_item_id: str
    execution_id: str
    status: MediaImportStatus
    requested_by: str
    requested_at: datetime
    approved_by: str | None
    approved_at: datetime | None
    rejected_by: str | None
    rejected_at: datetime | None
    rejection_reason: str | None
    revoked_by: str | None
    revoked_at: datetime | None
    revocation_reason: str | None
    decision_acknowledgements: MediaImportDecisionAcknowledgements | None
    created_at: datetime
    updated_at: datetime
    plan: MediaImportPlanResponse
    preflight: MediaImportPreflightResponse | None
    events: list[MediaImportEventResponse]


class MediaImportRequestSummaryResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    download_job_id: str
    media_item_id: str
    execution_id: str
    status: MediaImportStatus
    requested_by: str
    requested_at: datetime
    updated_at: datetime
    plan_id: str
    mode: Literal["PLAN_ONLY_NO_FILE_OPERATION"]
    proposed_operation: MediaImportOperation
    plan_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    media_type: MediaType
    tmdb_id: int | None = Field(default=None, ge=1)
    media_title: str = Field(min_length=1, max_length=500)
    media_year: int | None = Field(default=None, ge=1870, le=2200)
    source_root_ref: str = Field(min_length=1, max_length=180)
    target_root_ref: str = Field(min_length=1, max_length=180)
    file_count: int = Field(ge=1)
    size_bytes: int = Field(ge=1)
    preflight_status: PreflightStatus | None
    preflight_checked_at: datetime | None
    preflight_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_preflight_summary(self) -> MediaImportRequestSummaryResponse:
        values = (
            self.preflight_status,
            self.preflight_checked_at,
            self.preflight_hash,
        )
        if any(value is None for value in values) and not all(
            value is None for value in values
        ):
            raise ValueError("preflight summary fields must be present together")
        return self
