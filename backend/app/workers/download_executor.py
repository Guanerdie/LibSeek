from __future__ import annotations

import asyncio
import hmac
import re
import uuid
from collections.abc import Awaitable, Callable, Collection, Sequence
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.adapters.downloaders.qbittorrent import QbAddResult
from app.adapters.pt_sites.execution_registry import (
    PtExecutionAdapter,
    PtExecutionRegistry,
)
from app.core.config import Settings
from app.core.pt_site_rules import effective_hnr_rule
from app.core.security import sanitize_details
from app.errors import AppError
from app.models.entities import (
    ApprovalEvent,
    ApprovalRequest,
    DownloadExecution,
    DownloadExecutionEvent,
    DownloadJob,
    DownloadPlan,
)
from app.models.enums import (
    ApprovalStatus,
    AutomationMode,
    AutomationStage,
    DownloadExecutionStatus,
    DownloadLaunchMode,
    Origin,
)
from app.schemas.adapters import (
    PtSearchMode,
    TorrentCandidate,
    TorrentSearchRequest,
)
from app.schemas.approvals import ApprovalCandidateSnapshot
from app.schemas.qbittorrent import QbTorrent
from app.services.approvals import verify_download_plan, verify_snapshot
from app.services.automation import stage_mode
from app.services.automation_policy import get_current_policy
from app.services.executions import (
    finalize_download_submission,
    mark_outcome_unknown,
    mark_reconciliation_required,
    persist_info_hash_before_submission,
    qb_target_fingerprint,
    record_download_job_monitor_error,
    update_download_job_observation,
    verify_download_execution,
)
from app.services.preflight import (
    is_allowed_save_path,
    require_preflight_policy_current,
)
from app.services.torrent_validation import ValidatedTorrent, validate_torrent

_ERROR_CODE = re.compile(r"^[A-Z][A-Z0-9_]{0,79}$")
_ACTIVE_EXECUTION_STATES = {
    DownloadExecutionStatus.VALIDATING,
    DownloadExecutionStatus.SUBMITTING,
}
_TERMINAL_EXECUTION_STATES = {
    DownloadExecutionStatus.SUBMITTED,
    DownloadExecutionStatus.ALREADY_PRESENT,
    DownloadExecutionStatus.OUTCOME_UNKNOWN,
    DownloadExecutionStatus.RECONCILIATION_REQUIRED,
    DownloadExecutionStatus.RECONCILIATION_PENDING,
    DownloadExecutionStatus.FAILED,
    DownloadExecutionStatus.CANCELLED,
}


class QbExecutionAdapter(Protocol):
    def set_before_request_guard(
        self, guard: Callable[[], Awaitable[None]] | None
    ) -> None: ...

    async def authenticate(self) -> None: ...

    async def find_torrents_by_hashes(
        self, hashes: Collection[str]
    ) -> list[QbTorrent]: ...

    async def ensure_category(
        self,
        category: str,
        save_path: str,
        *,
        write_guard: Callable[[], Awaitable[None]] | None = None,
    ) -> None: ...

    async def add_torrent(
        self,
        torrent: bytes,
        *,
        expected_info_hash: str,
        save_path: str,
        category: str,
        tags: tuple[str, ...] = (),
        start_immediately: bool = True,
        category_prepared: bool = False,
        category_write_guard: Callable[[], Awaitable[None]] | None = None,
        write_guard: Callable[[], Awaitable[None]] | None = None,
    ) -> QbAddResult: ...

    async def aclose(self) -> None: ...


class QbMonitorAdapter(Protocol):
    async def authenticate(self) -> None: ...

    async def find_torrents_by_hashes(
        self, hashes: Collection[str]
    ) -> list[QbTorrent]: ...

    async def aclose(self) -> None: ...


@dataclass(frozen=True)
class ExecutionClaim:
    execution_id: str
    lease_token: str


@dataclass(frozen=True)
class ExecutionBinding:
    execution_id: str
    approval_id: str
    plan: DownloadPlan
    snapshot: ApprovalCandidateSnapshot
    launch_mode: DownloadLaunchMode
    origin: Origin


class DownloadExecutor:
    """Lease-fenced executor for one approved, immutable download plan at a time."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        worker_id: str,
        pt_execution_registry: PtExecutionRegistry,
        qb_factory: Callable[[], QbExecutionAdapter],
        settings: Settings,
    ) -> None:
        self.session_factory = session_factory
        self.worker_id = worker_id.strip()
        self.pt_execution_registry = pt_execution_registry
        self.qb_factory = qb_factory
        self.settings = settings
        if not self.worker_id or len(self.worker_id) > 180:
            raise ValueError("worker_id must be between 1 and 180 characters")

    async def run_once(self) -> bool:
        require_download_executor_enabled(self.settings)
        recovered = await self._recover_stale_executions()
        claim = await self._claim_next_execution()
        if claim is None:
            return recovered > 0

        stop_heartbeat = asyncio.Event()
        heartbeat = asyncio.create_task(self._heartbeat(claim, stop_heartbeat))
        try:
            await self._process_claim(claim)
        finally:
            stop_heartbeat.set()
            heartbeat.cancel()
            with suppress(asyncio.CancelledError):
                await heartbeat
        return True

    async def _process_claim(self, claim: ExecutionClaim) -> None:
        pt_site: PtExecutionAdapter | None = None
        qb: QbExecutionAdapter | None = None
        reservation_committed = False
        category_write_may_have_occurred = False
        torrent_add_may_have_occurred = False
        try:
            if not await self._ensure_external_action_allowed(
                claim, expected_status=DownloadExecutionStatus.VALIDATING
            ):
                return
            binding = await self._load_binding(claim)
            search_requests = self._search_requests(
                binding,
                self.pt_execution_registry.search_modes(
                    binding.plan.site_id,
                    media_type=binding.snapshot.media_type,
                ),
            )
            pt_site = await self.pt_execution_registry.create(
                binding.plan.site_id,
                media_type=binding.snapshot.media_type,
            )

            async def pt_request_guard() -> None:
                allowed = await self._ensure_external_action_allowed(
                    claim,
                    expected_status=DownloadExecutionStatus.VALIDATING,
                )
                if not allowed:
                    raise self._lease_lost()

            self._install_request_guard(pt_site, pt_request_guard)

            searched_candidates: list[TorrentCandidate] = []
            candidate: TorrentCandidate | None = None
            for search_request in search_requests:
                if not await self._ensure_external_action_allowed(
                    claim, expected_status=DownloadExecutionStatus.VALIDATING
                ):
                    return
                results = await pt_site.search(search_request)
                searched_candidates.extend(results)
                exact = self._matching_candidates(binding, results)
                if len(exact) > 1:
                    self._require_exact_candidate(binding, results)
                if exact:
                    candidate = self._require_exact_candidate(binding, results)
                    break
            if candidate is None:
                candidate = self._require_exact_candidate(binding, searched_candidates)

            if not await self._ensure_external_action_allowed(
                claim, expected_status=DownloadExecutionStatus.VALIDATING
            ):
                return
            torrent_payload = await pt_site.fetch_torrent(candidate.torrent_id)
            metadata = validate_torrent(
                torrent_payload,
                expected_info_hash=binding.plan.expected_info_hash,
                max_torrent_bytes=self.settings.torrent_max_bytes,
                max_files=self.settings.torrent_max_files,
            )
            self._require_torrent_candidate_consistency(binding, candidate, metadata)

            qb = self.qb_factory()
            qb_expected_status = DownloadExecutionStatus.VALIDATING

            async def qb_request_guard() -> None:
                allowed = await self._ensure_external_action_allowed(
                    claim,
                    expected_status=qb_expected_status,
                    external_write_may_have_occurred=torrent_add_may_have_occurred,
                )
                if not allowed:
                    raise self._lease_lost()

            self._install_request_guard(qb, qb_request_guard)
            if not await self._ensure_external_action_allowed(
                claim, expected_status=DownloadExecutionStatus.VALIDATING
            ):
                return
            await qb.authenticate()

            if not await self._ensure_external_action_allowed(
                claim, expected_status=DownloadExecutionStatus.VALIDATING
            ):
                return
            existing = self._find_unique_observation(
                metadata,
                await qb.find_torrents_by_hashes(sorted(metadata.identity_hashes)),
            )

            if existing is None:
                if not await self._ensure_external_action_allowed(
                    claim, expected_status=DownloadExecutionStatus.VALIDATING
                ):
                    return

                async def category_write_guard() -> None:
                    nonlocal category_write_may_have_occurred
                    allowed = await self._ensure_external_action_allowed(
                        claim,
                        expected_status=DownloadExecutionStatus.VALIDATING,
                    )
                    if not allowed:
                        raise self._lease_lost()
                    category_write_may_have_occurred = True

                await qb.ensure_category(
                    binding.plan.category,
                    self._required_save_path(),
                    write_guard=category_write_guard,
                )

            staged_status = await self._reserve_submission(claim, metadata)
            if staged_status == DownloadExecutionStatus.CANCELLED:
                return
            if staged_status != DownloadExecutionStatus.SUBMITTING:
                raise AppError(
                    "DOWNLOAD_EXECUTION_TRANSITION_INVALID",
                    "下载执行未进入提交预留状态",
                    status_code=409,
                )
            reservation_committed = True
            qb_expected_status = DownloadExecutionStatus.SUBMITTING

            if existing is not None:
                outcome = DownloadExecutionStatus.ALREADY_PRESENT
            else:
                async def torrent_add_write_guard() -> None:
                    nonlocal torrent_add_may_have_occurred
                    await self._write_guard(claim)
                    torrent_add_may_have_occurred = True

                add_result = await qb.add_torrent(
                    torrent_payload,
                    expected_info_hash=self._selected_info_hash(binding.plan, metadata),
                    save_path=self._required_save_path(),
                    category=binding.plan.category,
                    tags=tuple(binding.plan.tags),
                    start_immediately=(
                        binding.launch_mode == DownloadLaunchMode.START_IMMEDIATELY
                    ),
                    category_prepared=True,
                    write_guard=torrent_add_write_guard,
                )
                if add_result.info_hash.casefold() not in metadata.identity_hashes:
                    raise AppError(
                        "QB_ADD_INFO_HASH_MISMATCH",
                        "qBittorrent 添加结果与已校验种子不一致",
                        status_code=502,
                    )
                outcome = DownloadExecutionStatus(add_result.outcome)

            if not await self._ensure_external_action_allowed(
                claim,
                expected_status=DownloadExecutionStatus.SUBMITTING,
                external_write_may_have_occurred=torrent_add_may_have_occurred,
            ):
                return
            observed = self._find_unique_observation(
                metadata,
                await qb.find_torrents_by_hashes(sorted(metadata.identity_hashes)),
            )
            if observed is None:
                raise AppError(
                    "QB_SUBMISSION_NOT_OBSERVED",
                    "qBittorrent 提交后未观察到已校验种子",
                    status_code=502,
                )
            if (
                outcome == DownloadExecutionStatus.SUBMITTED
                and binding.launch_mode == DownloadLaunchMode.ADD_PAUSED
                and observed.state.casefold()
                not in {"pauseddl", "pausedup", "stoppeddl", "stoppedup"}
            ):
                raise AppError(
                    "QB_ADD_PAUSED_NOT_OBSERVED",
                    "qBittorrent 未保持添加后暂停状态，必须人工对账",
                    status_code=502,
                )
            if not await self._ensure_external_action_allowed(
                claim,
                expected_status=DownloadExecutionStatus.SUBMITTING,
                external_write_may_have_occurred=torrent_add_may_have_occurred,
            ):
                return

            async with self.session_factory() as session:
                await finalize_download_submission(
                    session,
                    claim.execution_id,
                    observed,
                    outcome,
                    self.settings,
                    actor=self.worker_id,
                    lease_token=claim.lease_token,
                )
                await session.commit()
        except AppError as exc:
            if exc.error_code == "DOWNLOAD_EXECUTION_LEASE_LOST":
                return
            if reservation_committed:
                await self._mark_reserved_failure(
                    claim,
                    exc,
                    torrent_add_may_have_occurred=torrent_add_may_have_occurred,
                )
            else:
                await self._mark_validation_failure(
                    claim,
                    exc,
                    category_write_may_have_occurred=(
                        category_write_may_have_occurred
                    ),
                )
        except Exception:
            error = AppError(
                "DOWNLOAD_EXECUTOR_INTERNAL_ERROR",
                "下载执行器发生内部错误",
                status_code=500,
                retryable=False,
            )
            if reservation_committed:
                await self._mark_reserved_failure(
                    claim,
                    error,
                    torrent_add_may_have_occurred=torrent_add_may_have_occurred,
                )
            else:
                await self._mark_validation_failure(
                    claim,
                    error,
                    category_write_may_have_occurred=(
                        category_write_may_have_occurred
                    ),
                )
        finally:
            if qb is not None:
                with suppress(Exception):
                    await qb.aclose()
            if pt_site is not None:
                with suppress(Exception):
                    await pt_site.aclose()

    async def _claim_next_execution(self) -> ExecutionClaim | None:
        async with self.session_factory() as session:
            now = await _database_now(session)
            eligible = or_(
                DownloadExecution.status == DownloadExecutionStatus.PENDING,
                and_(
                    DownloadExecution.status == DownloadExecutionStatus.RETRY_WAIT,
                    or_(
                        DownloadExecution.next_retry_at.is_(None),
                        DownloadExecution.next_retry_at <= now,
                    ),
                ),
            )
            execution = await session.scalar(
                select(DownloadExecution)
                .where(eligible, DownloadExecution.attempts < DownloadExecution.max_attempts)
                .order_by(DownloadExecution.created_at, DownloadExecution.id)
                .with_for_update(skip_locked=True)
                .limit(1)
            )
            if execution is None:
                return None
            previous = execution.status
            lease_token = str(uuid.uuid4())
            execution.status = DownloadExecutionStatus.VALIDATING
            execution.attempts += 1
            execution.next_retry_at = None
            execution.locked_at = now
            execution.locked_by = self.worker_id
            execution.lease_token = lease_token
            execution.error_code = None
            execution.error_message = None
            self._add_execution_event(
                session,
                execution,
                event_type="VALIDATION_CLAIMED",
                from_status=previous,
                to_status=DownloadExecutionStatus.VALIDATING,
                details={"attempt": execution.attempts},
            )
            await session.commit()
            return ExecutionClaim(execution.id, lease_token)

    async def _load_binding(self, claim: ExecutionClaim) -> ExecutionBinding:
        async with self.session_factory() as session:
            execution = await session.get(DownloadExecution, claim.execution_id)
            if execution is None:
                raise AppError(
                    "DOWNLOAD_EXECUTION_NOT_FOUND", "下载执行记录不存在", status_code=404
                )
            self._require_matching_lease(execution, claim)
            await verify_download_execution(session, execution)
            approval = await session.get(ApprovalRequest, execution.approval_id)
            if approval is None:
                raise AppError(
                    "APPROVAL_REQUEST_NOT_FOUND", "审批请求不存在", status_code=404
                )
            snapshot = verify_snapshot(approval)
            if approval.status != ApprovalStatus.APPROVED:
                raise AppError(
                    "DOWNLOAD_EXECUTION_APPROVAL_INVALIDATED",
                    "审批不再处于可执行状态",
                    status_code=409,
                )
            plan = await session.scalar(
                select(DownloadPlan).where(DownloadPlan.approval_id == approval.id).limit(1)
            )
            if plan is None:
                raise AppError(
                    "DOWNLOAD_PLAN_NOT_FOUND", "审批没有对应下载计划", status_code=409
                )
            verify_download_plan(plan, approval)
            require_preflight_policy_current(
                plan.preflight_policy_fingerprint,
                self.settings,
            )
            if qb_target_fingerprint(
                self.settings,
                plan,
                execution.launch_mode,
                snapshot.media_title,
            ) != (
                execution.qb_target_fingerprint
            ):
                raise AppError(
                    "EXECUTION_TARGET_CONFIG_CHANGED",
                    "qBittorrent 目标配置与执行意图不一致",
                    status_code=409,
                )
            if plan.site_id != snapshot.site_id:
                raise AppError(
                    "DOWNLOAD_PLAN_BINDING_INVALID",
                    "下载计划与审批候选的 PT 站点绑定不一致",
                    status_code=409,
                )
            if plan.torrent_ref != snapshot.torrent_ref:
                raise AppError(
                    "DOWNLOAD_PLAN_BINDING_INVALID",
                    "下载计划与审批候选引用不一致",
                    status_code=409,
                )
            return ExecutionBinding(
                execution_id=execution.id,
                approval_id=approval.id,
                plan=plan,
                snapshot=snapshot,
                launch_mode=execution.launch_mode,
                origin=execution.origin,
            )

    async def _reserve_submission(
        self, claim: ExecutionClaim, metadata: ValidatedTorrent
    ) -> DownloadExecutionStatus:
        async with self.session_factory() as session:
            execution = await session.get(
                DownloadExecution, claim.execution_id, with_for_update=True
            )
            if execution is None:
                raise AppError(
                    "DOWNLOAD_EXECUTION_NOT_FOUND", "下载执行记录不存在", status_code=404
                )
            self._require_fresh_matching_lease(
                execution,
                claim,
                expected_status=DownloadExecutionStatus.VALIDATING,
                now=await _database_now(session),
            )
            staged = await persist_info_hash_before_submission(
                session,
                claim.execution_id,
                metadata,
                actor=self.worker_id,
                lease_token=claim.lease_token,
                lease_seconds=self.settings.download_execution_lease_seconds,
            )
            status = staged.status
            await session.commit()
            return status

    async def _ensure_external_action_allowed(
        self,
        claim: ExecutionClaim,
        *,
        expected_status: DownloadExecutionStatus,
        external_write_may_have_occurred: bool = False,
    ) -> bool:
        async with self.session_factory() as session:
            execution = await session.get(
                DownloadExecution, claim.execution_id, with_for_update=True
            )
            if execution is None:
                raise AppError(
                    "DOWNLOAD_EXECUTION_LEASE_LOST",
                    "下载执行租约已失效，禁止外部请求",
                    status_code=409,
                )
            now = await _database_now(session)
            self._require_fresh_matching_lease(
                execution,
                claim,
                expected_status=expected_status,
                now=now,
            )
            approval = await session.get(
                ApprovalRequest, execution.approval_id, with_for_update=True
            )
            plan = await session.scalar(
                select(DownloadPlan)
                .where(DownloadPlan.approval_id == execution.approval_id)
                .limit(1)
            )
            if plan is None:
                raise AppError(
                    "DOWNLOAD_PLAN_NOT_FOUND",
                    "审批没有对应下载计划",
                    status_code=409,
                )
            if approval is not None:
                verify_download_plan(plan, approval)
            require_preflight_policy_current(
                plan.preflight_policy_fingerprint,
                self.settings,
            )
            automation_error = await self._automation_fence_error(
                session, execution, approval
            )
            if automation_error is not None:
                if external_write_may_have_occurred:
                    raise AppError(
                        "AUTOMATION_POLICY_CHANGED_AFTER_WRITE",
                        "自动化策略在 qBittorrent 写入可能发生后变化，必须人工对账",
                        status_code=409,
                    )
                await self._cancel_by_automation_fence(
                    session,
                    execution,
                    approval,
                    error_code=automation_error,
                    now=now,
                )
                await session.commit()
                return False
            if expected_status == DownloadExecutionStatus.VALIDATING:
                if approval is not None:
                    verify_snapshot(approval)
                    if (
                        approval.status == ApprovalStatus.APPROVED
                        and _as_utc(approval.expires_at) <= _as_utc(now)
                    ):
                        previous_approval = approval.status
                        approval.status = ApprovalStatus.EXPIRED
                        approval.decided_at = now
                        session.add(
                            ApprovalEvent(
                                approval_request_id=approval.id,
                                event_type="EXPIRED",
                                from_status=previous_approval.value,
                                to_status=ApprovalStatus.EXPIRED.value,
                                actor="system",
                                reason="审批有效期已结束",
                                snapshot_hash=approval.snapshot_hash,
                                sanitized_details={},
                            )
                        )
                if approval is None or approval.status != ApprovalStatus.APPROVED:
                    previous = execution.status
                    execution.status = DownloadExecutionStatus.CANCELLED
                    execution.next_retry_at = None
                    execution.locked_at = None
                    execution.locked_by = None
                    execution.lease_token = None
                    execution.error_code = "APPROVAL_INVALIDATED"
                    execution.error_message = "审批已撤销、过期或不存在，禁止继续提交"
                    self._add_execution_event(
                        session,
                        execution,
                        event_type="APPROVAL_INVALIDATED",
                        from_status=previous,
                        to_status=DownloadExecutionStatus.CANCELLED,
                        details={
                            "approval_status": (
                                approval.status.value if approval is not None else "NOT_FOUND"
                            ),
                            "automatic_retry_allowed": False,
                            "external_request_performed": False,
                        },
                    )
                    await session.commit()
                    return False
            elif approval is None or approval.status != ApprovalStatus.EXECUTING:
                raise AppError(
                    "DOWNLOAD_EXECUTION_APPROVAL_INVALIDATED",
                    "提交预留的审批不再处于 EXECUTING 状态",
                    status_code=409,
                )
            execution.locked_at = now
            await session.commit()
            return True

    async def _heartbeat(
        self, claim: ExecutionClaim, stop_event: asyncio.Event
    ) -> None:
        interval = self.settings.download_execution_lease_renew_interval_seconds
        while not stop_event.is_set():
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=interval)
                return
            except TimeoutError:
                pass
            if not await self._renew_lease(claim):
                return

    async def _write_guard(self, claim: ExecutionClaim) -> None:
        allowed = await self._ensure_external_action_allowed(
            claim, expected_status=DownloadExecutionStatus.SUBMITTING
        )
        if not allowed:
            raise self._lease_lost()

    @staticmethod
    def _install_request_guard(
        adapter: object,
        guard: Callable[[], Awaitable[None]],
    ) -> None:
        set_request_guard = getattr(adapter, "set_before_request_guard", None)
        if not callable(set_request_guard):
            raise AppError(
                "DOWNLOAD_EXECUTION_REQUEST_GUARD_UNAVAILABLE",
                "外部适配器无法安装逐请求下载执行围栏",
                status_code=409,
            )
        set_request_guard(guard)

    async def _automation_fence_error(
        self,
        session: AsyncSession,
        execution: DownloadExecution,
        approval: ApprovalRequest | None,
    ) -> str | None:
        if execution.origin != Origin.AUTOMATION:
            return None
        if (
            execution.launch_mode != DownloadLaunchMode.ADD_PAUSED
            or not self.settings.enable_automation_engine
            or not self.settings.enable_download_execution_control_plane
            or not self.settings.download_executor_enabled
        ):
            return "AUTOMATION_EXECUTION_GATE_DISABLED"
        if approval is None:
            return "AUTOMATION_APPROVAL_MISSING"
        snapshot = verify_snapshot(approval)
        if not effective_hnr_rule(snapshot.site_id, snapshot.hit_and_run).known:
            return "HNR_UNKNOWN"
        _, current = await get_current_policy(session)
        if execution.automation_policy_revision_id != current.id:
            return "AUTOMATION_POLICY_REVISION_CHANGED"
        if stage_mode(current, AutomationStage.EXECUTION) != AutomationMode.AUTO_IF_ELIGIBLE:
            return "AUTOMATION_EXECUTION_MODE_CHANGED"
        return None

    def _add_approval_fence_event(
        self,
        session: AsyncSession,
        approval: ApprovalRequest,
        *,
        previous: ApprovalStatus,
        error_code: str,
    ) -> None:
        session.add(
            ApprovalEvent(
                approval_request_id=approval.id,
                event_type="AUTOMATION_EXECUTION_CANCELLED",
                from_status=previous.value,
                to_status=ApprovalStatus.REVOKED.value,
                actor=self.worker_id,
                reason="自动化策略或安全资格在外部写入前失效",
                snapshot_hash=approval.snapshot_hash,
                sanitized_details={"error_code": error_code},
            )
        )

    async def _cancel_by_automation_fence(
        self,
        session: AsyncSession,
        execution: DownloadExecution,
        approval: ApprovalRequest | None,
        *,
        error_code: str,
        now: datetime,
    ) -> None:
        previous = execution.status
        execution.status = DownloadExecutionStatus.CANCELLED
        execution.error_code = error_code
        execution.error_message = "自动化策略或安全资格已变化，外部写入前取消"
        execution.next_retry_at = None
        execution.locked_at = None
        execution.locked_by = None
        execution.lease_token = None
        if approval is not None and approval.status in {
            ApprovalStatus.APPROVED,
            ApprovalStatus.EXECUTING,
        }:
            previous_approval = approval.status
            approval.status = ApprovalStatus.REVOKED
            approval.decided_at = now
            self._add_approval_fence_event(
                session,
                approval,
                previous=previous_approval,
                error_code=error_code,
            )
        self._add_execution_event(
            session,
            execution,
            event_type="AUTOMATION_FENCE_CANCELLED",
            from_status=previous,
            to_status=DownloadExecutionStatus.CANCELLED,
            details={
                "error_code": error_code,
                "automatic_retry_allowed": False,
                "external_write_performed": False,
            },
        )
        await session.flush()

    async def _renew_lease(self, claim: ExecutionClaim) -> bool:
        async with self.session_factory() as session:
            execution = await session.get(
                DownloadExecution, claim.execution_id, with_for_update=True
            )
            if execution is None:
                return False
            now = await _database_now(session)
            try:
                self._require_fresh_matching_lease(
                    execution,
                    claim,
                    expected_status=None,
                    now=now,
                )
            except AppError:
                return False
            execution.locked_at = now
            await session.commit()
            return True

    async def _recover_stale_executions(self) -> int:
        async with self.session_factory() as session:
            now = await _database_now(session)
            cutoff = now - timedelta(seconds=self.settings.download_execution_lease_seconds)
            stale = list(
                (
                    await session.scalars(
                        select(DownloadExecution)
                        .where(
                            DownloadExecution.status.in_(_ACTIVE_EXECUTION_STATES),
                            DownloadExecution.locked_at < cutoff,
                        )
                        .order_by(DownloadExecution.locked_at, DownloadExecution.id)
                        .with_for_update(skip_locked=True)
                        .limit(50)
                    )
                ).all()
            )
            for execution in stale:
                previous = execution.status
                if previous == DownloadExecutionStatus.SUBMITTING:
                    target = DownloadExecutionStatus.RECONCILIATION_REQUIRED
                    execution.next_retry_at = None
                    execution.error_code = "EXECUTOR_STALE_AFTER_RESERVATION"
                    execution.error_message = (
                        "提交预留后的执行租约过期，必须人工对账，禁止自动重试"
                    )
                    event_type = "STALE_SUBMISSION_REQUIRES_RECONCILIATION"
                else:
                    retry = execution.attempts < execution.max_attempts
                    target = (
                        DownloadExecutionStatus.RETRY_WAIT
                        if retry
                        else DownloadExecutionStatus.FAILED
                    )
                    execution.next_retry_at = (
                        now + timedelta(seconds=self._retry_delay(execution.attempts))
                        if retry
                        else None
                    )
                    execution.error_code = "EXECUTOR_VALIDATION_LEASE_EXPIRED"
                    execution.error_message = "校验阶段执行租约过期"
                    event_type = "STALE_VALIDATION_RECOVERED"
                execution.status = target
                execution.locked_at = None
                execution.locked_by = None
                execution.lease_token = None
                self._add_execution_event(
                    session,
                    execution,
                    event_type=event_type,
                    from_status=previous,
                    to_status=target,
                    details={
                        "attempt": execution.attempts,
                        "automatic_retry_allowed": target == DownloadExecutionStatus.RETRY_WAIT,
                    },
                )
            if stale:
                await session.commit()
            return len(stale)

    async def _mark_validation_failure(
        self,
        claim: ExecutionClaim,
        error: AppError,
        *,
        category_write_may_have_occurred: bool = False,
    ) -> None:
        async with self.session_factory() as session:
            execution = await session.get(
                DownloadExecution, claim.execution_id, with_for_update=True
            )
            if execution is None or not self._owns_lease(execution, claim):
                return
            if execution.status in _TERMINAL_EXECUTION_STATES:
                return
            if execution.status != DownloadExecutionStatus.VALIDATING:
                return
            retry = error.retryable and execution.attempts < execution.max_attempts
            target = (
                DownloadExecutionStatus.RETRY_WAIT
                if retry
                else DownloadExecutionStatus.FAILED
            )
            previous = execution.status
            execution.status = target
            now = await _database_now(session)
            execution.next_retry_at = (
                now + timedelta(seconds=self._retry_delay(execution.attempts))
                if retry
                else None
            )
            execution.locked_at = None
            execution.locked_by = None
            execution.lease_token = None
            execution.error_code = self._safe_error_code(error)
            execution.error_message = self._validation_error_message(
                error,
                attempt=execution.attempts,
                max_attempts=execution.max_attempts,
                retry=retry,
            )
            event_details: dict[str, object] = {
                "error_code": execution.error_code,
                "attempt": execution.attempts,
                "automatic_retry_allowed": retry,
                "external_write_performed": False,
            }
            upstream_status_code = self._safe_upstream_status_code(error)
            if upstream_status_code is not None:
                event_details["upstream_status_code"] = upstream_status_code
            if category_write_may_have_occurred:
                event_details["category_write_may_have_occurred"] = True
            self._add_execution_event(
                session,
                execution,
                event_type="VALIDATION_RETRY_SCHEDULED" if retry else "VALIDATION_FAILED",
                from_status=previous,
                to_status=target,
                details=event_details,
            )
            await session.commit()

    async def _mark_reserved_failure(
        self,
        claim: ExecutionClaim,
        error: AppError,
        *,
        torrent_add_may_have_occurred: bool,
    ) -> None:
        error_code = self._safe_error_code(error)
        async with self.session_factory() as session:
            execution = await session.get(
                DownloadExecution, claim.execution_id, with_for_update=True
            )
            if execution is None or not self._owns_lease(execution, claim):
                return
            if execution.status != DownloadExecutionStatus.SUBMITTING:
                return
            if torrent_add_may_have_occurred:
                await mark_outcome_unknown(
                    session,
                    claim.execution_id,
                    actor=self.worker_id,
                    lease_token=claim.lease_token,
                    lease_seconds=self.settings.download_execution_lease_seconds,
                    error_code=error_code,
                    error_message=(
                        "qBittorrent 写入可能已经发生，必须人工对账，禁止自动重试"
                    ),
                )
            else:
                await mark_reconciliation_required(
                    session,
                    claim.execution_id,
                    actor=self.worker_id,
                    lease_token=claim.lease_token,
                    lease_seconds=self.settings.download_execution_lease_seconds,
                    error_code=error_code,
                    error_message=(
                        "提交已预留但未完成验证，必须人工确认后续动作，禁止自动重试"
                    ),
                )
            await session.commit()

    def _require_fresh_matching_lease(
        self,
        execution: DownloadExecution,
        claim: ExecutionClaim,
        *,
        expected_status: DownloadExecutionStatus | None,
        now: datetime,
    ) -> None:
        self._require_matching_lease(execution, claim)
        if expected_status is not None and execution.status != expected_status:
            raise self._lease_lost()
        if execution.status not in _ACTIVE_EXECUTION_STATES or execution.locked_at is None:
            raise self._lease_lost()
        expires_at = _as_utc(execution.locked_at) + timedelta(
            seconds=self.settings.download_execution_lease_seconds
        )
        if expires_at <= _as_utc(now):
            raise self._lease_lost()

    def _require_matching_lease(
        self, execution: DownloadExecution, claim: ExecutionClaim
    ) -> None:
        if not self._owns_lease(execution, claim):
            raise self._lease_lost()

    def _owns_lease(self, execution: DownloadExecution, claim: ExecutionClaim) -> bool:
        return (
            execution.lease_token is not None
            and hmac.compare_digest(execution.lease_token, claim.lease_token)
            and execution.locked_by is not None
            and hmac.compare_digest(execution.locked_by, self.worker_id)
            and execution.locked_at is not None
        )

    @staticmethod
    def _lease_lost() -> AppError:
        return AppError(
            "DOWNLOAD_EXECUTION_LEASE_LOST",
            "下载执行租约已过期或被其他 Worker 接管，禁止外部请求",
            status_code=409,
        )

    def _retry_delay(self, attempts: int) -> int:
        delay: int = self.settings.download_execution_retry_base_seconds * 2 ** max(
            attempts - 1, 0
        )
        return int(min(delay, self.settings.download_execution_retry_max_seconds))

    def _required_save_path(self) -> str:
        value = self.settings.qb_target_save_path
        if value is None or not value.strip():
            raise AppError(
                "EXECUTION_TARGET_NOT_CONFIGURED",
                "qBittorrent 保存路径尚未配置",
                status_code=409,
            )
        return value

    @staticmethod
    def _search_requests(
        binding: ExecutionBinding,
        search_modes: tuple[PtSearchMode, ...],
    ) -> tuple[TorrentSearchRequest, ...]:
        common: dict[str, object] = {
            "type": binding.snapshot.media_type,
            "limit": 100,
        }
        requests: list[TorrentSearchRequest] = []
        if (
            PtSearchMode.TMDB_ID in search_modes
            and binding.snapshot.tmdb_id is not None
        ):
            requests.append(TorrentSearchRequest(tmdb=binding.snapshot.tmdb_id, **common))
        if PtSearchMode.TEXT in search_modes:
            requests.append(
                TorrentSearchRequest(search=binding.plan.release_title, **common)
            )
        if not requests:
            raise AppError(
                "PT_SITE_EXECUTION_SEARCH_UNSUPPORTED",
                "PT 站点无法使用审批快照中的标识重新绑定固定候选",
                status_code=409,
            )
        return tuple(requests)

    @staticmethod
    def _matching_candidates(
        binding: ExecutionBinding,
        candidates: Sequence[TorrentCandidate],
    ) -> list[TorrentCandidate]:
        return [
            candidate
            for candidate in candidates
            if candidate.site_id == binding.plan.site_id
            and candidate.torrent_id == binding.snapshot.torrent_id
        ]

    @staticmethod
    def _require_exact_candidate(
        binding: ExecutionBinding, candidates: Sequence[TorrentCandidate]
    ) -> TorrentCandidate:
        exact = DownloadExecutor._matching_candidates(binding, candidates)
        if len(exact) != 1:
            raise AppError(
                _pt_error_code(
                    binding.plan.site_id,
                    avistaz="AVISTAZ_TORRENT_ID_NOT_UNIQUE",
                    generic="PT_SITE_TORRENT_ID_NOT_UNIQUE",
                ),
                "PT 站点重新搜索未得到唯一的已批准 torrent ID",
                status_code=409,
            )
        candidate = exact[0]
        if _normalized_title(candidate.release_title) != _normalized_title(
            binding.plan.release_title
        ):
            raise AppError(
                _pt_error_code(
                    binding.plan.site_id,
                    avistaz="AVISTAZ_CANDIDATE_BINDING_DRIFT",
                    generic="PT_SITE_CANDIDATE_BINDING_DRIFT",
                ),
                "PT 站点候选标题与不可变下载计划不一致",
                status_code=409,
            )
        tmdb_binding_mismatch = (
            candidate.tmdb_id is not None
            and binding.snapshot.tmdb_id is not None
            and candidate.tmdb_id != binding.snapshot.tmdb_id
        )
        if binding.origin == Origin.AUTOMATION:
            tmdb_binding_mismatch = (
                candidate.tmdb_id is None
                or binding.snapshot.tmdb_id is None
                or candidate.tmdb_id != binding.snapshot.tmdb_id
            )
        if tmdb_binding_mismatch:
            raise AppError(
                _pt_error_code(
                    binding.plan.site_id,
                    avistaz="AVISTAZ_CANDIDATE_BINDING_DRIFT",
                    generic="PT_SITE_CANDIDATE_BINDING_DRIFT",
                ),
                "PT 站点候选 TMDB ID 与不可变审批不一致",
                status_code=409,
            )
        candidate_hnr = effective_hnr_rule(candidate.site_id, candidate.hit_and_run)
        snapshot_hnr = effective_hnr_rule(
            binding.snapshot.site_id, binding.snapshot.hit_and_run
        )
        if binding.origin == Origin.AUTOMATION and (
            not candidate_hnr.known
            or not snapshot_hnr.known
            or candidate_hnr.applies != snapshot_hnr.applies
        ):
            raise AppError(
                _pt_error_code(
                    binding.plan.site_id,
                    avistaz="AVISTAZ_HNR_BINDING_DRIFT",
                    generic="PT_SITE_HNR_BINDING_DRIFT",
                ),
                "PT 站点候选 H&R 信息与自动审批快照不一致",
                status_code=409,
            )
        return candidate

    @staticmethod
    def _require_torrent_candidate_consistency(
        binding: ExecutionBinding,
        candidate: TorrentCandidate,
        metadata: ValidatedTorrent,
    ) -> None:
        if candidate.info_hash is not None and not metadata.matches_hash(candidate.info_hash):
            raise AppError(
                _pt_error_code(
                    binding.plan.site_id,
                    avistaz="AVISTAZ_CANDIDATE_INFO_HASH_DRIFT",
                    generic="PT_SITE_CANDIDATE_INFO_HASH_DRIFT",
                ),
                "PT 站点候选 info hash 与实际种子不一致",
                status_code=409,
            )
        expected_size = binding.plan.estimated_size_bytes
        if expected_size is not None and metadata.total_size_bytes != expected_size:
            raise AppError(
                _pt_error_code(
                    binding.plan.site_id,
                    avistaz="AVISTAZ_CANDIDATE_SIZE_DRIFT",
                    generic="PT_SITE_CANDIDATE_SIZE_DRIFT",
                ),
                "实际种子大小与不可变下载计划不一致",
                status_code=409,
            )
        if candidate.size_bytes is not None and metadata.total_size_bytes != candidate.size_bytes:
            raise AppError(
                _pt_error_code(
                    binding.plan.site_id,
                    avistaz="AVISTAZ_CANDIDATE_SIZE_DRIFT",
                    generic="PT_SITE_CANDIDATE_SIZE_DRIFT",
                ),
                "PT 站点候选大小与实际种子不一致",
                status_code=409,
            )

    @staticmethod
    def _selected_info_hash(plan: DownloadPlan, metadata: ValidatedTorrent) -> str:
        selected = plan.expected_info_hash or metadata.info_hash_v1 or metadata.info_hash_v2
        if selected is None:
            raise AppError(
                "DOWNLOAD_EXECUTION_INFO_HASH_INVALID",
                "种子没有可提交的 info hash",
                status_code=409,
            )
        return selected.casefold()

    @staticmethod
    def _find_unique_observation(
        metadata: ValidatedTorrent, observations: Sequence[QbTorrent]
    ) -> QbTorrent | None:
        matches = [
            observed
            for observed in observations
            if not metadata.identity_hashes.isdisjoint(observed.identity_hashes)
        ]
        if len(matches) > 1:
            raise AppError(
                "QB_INFO_HASH_AMBIGUOUS",
                "qBittorrent 返回多个匹配同一已校验种子的任务",
                status_code=502,
            )
        return matches[0] if matches else None

    @staticmethod
    def _safe_error_code(error: AppError) -> str:
        return (
            error.error_code
            if _ERROR_CODE.fullmatch(error.error_code)
            else "DOWNLOAD_EXECUTOR_INTERNAL_ERROR"
        )

    @staticmethod
    def _safe_upstream_status_code(error: AppError) -> int | None:
        value = error.details.get("upstream_status_code")
        return value if type(value) is int and 400 <= value <= 599 else None

    @classmethod
    def _validation_error_message(
        cls,
        error: AppError,
        *,
        attempt: int,
        max_attempts: int,
        retry: bool,
    ) -> str:
        upstream_status_code = cls._safe_upstream_status_code(error)
        if upstream_status_code == 403 and error.error_code in {
            "AVISTAZ_TORRENT_FETCH_FAILED",
            "AVISTAZ_TORRENT_FETCH_FORBIDDEN",
        }:
            retry_status = (
                "已安排自动重试"
                if retry
                else "未安排自动重试或重试次数已耗尽"
            )
            return (
                f"AvistaZ 返回 HTTP {upstream_status_code}，{retry_status}"
                f"（第 {attempt}/{max_attempts} 次）"
            )
        return "下载执行在写入前失败，请根据错误代码处理"

    def _add_execution_event(
        self,
        session: AsyncSession,
        execution: DownloadExecution,
        *,
        event_type: str,
        from_status: DownloadExecutionStatus,
        to_status: DownloadExecutionStatus,
        details: dict[str, object],
    ) -> None:
        session.add(
            DownloadExecutionEvent(
                download_execution_id=execution.id,
                event_type=event_type,
                from_status=from_status.value,
                to_status=to_status.value,
                actor=self.worker_id,
                sanitized_details=sanitize_details(details),
            )
        )


class DownloadMonitor:
    """Read-only qB observer; it never invokes downloader mutation methods."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        monitor_id: str,
        qb_factory: Callable[[], QbMonitorAdapter],
        settings: Settings,
    ) -> None:
        self.session_factory = session_factory
        self.monitor_id = monitor_id.strip()
        self.qb_factory = qb_factory
        self.settings = settings
        self._cursor: tuple[datetime, str] | None = None
        if not self.monitor_id or len(self.monitor_id) > 120:
            raise ValueError("monitor_id must be between 1 and 120 characters")

    def reconfigure(
        self,
        settings: Settings,
        qb_factory: Callable[[], QbMonitorAdapter],
    ) -> None:
        self.settings = settings
        self.qb_factory = qb_factory

    async def run_once(self) -> int:
        require_download_monitor_enabled(self.settings)
        expected_save_path = self.settings.qb_target_save_path
        if expected_save_path is None:
            raise AppError(
                "DOWNLOAD_MONITOR_TARGET_NOT_CONFIGURED",
                "qBittorrent monitoring target is not configured",
                status_code=409,
            )
        jobs = await self._next_jobs()
        if not jobs:
            self._cursor = None
            jobs = await self._next_jobs()
        if not jobs:
            return 0

        qb = self.qb_factory()
        try:
            await qb.authenticate()
            hashes = {
                info_hash
                for job in jobs
                for info_hash in self._job_hashes(job)
            }
            observations = (
                await qb.find_torrents_by_hashes(sorted(hashes)) if hashes else []
            )
        finally:
            await qb.aclose()

        updated = 0
        for job in jobs:
            try:
                observed = self._job_observation(job, observations)
            except AppError as error:
                error_code = (
                    error.error_code
                    if _ERROR_CODE.fullmatch(error.error_code)
                    else "DOWNLOAD_MONITOR_INTERNAL_ERROR"
                )
                async with self.session_factory() as session:
                    await record_download_job_monitor_error(
                        session,
                        job.id,
                        error_code=error_code,
                        error_message=error.message,
                        actor=self.monitor_id,
                        details=error.details,
                    )
                    await session.commit()
                updated += 1
                continue
            async with self.session_factory() as session:
                await update_download_job_observation(
                    session,
                    job.id,
                    observed,
                    expected_save_path=expected_save_path,
                    actor=self.monitor_id,
                )
                await session.commit()
            updated += 1
        last = jobs[-1]
        self._cursor = (_as_utc(last.created_at), last.id)
        return updated

    async def _next_jobs(self) -> list[DownloadJob]:
        async with self.session_factory() as session:
            statement = select(DownloadJob)
            if self._cursor is not None:
                created_at, job_id = self._cursor
                statement = statement.where(
                    or_(
                        DownloadJob.created_at > created_at,
                        and_(DownloadJob.created_at == created_at, DownloadJob.id > job_id),
                    )
                )
            return list(
                (
                    await session.scalars(
                        statement.order_by(DownloadJob.created_at, DownloadJob.id).limit(
                            self.settings.download_monitor_batch_size
                        )
                    )
                ).all()
            )

    @staticmethod
    def _job_hashes(job: DownloadJob) -> frozenset[str]:
        hashes = {value for value in (job.info_hash_v1, job.info_hash_v2) if value}
        if job.info_hash_v2 is not None:
            hashes.add(job.info_hash_v2[:40])
        return frozenset(hashes)

    @staticmethod
    def _job_observation(
        job: DownloadJob, observations: Sequence[QbTorrent]
    ) -> QbTorrent | None:
        hashes = DownloadMonitor._job_hashes(job)
        matches = [
            observed
            for observed in observations
            if not hashes.isdisjoint(observed.identity_hashes)
        ]
        if len(matches) > 1:
            raise AppError(
                "QB_INFO_HASH_AMBIGUOUS",
                "qBittorrent 返回多个匹配下载任务的观察结果",
                status_code=502,
                details={"match_count": len(matches)},
            )
        return matches[0] if matches else None


def require_download_executor_enabled(settings: Settings) -> None:
    missing = [
        name
        for name, enabled in (
            ("ENABLE_DOWNLOAD_EXECUTOR", settings.enable_download_executor),
            ("ENABLE_AVISTAZ_TORRENT_FETCH", settings.enable_avistaz_torrent_fetch),
            ("ENABLE_QB_WRITE", settings.enable_qb_write),
        )
        if not enabled
    ]
    if missing:
        raise AppError(
            "DOWNLOAD_EXECUTOR_DISABLED",
            "下载执行器默认关闭，所有执行开关必须同时启用",
            status_code=403,
            details={"missing_flags": missing},
        )


def require_download_monitor_enabled(settings: Settings) -> None:
    if not settings.enable_download_monitor or not settings.enable_qb_read_only:
        raise AppError(
            "DOWNLOAD_MONITOR_DISABLED",
            "下载监控默认关闭，且只允许在 qBittorrent 只读模式下启用",
            status_code=403,
        )
    save_path = settings.qb_target_save_path
    if (
        save_path is None
        or not save_path.strip()
        or any(marker in save_path for marker in ("\x00", "\r", "\n"))
    ):
        raise AppError(
            "DOWNLOAD_MONITOR_TARGET_NOT_CONFIGURED",
            "qBittorrent monitoring target is not configured",
            status_code=409,
        )
    if not settings.qb_allowed_save_paths or not is_allowed_save_path(
        save_path, settings.qb_allowed_save_paths
    ):
        raise AppError(
            "DOWNLOAD_MONITOR_TARGET_NOT_ALLOWED",
            "qBittorrent monitoring target is outside the allowed save paths",
            status_code=409,
        )


def _pt_error_code(site_id: str, *, avistaz: str, generic: str) -> str:
    return avistaz if site_id == "avistaz" else generic


def _normalized_title(value: str) -> str:
    return " ".join(value.casefold().split())


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


async def _database_now(session: AsyncSession) -> datetime:
    value = await session.scalar(select(func.now()))
    return value if isinstance(value, datetime) else datetime.now(UTC)
