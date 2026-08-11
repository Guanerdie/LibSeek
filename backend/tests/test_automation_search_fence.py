from __future__ import annotations

from collections.abc import Awaitable, Callable

import pytest

from app.core.config import Settings
from app.errors import AppError
from app.models.entities import AutomationDecision, Job
from app.models.enums import (
    AutomationMode,
    AutomationStage,
    DecisionOutcome,
    JobStatus,
)
from app.services import automation
from app.services.automation import (
    build_automation_decision,
    require_automatic_torrent_search_current,
)
from app.services.automation_policy import _new_default_policy


class _DecisionSession:
    def __init__(self, decision: AutomationDecision) -> None:
        self.decision = decision

    async def get(self, _model: object, identifier: str) -> AutomationDecision | None:
        return self.decision if identifier == self.decision.id else None


def _guard_fixture(
    *,
    job_search_run_id: str = "search-run-one",
    job_site_id: str = "avistaz",
) -> tuple[_DecisionSession, Job, Settings, Callable[..., Awaitable[object]]]:
    head, revision = _new_default_policy()
    revision.torrent_selection_mode = AutomationMode.AUTO_IF_ELIGIBLE
    decision = build_automation_decision(
        revision,
        stage=AutomationStage.TORRENT_SELECTION,
        action="QUEUE_TORRENT_SEARCH",
        outcome=DecisionOutcome.ACTION_CREATED,
        media_item_id="media-one",
        reason_codes=("IDENTITY_CONFIRMED",),
        evidence={
            "trigger": "IDENTITY_CONFIRMED",
            "search_run_id": "search-run-one",
            "site_id": "avistaz",
        },
    )
    job = Job(
        id="job-one",
        job_type="TORRENT_SEARCH:avistaz:media-one",
        status=JobStatus.PENDING,
        payload={
            "media_id": "media-one",
            "search_run_id": job_search_run_id,
            "site_id": job_site_id,
            "automation_policy_revision_id": revision.id,
            "automation_decision_id": decision.id,
        },
    )

    async def current_policy(*_args: object, **_kwargs: object) -> object:
        return head, revision

    return (
        _DecisionSession(decision),
        job,
        Settings(enable_automation_engine=True),
        current_policy,
    )


@pytest.mark.asyncio
async def test_automatic_torrent_search_accepts_exact_run_and_site_binding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session, job, settings, current_policy = _guard_fixture()
    monkeypatch.setattr(automation, "get_current_policy", current_policy)

    await require_automatic_torrent_search_current(
        session,  # type: ignore[arg-type]
        job=job,
        settings=settings,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("search_run_id", "site_id"),
    (
        ("search-run-two", "avistaz"),
        ("search-run-one", "fixture-nexus"),
    ),
)
async def test_automatic_torrent_search_rejects_decision_subject_rebinding(
    monkeypatch: pytest.MonkeyPatch,
    search_run_id: str,
    site_id: str,
) -> None:
    session, job, settings, current_policy = _guard_fixture(
        job_search_run_id=search_run_id,
        job_site_id=site_id,
    )
    monkeypatch.setattr(automation, "get_current_policy", current_policy)

    with pytest.raises(AppError) as caught:
        await require_automatic_torrent_search_current(
            session,  # type: ignore[arg-type]
            job=job,
            settings=settings,
        )

    assert caught.value.error_code == "AUTOMATION_TORRENT_SEARCH_BINDING_INVALID"
