from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    CheckConstraint,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    event,
    inspect,
    text,
)
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Mapped, Mapper, mapped_column

from app.core.time import utc_now
from app.db.base import Base
from app.models.enums import (
    ApprovalStatus,
    DownloadExecutionStatus,
    DownloadJobStatus,
    DownloadLaunchMode,
    ExecutionIntentStatus,
    HnrStatus,
    IdentityConfidence,
    JobStatus,
    MediaType,
    MetadataStatus,
    WorkflowStatus,
)


def new_id() -> str:
    return str(uuid.uuid4())


def portable_hex_check(column: str, lengths: tuple[int, ...]) -> str:
    stripped = f"lower({column})"
    for character in "0123456789abcdef":
        stripped = f"replace({stripped}, '{character}', '')"
    allowed_lengths = ", ".join(str(length) for length in lengths)
    return (
        f"{column} IS NULL OR (length({column}) IN ({allowed_lengths}) "
        f"AND {column} = lower({column}) AND length({stripped}) = 0)"
    )


class DiscoveryRun(Base):
    __tablename__ = "discovery_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    source: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    status: Mapped[JobStatus] = mapped_column(
        Enum(JobStatus, native_enum=False, length=20), default=JobStatus.PENDING, nullable=False
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    discovered_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    updated_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(80))
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )


class MediaItem(Base):
    __tablename__ = "media_items"
    __table_args__ = (
        Index(
            "uq_media_source_type_tmdb",
            "source",
            "media_type",
            "tmdb_id",
            unique=True,
            postgresql_where=text("tmdb_id IS NOT NULL"),
            sqlite_where=text("tmdb_id IS NOT NULL"),
        ),
        Index(
            "uq_media_source_type_fallback",
            "source",
            "media_type",
            "source_item_id",
            unique=True,
            postgresql_where=text("tmdb_id IS NULL"),
            sqlite_where=text("tmdb_id IS NULL"),
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    source: Mapped[str] = mapped_column(String(50), nullable=False)
    source_item_id: Mapped[str] = mapped_column(String(180), nullable=False)
    media_type: Mapped[MediaType] = mapped_column(
        Enum(
            MediaType,
            native_enum=False,
            length=10,
            values_callable=lambda members: [member.value for member in members],
        ),
        nullable=False,
    )
    tmdb_id: Mapped[int | None] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    original_title: Mapped[str | None] = mapped_column(String(500))
    year: Mapped[int | None] = mapped_column(Integer)
    poster_path: Mapped[str | None] = mapped_column(Text)
    raw_type: Mapped[str | None] = mapped_column(String(80))
    local_episodes: Mapped[int | None] = mapped_column(Integer)
    local_episode_matrix: Mapped[dict[str, list[int]] | None] = mapped_column(JSON)
    total_episodes: Mapped[int | None] = mapped_column(Integer)
    aired_episodes: Mapped[int | None] = mapped_column(Integer)
    missing_episodes: Mapped[list[str] | None] = mapped_column(JSON)
    discovery_status: Mapped[str] = mapped_column(String(40), default="MISSING", nullable=False)
    identity_confidence: Mapped[IdentityConfidence] = mapped_column(
        Enum(IdentityConfidence, native_enum=False, length=30), nullable=False
    )
    metadata_status: Mapped[MetadataStatus] = mapped_column(
        Enum(MetadataStatus, native_enum=False, length=30), nullable=False
    )
    workflow_status: Mapped[WorkflowStatus] = mapped_column(
        Enum(WorkflowStatus, native_enum=False, length=30),
        default=WorkflowStatus.DISCOVERED,
        nullable=False,
    )
    discovered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (
        Index(
            "uq_jobs_active_type",
            "job_type",
            unique=True,
            postgresql_where=text("status IN ('PENDING', 'RUNNING', 'RETRY_WAIT')"),
            sqlite_where=text("status IN ('PENDING', 'RUNNING', 'RETRY_WAIT')"),
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    job_type: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    status: Mapped[JobStatus] = mapped_column(
        Enum(JobStatus, native_enum=False, length=20), default=JobStatus.PENDING, nullable=False
    )
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    run_id: Mapped[str | None] = mapped_column(
        ForeignKey("discovery_runs.id", ondelete="CASCADE"), unique=True
    )
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    locked_by: Mapped[str | None] = mapped_column(String(180))
    lease_token: Mapped[str | None] = mapped_column(String(36))
    error_code: Mapped[str | None] = mapped_column(String(80))
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    event_type: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    entity_type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    entity_id: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    sanitized_details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False, index=True
    )


class WorkerHeartbeat(Base):
    __tablename__ = "worker_heartbeats"

    worker_id: Mapped[str] = mapped_column(String(180), primary_key=True)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class MetadataMatch(Base):
    __tablename__ = "metadata_matches"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    media_id: Mapped[str] = mapped_column(
        ForeignKey("media_items.id", ondelete="CASCADE"), nullable=False, index=True
    )
    tmdb_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    rank: Mapped[int] = mapped_column(Integer, nullable=False)
    score: Mapped[float] = mapped_column(Float, nullable=False)
    match_reasons: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    conflicts: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    candidate_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False, index=True
    )


class IdentityReview(Base):
    __tablename__ = "identity_reviews"
    __table_args__ = (
        Index(
            "uq_identity_reviews_confirmed_media",
            "media_id",
            unique=True,
            postgresql_where=text("status = 'CONFIRMED'"),
            sqlite_where=text("status = 'CONFIRMED'"),
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    media_id: Mapped[str] = mapped_column(
        ForeignKey("media_items.id", ondelete="CASCADE"), nullable=False, index=True
    )
    metadata_match_id: Mapped[str] = mapped_column(
        ForeignKey("metadata_matches.id", ondelete="RESTRICT"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="CONFIRMED")
    confirmed_by: Mapped[str] = mapped_column(String(120), nullable=False)
    candidate_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False, index=True
    )


class TorrentSearchRun(Base):
    __tablename__ = "torrent_search_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    media_id: Mapped[str] = mapped_column(
        ForeignKey("media_items.id", ondelete="CASCADE"), nullable=False, index=True
    )
    site_id: Mapped[str] = mapped_column(String(50), nullable=False, default="avistaz")
    status: Mapped[WorkflowStatus] = mapped_column(
        Enum(WorkflowStatus, native_enum=False, length=30),
        default=WorkflowStatus.PT_SEARCH_PENDING,
        nullable=False,
        index=True,
    )
    strategy_log: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list, nullable=False)
    sanitized_request: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    candidate_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(80))
    error_message: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False, index=True
    )


class TorrentCandidateRecord(Base):
    __tablename__ = "torrent_candidates"
    __table_args__ = (
        Index(
            "uq_torrent_candidate_search_site_torrent",
            "search_run_id",
            "site_id",
            "torrent_id",
            unique=True,
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    search_run_id: Mapped[str] = mapped_column(
        ForeignKey("torrent_search_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    site_id: Mapped[str] = mapped_column(String(50), nullable=False)
    torrent_id: Mapped[str] = mapped_column(String(180), nullable=False)
    candidate_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    match_score: Mapped[float] = mapped_column(Float, nullable=False)
    match_reasons: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    warnings: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False, index=True
    )


class ApprovalRequest(Base):
    __tablename__ = "approval_requests"
    __table_args__ = (
        Index(
            "uq_approval_active_candidate",
            "torrent_candidate_id",
            unique=True,
            postgresql_where=text("status IN ('PENDING', 'APPROVED', 'EXECUTING')"),
            sqlite_where=text("status IN ('PENDING', 'APPROVED', 'EXECUTING')"),
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    media_item_id: Mapped[str] = mapped_column(
        ForeignKey("media_items.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    torrent_candidate_id: Mapped[str] = mapped_column(
        ForeignKey("torrent_candidates.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    status: Mapped[ApprovalStatus] = mapped_column(
        Enum(ApprovalStatus, native_enum=False, length=20),
        default=ApprovalStatus.PENDING,
        nullable=False,
        index=True,
    )
    candidate_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    snapshot_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    requested_by: Mapped[str] = mapped_column(String(120), nullable=False)
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    preflight_result: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    preflight_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False, index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )


class ApprovalEvent(Base):
    __tablename__ = "approval_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    approval_request_id: Mapped[str] = mapped_column(
        ForeignKey("approval_requests.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    event_type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    from_status: Mapped[str | None] = mapped_column(String(20))
    to_status: Mapped[str] = mapped_column(String(20), nullable=False)
    actor: Mapped[str] = mapped_column(String(120), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text)
    snapshot_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    sanitized_details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False, index=True
    )


class DownloadPlan(Base):
    __tablename__ = "download_plans"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    approval_id: Mapped[str] = mapped_column(
        ForeignKey("approval_requests.id", ondelete="RESTRICT"),
        nullable=False,
        unique=True,
        index=True,
    )
    approval_snapshot_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    preflight_policy_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    plan_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    site_id: Mapped[str] = mapped_column(String(50), nullable=False)
    torrent_ref: Mapped[str] = mapped_column(String(180), nullable=False)
    expected_info_hash: Mapped[str | None] = mapped_column(String(64))
    release_title: Mapped[str] = mapped_column(String(1000), nullable=False)
    save_path_ref: Mapped[str] = mapped_column(String(180), nullable=False)
    category: Mapped[str] = mapped_column(String(300), nullable=False)
    tags: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    estimated_size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    media_destination_plan: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    preflight_result: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    warnings: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False, index=True
    )


class ExecutionIntent(Base):
    __tablename__ = "execution_intents"
    __table_args__ = (
        CheckConstraint(
            "((status = 'CONSUMED' AND consumed_at IS NOT NULL AND consumed_by IS NOT NULL) "
            "OR (status <> 'CONSUMED' AND consumed_at IS NULL AND consumed_by IS NULL))",
            name="ck_execution_intents_consumed_fields",
        ),
        Index(
            "uq_execution_intent_active_approval",
            "approval_id",
            unique=True,
            postgresql_where=text("status = 'ACTIVE'"),
            sqlite_where=text("status = 'ACTIVE'"),
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    approval_id: Mapped[str] = mapped_column(
        ForeignKey("approval_requests.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    nonce_sha256: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    approval_snapshot_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    plan_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    qb_target_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    launch_mode: Mapped[DownloadLaunchMode] = mapped_column(
        Enum(DownloadLaunchMode, native_enum=False, length=30), nullable=False
    )
    status: Mapped[ExecutionIntentStatus] = mapped_column(
        Enum(ExecutionIntentStatus, native_enum=False, length=20),
        default=ExecutionIntentStatus.ACTIVE,
        nullable=False,
        index=True,
    )
    created_by: Mapped[str] = mapped_column(String(120), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    consumed_by: Mapped[str | None] = mapped_column(String(120))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False, index=True
    )


class DownloadExecution(Base):
    __tablename__ = "download_executions"
    __table_args__ = (
        CheckConstraint(
            "attempts >= 0 AND max_attempts >= 1 AND attempts <= max_attempts",
            name="ck_download_executions_attempts",
        ),
        CheckConstraint(
            portable_hex_check("actual_info_hash", (40, 64)),
            name="ck_download_executions_actual_info_hash_shape",
        ),
        CheckConstraint(
            portable_hex_check("actual_info_hash_v1", (40,)),
            name="ck_download_executions_actual_info_hash_v1_shape",
        ),
        CheckConstraint(
            portable_hex_check("actual_info_hash_v2", (64,)),
            name="ck_download_executions_actual_info_hash_v2_shape",
        ),
        CheckConstraint(
            "actual_info_hash IS NULL "
            "OR (actual_info_hash_v1 IS NOT NULL "
            "AND actual_info_hash = actual_info_hash_v1) "
            "OR (actual_info_hash_v2 IS NOT NULL "
            "AND (actual_info_hash = actual_info_hash_v2 "
            "OR actual_info_hash = substr(actual_info_hash_v2, 1, 40)))",
            name="ck_download_executions_actual_info_hash_binding",
        ),
        CheckConstraint(
            "((locked_at IS NULL AND locked_by IS NULL AND lease_token IS NULL) OR "
            "(locked_at IS NOT NULL AND locked_by IS NOT NULL AND lease_token IS NOT NULL))",
            name="ck_download_executions_lease_fields",
        ),
        CheckConstraint(
            "((status IN ('VALIDATING', 'SUBMITTING') AND locked_at IS NOT NULL) OR "
            "(status NOT IN ('VALIDATING', 'SUBMITTING') AND locked_at IS NULL))",
            name="ck_download_executions_lease_status",
        ),
        CheckConstraint(
            "((actual_info_hash IS NULL AND actual_info_hash_v1 IS NULL "
            "AND actual_info_hash_v2 IS NULL AND actual_size_bytes IS NULL "
            "AND actual_file_count IS NULL AND validated_at IS NULL "
            "AND submitted_at IS NULL) OR "
            "(actual_info_hash IS NOT NULL "
            "AND (actual_info_hash_v1 IS NOT NULL OR actual_info_hash_v2 IS NOT NULL) "
            "AND actual_size_bytes > 0 AND actual_file_count > 0 "
            "AND validated_at IS NOT NULL AND submitted_at IS NOT NULL))",
            name="ck_download_executions_validation_bundle",
        ),
        CheckConstraint(
            "((status IN ('SUBMITTING', 'SUBMITTED', 'ALREADY_PRESENT', "
            "'OUTCOME_UNKNOWN', 'RECONCILIATION_REQUIRED', 'RECONCILIATION_PENDING') "
            "AND actual_info_hash IS NOT NULL) OR "
            "(status NOT IN ('SUBMITTING', 'SUBMITTED', 'ALREADY_PRESENT', "
            "'OUTCOME_UNKNOWN', 'RECONCILIATION_REQUIRED', 'RECONCILIATION_PENDING') "
            "AND actual_info_hash IS NULL))",
            name="ck_download_executions_submission_metadata",
        ),
        CheckConstraint(
            "((status IN ('SUBMITTED', 'ALREADY_PRESENT') AND verified_at IS NOT NULL) OR "
            "(status NOT IN ('SUBMITTED', 'ALREADY_PRESENT') AND verified_at IS NULL))",
            name="ck_download_executions_verified_status",
        ),
        CheckConstraint(
            "((reconciliation_requested_by IS NULL AND reconciliation_requested_at IS NULL) "
            "OR (reconciliation_requested_by IS NOT NULL "
            "AND reconciliation_requested_at IS NOT NULL))",
            name="ck_download_executions_reconciliation_fields",
        ),
        CheckConstraint(
            "status <> 'RECONCILIATION_PENDING' OR "
            "(reconciliation_requested_by IS NOT NULL "
            "AND reconciliation_requested_at IS NOT NULL)",
            name="ck_download_executions_reconciliation_status",
        ),
        CheckConstraint(
            "reconciliation_reason IS NULL OR "
            "(reconciliation_requested_by IS NOT NULL "
            "AND reconciliation_requested_at IS NOT NULL)",
            name="ck_download_executions_reconciliation_reason",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    approval_id: Mapped[str] = mapped_column(
        ForeignKey("approval_requests.id", ondelete="RESTRICT"),
        nullable=False,
        unique=True,
        index=True,
    )
    intent_id: Mapped[str] = mapped_column(
        ForeignKey("execution_intents.id", ondelete="RESTRICT"),
        nullable=False,
        unique=True,
        index=True,
    )
    idempotency_key_sha256: Mapped[str] = mapped_column(
        String(64), nullable=False, unique=True
    )
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    approval_snapshot_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    plan_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    qb_target_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    launch_mode: Mapped[DownloadLaunchMode] = mapped_column(
        Enum(DownloadLaunchMode, native_enum=False, length=30), nullable=False
    )
    status: Mapped[DownloadExecutionStatus] = mapped_column(
        Enum(DownloadExecutionStatus, native_enum=False, length=40),
        default=DownloadExecutionStatus.PENDING,
        nullable=False,
        index=True,
    )
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
    next_retry_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), index=True
    )
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    locked_by: Mapped[str | None] = mapped_column(String(180))
    lease_token: Mapped[str | None] = mapped_column(String(36))
    actual_info_hash: Mapped[str | None] = mapped_column(String(64))
    actual_info_hash_v1: Mapped[str | None] = mapped_column(String(40))
    actual_info_hash_v2: Mapped[str | None] = mapped_column(String(64))
    actual_size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    actual_file_count: Mapped[int | None] = mapped_column(Integer)
    validated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(String(80))
    error_message: Mapped[str | None] = mapped_column(Text)
    requested_by: Mapped[str] = mapped_column(String(120), nullable=False)
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    reconciliation_requested_by: Mapped[str | None] = mapped_column(String(120))
    reconciliation_requested_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    reconciliation_reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False, index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )


class DownloadExecutionEvent(Base):
    __tablename__ = "download_execution_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    download_execution_id: Mapped[str] = mapped_column(
        ForeignKey("download_executions.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    event_type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    from_status: Mapped[str | None] = mapped_column(String(40))
    to_status: Mapped[str] = mapped_column(String(40), nullable=False)
    actor: Mapped[str] = mapped_column(String(120), nullable=False)
    sanitized_details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False, index=True
    )


class DownloadJob(Base):
    __tablename__ = "download_jobs"
    __table_args__ = (
        CheckConstraint(
            "info_hash_v1 IS NOT NULL OR info_hash_v2 IS NOT NULL",
            name="ck_download_jobs_info_hash_required",
        ),
        CheckConstraint(
            portable_hex_check("info_hash_v1", (40,)),
            name="ck_download_jobs_info_hash_v1_shape",
        ),
        CheckConstraint(
            portable_hex_check("info_hash_v2", (64,)),
            name="ck_download_jobs_info_hash_v2_shape",
        ),
        CheckConstraint(
            "size_bytes > 0 AND file_count > 0",
            name="ck_download_jobs_content_bounds",
        ),
        CheckConstraint(
            "progress >= 0 AND progress <= 1",
            name="ck_download_jobs_progress",
        ),
        CheckConstraint(
            "download_speed_bps >= 0 AND upload_speed_bps >= 0 "
            "AND downloaded_bytes >= 0 AND uploaded_bytes >= 0 AND ratio >= -1",
            name="ck_download_jobs_transfer_metrics",
        ),
        CheckConstraint(
            "status <> 'COMPLETED' OR completed_at IS NOT NULL",
            name="ck_download_jobs_completed_at",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    execution_id: Mapped[str] = mapped_column(
        ForeignKey("download_executions.id", ondelete="RESTRICT"),
        nullable=False,
        unique=True,
        index=True,
    )
    approval_id: Mapped[str] = mapped_column(
        ForeignKey("approval_requests.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    media_item_id: Mapped[str] = mapped_column(
        ForeignKey("media_items.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    status: Mapped[DownloadJobStatus] = mapped_column(
        Enum(DownloadJobStatus, native_enum=False, length=20),
        default=DownloadJobStatus.QUEUED,
        nullable=False,
        index=True,
    )
    release_title: Mapped[str] = mapped_column(String(1000), nullable=False)
    info_hash_v1: Mapped[str | None] = mapped_column(String(40))
    info_hash_v2: Mapped[str | None] = mapped_column(String(64))
    save_path_ref: Mapped[str] = mapped_column(String(180), nullable=False)
    category: Mapped[str] = mapped_column(String(300), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    file_count: Mapped[int] = mapped_column(Integer, nullable=False)
    progress: Mapped[float] = mapped_column(Float, default=0, nullable=False)
    download_speed_bps: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    upload_speed_bps: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    downloaded_bytes: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    uploaded_bytes: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    ratio: Mapped[float] = mapped_column(Float, default=0, nullable=False)
    hnr_status: Mapped[HnrStatus] = mapped_column(
        Enum(HnrStatus, native_enum=False, length=20),
        default=HnrStatus.UNKNOWN,
        nullable=False,
    )
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), index=True
    )
    error_code: Mapped[str | None] = mapped_column(String(80))
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False, index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )


class DownloadJobEvent(Base):
    __tablename__ = "download_job_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    download_job_id: Mapped[str] = mapped_column(
        ForeignKey("download_jobs.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    event_type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    from_status: Mapped[str | None] = mapped_column(String(20))
    to_status: Mapped[str] = mapped_column(String(20), nullable=False)
    actor: Mapped[str] = mapped_column(String(120), nullable=False)
    sanitized_details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False, index=True
    )


_APPROVAL_IMMUTABLE_FIELDS = (
    "media_item_id",
    "torrent_candidate_id",
    "candidate_snapshot",
    "snapshot_hash",
    "requested_by",
    "requested_at",
    "expires_at",
    "created_at",
)

_EXECUTION_INTENT_IMMUTABLE_FIELDS = (
    "approval_id",
    "nonce_sha256",
    "approval_snapshot_hash",
    "plan_hash",
    "qb_target_fingerprint",
    "launch_mode",
    "created_by",
    "expires_at",
    "created_at",
)

_DOWNLOAD_EXECUTION_IMMUTABLE_FIELDS = (
    "approval_id",
    "intent_id",
    "idempotency_key_sha256",
    "request_hash",
    "approval_snapshot_hash",
    "plan_hash",
    "qb_target_fingerprint",
    "launch_mode",
    "requested_by",
    "requested_at",
    "created_at",
)

_DOWNLOAD_JOB_IMMUTABLE_FIELDS = (
    "execution_id",
    "approval_id",
    "media_item_id",
    "release_title",
    "info_hash_v1",
    "info_hash_v2",
    "save_path_ref",
    "category",
    "size_bytes",
    "file_count",
    "started_at",
    "created_at",
)


@event.listens_for(ApprovalRequest, "before_update")
def reject_approval_identity_update(
    _mapper: Mapper[ApprovalRequest],
    _connection: Connection,
    target: ApprovalRequest,
) -> None:
    state = inspect(target)
    changed = [
        field for field in _APPROVAL_IMMUTABLE_FIELDS if state.attrs[field].history.has_changes()
    ]
    if changed:
        raise ValueError(f"approval immutable fields cannot change: {', '.join(changed)}")


@event.listens_for(ExecutionIntent, "before_update")
def reject_execution_intent_binding_update(
    _mapper: Mapper[ExecutionIntent],
    _connection: Connection,
    target: ExecutionIntent,
) -> None:
    state = inspect(target)
    changed = [
        field
        for field in _EXECUTION_INTENT_IMMUTABLE_FIELDS
        if state.attrs[field].history.has_changes()
    ]
    if changed:
        raise ValueError(f"execution intent immutable fields cannot change: {', '.join(changed)}")


@event.listens_for(DownloadExecution, "before_update")
def reject_download_execution_binding_update(
    _mapper: Mapper[DownloadExecution],
    _connection: Connection,
    target: DownloadExecution,
) -> None:
    state = inspect(target)
    changed = [
        field
        for field in _DOWNLOAD_EXECUTION_IMMUTABLE_FIELDS
        if state.attrs[field].history.has_changes()
    ]
    if changed:
        raise ValueError(
            f"download execution immutable fields cannot change: {', '.join(changed)}"
        )
    info_hash_history = state.attrs.actual_info_hash.history
    if (
        info_hash_history.has_changes()
        and info_hash_history.deleted
        and info_hash_history.deleted[0] is not None
    ):
        raise ValueError("download execution actual_info_hash cannot change once persisted")
    for field in (
        "actual_info_hash_v1",
        "actual_info_hash_v2",
        "actual_size_bytes",
        "actual_file_count",
        "validated_at",
        "submitted_at",
        "verified_at",
    ):
        history = state.attrs[field].history
        if history.has_changes() and history.deleted and history.deleted[0] is not None:
            raise ValueError(f"download execution {field} cannot change once persisted")


@event.listens_for(DownloadJob, "before_update")
def reject_download_job_identity_update(
    _mapper: Mapper[DownloadJob],
    _connection: Connection,
    target: DownloadJob,
) -> None:
    state = inspect(target)
    changed = [
        field
        for field in _DOWNLOAD_JOB_IMMUTABLE_FIELDS
        if state.attrs[field].history.has_changes()
    ]
    if changed:
        raise ValueError(f"download job immutable fields cannot change: {', '.join(changed)}")


@event.listens_for(ApprovalRequest, "before_delete")
@event.listens_for(ApprovalEvent, "before_update")
@event.listens_for(ApprovalEvent, "before_delete")
@event.listens_for(DownloadPlan, "before_update")
@event.listens_for(DownloadPlan, "before_delete")
@event.listens_for(ExecutionIntent, "before_delete")
@event.listens_for(DownloadExecution, "before_delete")
@event.listens_for(DownloadExecutionEvent, "before_update")
@event.listens_for(DownloadExecutionEvent, "before_delete")
@event.listens_for(DownloadJob, "before_delete")
@event.listens_for(DownloadJobEvent, "before_update")
@event.listens_for(DownloadJobEvent, "before_delete")
def reject_immutable_audit_mutation(
    _mapper: Mapper[object],
    _connection: Connection,
    _target: object,
) -> None:
    raise ValueError("immutable approval audit records cannot be updated or deleted")
