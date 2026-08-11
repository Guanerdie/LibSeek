from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import AutomationMode, AutomationStage, DecisionOutcome


class AutomationPolicyRevisionCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    base_revision_no: int = Field(ge=1)
    identity_mode: AutomationMode
    torrent_selection_mode: AutomationMode
    approval_mode: AutomationMode
    execution_mode: AutomationMode
    identity_min_score: float = Field(default=0.5, ge=0, le=1)
    identity_min_margin: float = Field(default=0.1, ge=0, le=1)
    torrent_min_score: float = Field(default=0.75, ge=0, le=1)
    torrent_min_margin: float = Field(default=0.1, ge=0, le=1)
    torrent_min_seeders: int = Field(default=1, ge=1, le=100_000)
    acknowledges_hnr: bool = False
    acknowledges_seeding: bool = False
    acknowledges_plan_only: bool = False
    acknowledges_add_paused_only: bool = False


class AutomationPolicyRevisionResponse(BaseModel):
    id: str
    revision_no: int
    identity_mode: AutomationMode
    torrent_selection_mode: AutomationMode
    approval_mode: AutomationMode
    execution_mode: AutomationMode
    identity_min_score: float
    identity_min_margin: float
    torrent_min_score: float
    torrent_min_margin: float
    torrent_min_seeders: int
    acknowledges_hnr: bool
    acknowledges_seeding: bool
    acknowledges_plan_only: bool
    acknowledges_add_paused_only: bool
    policy_hash: str
    previous_policy_hash: str | None
    effective_from: datetime
    created_by: str
    created_at: datetime


class AutomationPolicyCurrentResponse(BaseModel):
    scope: str
    version: int
    engine_enabled: bool
    revision: AutomationPolicyRevisionResponse


class AutomationDecisionResponse(BaseModel):
    id: str
    policy_revision_id: str
    stage: AutomationStage
    action: str
    outcome: DecisionOutcome
    media_item_id: str
    metadata_match_id: str | None
    torrent_candidate_id: str | None
    approval_request_id: str | None
    download_execution_id: str | None
    reason_codes: list[str]
    evidence_snapshot: dict[str, Any]
    evidence_hash: str
    actor: str
    created_at: datetime
