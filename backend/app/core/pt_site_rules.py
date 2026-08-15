from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

AVISTAZ_DEFAULT_HNR_DAYS = 7


@dataclass(frozen=True)
class EffectiveHnrRule:
    applies: bool | None
    source: Literal["SITE_DEFAULT", "CANDIDATE_METADATA", "UNKNOWN"]
    minimum_seeding_days: int | None = None

    @property
    def known(self) -> bool:
        return self.applies is not None


def effective_hnr_rule(site_id: str, hit_and_run: bool | None) -> EffectiveHnrRule:
    if site_id.casefold() == "avistaz":
        return EffectiveHnrRule(
            applies=True,
            source="SITE_DEFAULT",
            minimum_seeding_days=AVISTAZ_DEFAULT_HNR_DAYS,
        )
    if hit_and_run is None:
        return EffectiveHnrRule(applies=None, source="UNKNOWN")
    return EffectiveHnrRule(applies=hit_and_run, source="CANDIDATE_METADATA")


def hnr_acknowledgement_required(site_id: str) -> bool:
    return site_id.casefold() != "avistaz"
