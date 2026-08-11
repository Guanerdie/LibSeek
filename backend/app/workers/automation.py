from __future__ import annotations

import asyncio
import math
import uuid
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.adapters.base import ReadOnlyDownloaderAdapter
from app.core.config import Settings
from app.core.time import utc_now
from app.errors import AppError
from app.models.entities import (
    ApprovalRequest,
    AutomationDecision,
    AutomationPolicyRevision,
    Job,
    WorkerHeartbeat,
)
from app.models.enums import (
    AutomationMode,
    AutomationStage,
    DecisionOutcome,
    JobStatus,
)
from app.schemas.adapters import AdapterManifest
from app.schemas.approvals import ApprovalCandidateSnapshot, PreflightResult
from app.schemas.qbittorrent import QbCategory, QbTorrent, QbTorrentFile
from app.services.approvals import (
    require_pending_approval,
    save_preflight_result,
)
from app.services.automation import (
    add_decision_once,
    build_automation_decision,
    maybe_finalize_automatic_approval,
    stage_mode,
    subject_is_new_for_revision,
)
from app.services.automation_policy import (
    canonical_hash,
    get_current_policy,
    verify_automation_decision,
)
from app.services.preflight import evaluate_preflight

_JOB_PREFIX = "AUTOMATION_PREFLIGHT:"


@dataclass(frozen=True)
class AutomationPreflightBinding:
    job_id: str
    lease_token: str
    approval_id: str
    media_item_id: str
    snapshot_hash: str
    policy_revision_id: str
    queue_decision_id: str
    preflight_state_hash: str
    snapshot: ApprovalCandidateSnapshot


class GuardedReadOnlyDownloader(ReadOnlyDownloaderAdapter):
    def __init__(
        self,
        delegate: ReadOnlyDownloaderAdapter,
        guard: Callable[[], Awaitable[None]],
    ) -> None:
        self.delegate = delegate
        self.guard = guard
        self.retryable_error: AppError | None = None

    def manifest(self) -> AdapterManifest:
        return self.delegate.manifest()

    async def authenticate(self) -> None:
        await self.guard()
        try:
            await self.delegate.authenticate()
        except AppError as exc:
            self._remember(exc)
            raise

    async def get_version(self) -> str:
        await self.guard()
        try:
            return await self.delegate.get_version()
        except AppError as exc:
            self._remember(exc)
            raise

    async def get_web_api_version(self) -> str:
        await self.guard()
        try:
            return await self.delegate.get_web_api_version()
        except AppError as exc:
            self._remember(exc)
            raise

    async def list_torrents(self) -> list[QbTorrent]:
        await self.guard()
        try:
            return await self.delegate.list_torrents()
        except AppError as exc:
            self._remember(exc)
            raise

    async def get_torrent_files(self, info_hash: str) -> list[QbTorrentFile]:
        await self.guard()
        try:
            return await self.delegate.get_torrent_files(info_hash)
        except AppError as exc:
            self._remember(exc)
            raise

    async def get_categories(self) -> dict[str, QbCategory]:
        await self.guard()
        try:
            return await self.delegate.get_categories()
        except AppError as exc:
            self._remember(exc)
            raise

    def _remember(self, error: AppError) -> None:
        if error.retryable and self.retryable_error is None:
            self.retryable_error = error


class AutomationPreflightWorker:
    """Dedicated qB read-only worker for automatic approval preflight jobs."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        worker_id: str,
        qb_factory: Callable[[], ReadOnlyDownloaderAdapter],
        settings: Settings,
    ) -> None:
        self.session_factory = session_factory
        self.worker_id = worker_id.strip()
        self.qb_factory = qb_factory
        self.settings = settings
        if not self.worker_id or len(self.worker_id) > 180:
            raise ValueError("worker_id must be between 1 and 180 characters")

    async def run_once(self) -> bool:
        require_automation_preflight_enabled(self.settings)
        await self._record_worker_heartbeat()
        job = await self.claim_job()
        if job is None:
            return False
        if job.lease_token is None:
            raise RuntimeError("claimed automation job is missing a lease token")

        stop_renewal = asyncio.Event()
        renewal = asyncio.create_task(
            self._renew_lease_loop(job.id, job.lease_token, stop_renewal)
        )
        adapter: ReadOnlyDownloaderAdapter | None = None
        try:
            binding = await self._prepare(job.id, job.lease_token)
            adapter = self.qb_factory()
            async def request_guard() -> None:
                await self._guard_external_read(binding)

            set_request_guard = getattr(adapter, "set_before_request_guard", None)
            if not callable(set_request_guard):
                raise AppError(
                    "AUTOMATION_REQUEST_GUARD_UNAVAILABLE",
                    "qBittorrent 适配器无法安装逐请求自动化围栏",
                    status_code=409,
                )
            set_request_guard(request_guard)
            guarded = GuardedReadOnlyDownloader(
                adapter,
                request_guard,
            )
            result = await evaluate_preflight(guarded, binding.snapshot, self.settings)
            if guarded.retryable_error is not None:
                raise guarded.retryable_error
            await self._finalize(binding, result)
        except AppError as exc:
            await self._fail(job.id, job.lease_token, exc)
        except Exception:
            await self._fail(
                job.id,
                job.lease_token,
                AppError(
                    "AUTOMATION_PREFLIGHT_INTERNAL_ERROR",
                    "自动预检 Worker 发生内部错误",
                    status_code=500,
                ),
            )
        finally:
            stop_renewal.set()
            renewal.cancel()
            with suppress(asyncio.CancelledError):
                await renewal
            if adapter is not None:
                close = getattr(adapter, "aclose", None)
                if callable(close):
                    with suppress(Exception):
                        await close()
        return True

    async def claim_job(self) -> Job | None:
        now = utc_now()
        stale_before = now - timedelta(seconds=self.settings.job_lease_seconds)
        claimable = or_(
            Job.status == JobStatus.PENDING,
            and_(
                Job.status == JobStatus.RETRY_WAIT,
                or_(Job.next_retry_at.is_(None), Job.next_retry_at <= now),
            ),
            and_(
                Job.status == JobStatus.RUNNING,
                Job.locked_at.is_not(None),
                Job.locked_at < stale_before,
            ),
        )
        async with self.session_factory() as session:
            job = await session.scalar(
                select(Job)
                .where(
                    claimable,
                    Job.attempts < Job.max_attempts,
                    Job.job_type.like(f"{_JOB_PREFIX}%"),
                )
                .order_by(Job.created_at, Job.id)
                .with_for_update(skip_locked=True)
                .limit(1)
            )
            if job is None:
                return None
            job.status = JobStatus.RUNNING
            job.attempts += 1
            job.next_retry_at = None
            job.locked_at = now
            job.locked_by = self.worker_id
            job.lease_token = str(uuid.uuid4())
            job.error_code = None
            job.error_message = None
            await session.commit()
            return job

    async def _prepare(
        self, job_id: str, lease_token: str
    ) -> AutomationPreflightBinding:
        async with self.session_factory() as session:
            job = self._require_job_lease(await session.get(Job, job_id), lease_token)
            approval_id, media_id, snapshot_hash, policy_revision_id, decision_id = (
                self._validated_payload(job)
            )
            approval = await session.get(ApprovalRequest, approval_id)
            if approval is None:
                raise AppError(
                    "APPROVAL_REQUEST_NOT_FOUND",
                    "自动预检绑定的审批不存在",
                    status_code=404,
                )
            _, current = await get_current_policy(session)
            snapshot = await self._validate_binding(
                session,
                job,
                approval,
                current,
                snapshot_hash=snapshot_hash,
                policy_revision_id=policy_revision_id,
                decision_id=decision_id,
                preflight_state_hash=_preflight_state_hash(approval),
            )
            return AutomationPreflightBinding(
                job_id=job.id,
                lease_token=lease_token,
                approval_id=approval.id,
                media_item_id=media_id,
                snapshot_hash=snapshot_hash,
                policy_revision_id=policy_revision_id,
                queue_decision_id=decision_id,
                preflight_state_hash=_preflight_state_hash(approval),
                snapshot=snapshot,
            )

    async def _guard_external_read(self, binding: AutomationPreflightBinding) -> None:
        async with self.session_factory() as session:
            job = self._require_job_lease(
                await session.get(Job, binding.job_id), binding.lease_token
            )
            approval = await session.get(ApprovalRequest, binding.approval_id)
            if approval is None:
                raise AppError(
                    "APPROVAL_REQUEST_NOT_FOUND",
                    "自动预检绑定的审批不存在",
                    status_code=404,
                )
            _, current = await get_current_policy(session)
            await self._validate_binding(
                session,
                job,
                approval,
                current,
                snapshot_hash=binding.snapshot_hash,
                policy_revision_id=binding.policy_revision_id,
                decision_id=binding.queue_decision_id,
                preflight_state_hash=binding.preflight_state_hash,
            )

    async def _finalize(
        self, binding: AutomationPreflightBinding, result: PreflightResult
    ) -> None:
        async with self.session_factory() as session:
            job = self._require_job_lease(
                await session.get(Job, binding.job_id, with_for_update=True),
                binding.lease_token,
            )
            approval = await session.get(
                ApprovalRequest, binding.approval_id, with_for_update=True
            )
            if approval is None:
                raise AppError(
                    "APPROVAL_REQUEST_NOT_FOUND",
                    "自动预检绑定的审批不存在",
                    status_code=404,
                )
            _, current = await get_current_policy(session, for_update=True)
            await self._validate_binding(
                session,
                job,
                approval,
                current,
                snapshot_hash=binding.snapshot_hash,
                policy_revision_id=binding.policy_revision_id,
                decision_id=binding.queue_decision_id,
                preflight_state_hash=binding.preflight_state_hash,
            )
            await save_preflight_result(
                session,
                approval,
                result,
                actor=self.worker_id,
            )
            await maybe_finalize_automatic_approval(
                session,
                approval=approval,
                settings=self.settings,
                expected_policy_revision_id=binding.policy_revision_id,
                expected_snapshot_hash=binding.snapshot_hash,
                trigger_job_id=binding.job_id,
            )
            job.status = JobStatus.SUCCEEDED
            job.error_code = None
            job.error_message = None
            job.next_retry_at = None
            job.locked_at = None
            job.locked_by = None
            job.lease_token = None
            await session.commit()

    async def _validate_binding(
        self,
        session: AsyncSession,
        job: Job,
        approval: ApprovalRequest,
        current: AutomationPolicyRevision,
        *,
        snapshot_hash: str,
        policy_revision_id: str,
        decision_id: str,
        preflight_state_hash: str,
    ) -> ApprovalCandidateSnapshot:
        if not self.settings.enable_automation_engine:
            raise AppError(
                "AUTOMATION_ENGINE_DISABLED",
                "自动化引擎已关闭，禁止 qBittorrent 预检请求",
                status_code=409,
            )
        if current.id != policy_revision_id:
            raise AppError(
                "AUTOMATION_POLICY_REVISION_CHANGED",
                "自动预检绑定的策略已不再是当前版本",
                status_code=409,
            )
        if stage_mode(current, AutomationStage.APPROVAL) != AutomationMode.AUTO_IF_ELIGIBLE:
            raise AppError(
                "AUTOMATION_APPROVAL_MODE_CHANGED",
                "当前策略不再允许自动审批预检",
                status_code=409,
            )
        if not subject_is_new_for_revision(approval.requested_at, current):
            raise AppError(
                "AUTOMATION_SUBJECT_STALE",
                "审批早于当前自动化策略，禁止自动处理",
                status_code=409,
            )
        snapshot = require_pending_approval(session, approval)
        if approval.snapshot_hash != snapshot_hash:
            raise AppError(
                "APPROVAL_SNAPSHOT_CHANGED",
                "自动预检审批快照绑定已变化",
                status_code=409,
            )
        if _preflight_state_hash(approval) != preflight_state_hash:
            raise AppError(
                "AUTOMATION_PREFLIGHT_CONCURRENT_UPDATE",
                "审批预检结果已被其他请求更新，当前结果已丢弃",
                status_code=409,
            )
        if snapshot.hit_and_run is None:
            raise AppError("HNR_UNKNOWN", "H&R 信息未知，禁止自动预检", status_code=409)
        decision = await session.get(AutomationDecision, decision_id)
        if decision is None:
            raise AppError(
                "AUTOMATION_DECISION_NOT_FOUND",
                "自动预检任务缺少队列决策绑定",
                status_code=409,
            )
        verify_automation_decision(decision)
        if (
            decision.policy_revision_id != policy_revision_id
            or decision.stage != AutomationStage.APPROVAL
            or decision.action != "QUEUE_PREFLIGHT"
            or decision.outcome != DecisionOutcome.ACTION_CREATED
            or decision.media_item_id != approval.media_item_id
            or decision.approval_request_id != approval.id
            or job.payload.get("media_id") != approval.media_item_id
            or job.payload.get("read_only") is not True
            or decision.evidence_snapshot.get("job_id") != job.id
            or decision.evidence_snapshot.get("approval_snapshot_hash")
            != approval.snapshot_hash
        ):
            raise AppError(
                "AUTOMATION_PREFLIGHT_BINDING_INVALID",
                "自动预检任务与策略决策绑定不一致",
                status_code=409,
            )
        return snapshot

    def _validated_payload(self, job: Job) -> tuple[str, str, str, str, str]:
        approval_id = job.payload.get("approval_id")
        media_id = job.payload.get("media_id")
        snapshot_hash = job.payload.get("approval_snapshot_hash")
        policy_revision_id = job.payload.get("automation_policy_revision_id")
        decision_id = job.payload.get("automation_decision_id")
        if (
            not isinstance(approval_id, str)
            or not isinstance(media_id, str)
            or job.job_type != f"{_JOB_PREFIX}{approval_id}"
            or not isinstance(snapshot_hash, str)
            or len(snapshot_hash) != 64
            or not isinstance(policy_revision_id, str)
            or not isinstance(decision_id, str)
        ):
            raise AppError(
                "AUTOMATION_PREFLIGHT_PAYLOAD_INVALID",
                "自动预检任务载荷无效",
                status_code=409,
            )
        return approval_id, media_id, snapshot_hash, policy_revision_id, decision_id

    async def _renew_lease_loop(
        self, job_id: str, lease_token: str, stop: asyncio.Event
    ) -> None:
        while True:
            try:
                await asyncio.wait_for(
                    stop.wait(),
                    timeout=self.settings.job_lease_renew_interval_seconds,
                )
                return
            except TimeoutError:
                if not await self._renew_lease(job_id, lease_token):
                    return

    async def _renew_lease(self, job_id: str, lease_token: str) -> bool:
        async with self.session_factory() as session:
            job = await session.get(Job, job_id, with_for_update=True)
            if not self._owns_job(job, lease_token):
                return False
            if job is None:
                return False
            job.locked_at = utc_now()
            await session.commit()
            return True

    async def _fail(self, job_id: str, lease_token: str, error: AppError) -> None:
        async with self.session_factory() as session:
            job = await session.get(Job, job_id, with_for_update=True)
            if not self._owns_job(job, lease_token):
                return
            if job is None:
                return
            retry = error.retryable and job.attempts < job.max_attempts
            job.status = JobStatus.RETRY_WAIT if retry else JobStatus.FAILED
            job.error_code = error.error_code
            job.error_message = error.message
            job.next_retry_at = (
                utc_now() + timedelta(seconds=self._retry_delay(error, job.attempts))
                if retry
                else None
            )
            job.locked_at = None
            job.locked_by = None
            job.lease_token = None
            if not retry:
                await self._record_failure_decision(session, job, error)
            await session.commit()

    async def _record_failure_decision(
        self, session: AsyncSession, job: Job, error: AppError
    ) -> None:
        approval_id = job.payload.get("approval_id")
        media_id = job.payload.get("media_id")
        if not isinstance(approval_id, str) or not isinstance(media_id, str):
            return
        approval = await session.get(ApprovalRequest, approval_id)
        if approval is None or approval.media_item_id != media_id:
            return
        _, revision = await get_current_policy(session)
        if error.error_code in {
            "AUTOMATION_POLICY_REVISION_CHANGED",
            "AUTOMATION_SUBJECT_STALE",
            "APPROVAL_SNAPSHOT_CHANGED",
            "AUTOMATION_PREFLIGHT_CONCURRENT_UPDATE",
        }:
            outcome = DecisionOutcome.STALE
        elif error.error_code in {
            "AUTOMATION_APPROVAL_MODE_CHANGED",
            "APPROVAL_NOT_PENDING",
        }:
            outcome = DecisionOutcome.MANUAL_REQUIRED
        else:
            outcome = DecisionOutcome.BLOCKED
        decision = build_automation_decision(
            revision,
            stage=AutomationStage.APPROVAL,
            action="RUN_PREFLIGHT",
            outcome=outcome,
            media_item_id=media_id,
            approval_request_id=approval_id,
            reason_codes=(error.error_code,),
            evidence={
                "job_id": job.id,
                "approval_snapshot_hash": approval.snapshot_hash,
                "job_policy_revision_id": job.payload.get(
                    "automation_policy_revision_id"
                ),
            },
        )
        await add_decision_once(session, decision)

    async def _record_worker_heartbeat(self) -> None:
        async with self.session_factory() as session:
            heartbeat = await session.get(WorkerHeartbeat, self.worker_id)
            if heartbeat is None:
                session.add(
                    WorkerHeartbeat(worker_id=self.worker_id, last_seen_at=utc_now())
                )
            else:
                heartbeat.last_seen_at = utc_now()
            await session.commit()

    def _require_job_lease(self, job: Job | None, lease_token: str) -> Job:
        if not self._owns_job(job, lease_token):
            raise AppError(
                "AUTOMATION_PREFLIGHT_LEASE_LOST",
                "自动预检任务租约已失效，禁止外部请求",
                status_code=409,
            )
        if job is None:
            raise AssertionError("validated automation job unexpectedly missing")
        return job

    def _owns_job(self, job: Job | None, lease_token: str) -> bool:
        return bool(
            job is not None
            and job.status == JobStatus.RUNNING
            and job.locked_by == self.worker_id
            and job.lease_token == lease_token
            and job.locked_at is not None
            and _as_utc(job.locked_at) + timedelta(seconds=self.settings.job_lease_seconds)
            > _as_utc(utc_now())
        )

    @staticmethod
    def _retry_delay(error: AppError, attempts: int) -> float:
        fallback = float(min(3600, 2**attempts))
        value = error.details.get("retry_after_seconds")
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return fallback
        requested = float(value)
        if not math.isfinite(requested):
            return fallback
        return max(fallback, min(3600.0, max(0.0, requested)))


def require_automation_preflight_enabled(settings: Settings) -> None:
    if not settings.enable_automation_engine:
        raise AppError(
            "AUTOMATION_ENGINE_DISABLED",
            "自动化引擎默认关闭",
            status_code=403,
        )
    if not settings.enable_qb_read_only or not settings.qb_configured:
        raise AppError(
            "QB_READ_ONLY_NOT_ENABLED",
            "自动预检要求已启用并配置 qBittorrent 只读连接",
            status_code=409,
        )


def _preflight_state_hash(approval: ApprovalRequest) -> str:
    return canonical_hash(
        {
            "preflight_result": approval.preflight_result,
            "preflight_checked_at": (
                approval.preflight_checked_at.isoformat()
                if approval.preflight_checked_at is not None
                else None
            ),
        }
    )


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
