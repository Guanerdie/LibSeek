"""Recompute stored candidate scores with the current quality formula.

Scores are written when a search runs, so rows from before the formula changed
still carry the old identity-plus-quality number.  Nothing reads them for
selection -- automation only looks at the candidates of the search it just ran
-- but the score histogram behind the "minimum score" setting reads thirty days
of rows, and mixing two scales makes that chart lie.

Run it against everything, or a window::

    docker compose exec -T api python -m app.maintenance.rescore_candidates --dry-run
    docker compose exec -T api python -m app.maintenance.rescore_candidates --days 30

Pure local arithmetic: the stored resolution, size, seeders and download factor
are all it needs, so no site or TMDB request is made.
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.time import utc_now
from app.db.session import SessionFactory
from app.services.quality import QualityWeights, score_release_quality
from app.simple.models import AutomationPolicy, ReleaseCandidate, ReleaseSearch


async def _weights(session: AsyncSession) -> QualityWeights:
    policy = await session.get(AutomationPolicy, "default")
    if policy is None:
        return QualityWeights()
    return QualityWeights(
        resolution=policy.weight_resolution,
        size=policy.weight_size,
        source=policy.weight_source,
        seeders=policy.weight_seeders,
        promotion=policy.weight_promotion,
        seeder_floor=policy.seeder_floor,
    )


def _rescore(candidate: ReleaseCandidate, weights: QualityWeights) -> float:
    # The preference flags were decided at search time against the settings of
    # the day and recorded in `reasons`; re-deriving them from today's settings
    # would quietly rewrite history.
    reasons = set(candidate.reasons or [])
    return score_release_quality(
        resolution=candidate.resolution,
        source=candidate.source,
        size_bytes=candidate.size_bytes,
        seeders=candidate.seeders,
        download_factor=candidate.download_factor,
        preferred_resolution="PREFERRED_RESOLUTION" in reasons,
        preferred_source="PREFERRED_SOURCE" in reasons,
        preferred_audio="PREFERRED_AUDIO" in reasons,
        preferred_subtitle="PREFERRED_SUBTITLE" in reasons,
        weights=weights,
    ).score


async def rescore(*, days: int | None, dry_run: bool) -> int:
    async with SessionFactory() as session:
        weights = await _weights(session)
        statement = select(ReleaseCandidate)
        if days is not None:
            since = utc_now() - timedelta(days=days)
            statement = statement.join(
                ReleaseSearch, ReleaseSearch.id == ReleaseCandidate.search_id
            ).where(ReleaseSearch.created_at >= since)
        rows = list(await session.scalars(statement))
        if not rows:
            print("没有需要重算的候选。")
            return 0

        changed = 0
        biggest_move = 0.0
        for index, candidate in enumerate(rows, start=1):
            fresh = _rescore(candidate, weights)
            move = abs(fresh - (candidate.score or 0))
            if move > 1e-6:
                changed += 1
                biggest_move = max(biggest_move, move)
                if not dry_run:
                    candidate.score = fresh
            if not dry_run and index % 2000 == 0:
                await session.commit()
                print(f"  已处理 {index}/{len(rows)}")
        if dry_run:
            print(
                f"候选 {len(rows)} 个，其中 {changed} 个分数会变，"
                f"最大变动 {biggest_move:.3f}（未写入）"
            )
            return 0
        await session.commit()
        print(f"完成：{len(rows)} 个候选，更新 {changed} 个，最大变动 {biggest_move:.3f}")
        return changed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--days",
        type=int,
        default=None,
        help="只重算最近多少天的搜索（默认全部）",
    )
    parser.add_argument("--dry-run", action="store_true", help="只统计会变多少，不写入")
    args = parser.parse_args(argv)

    asyncio.run(rescore(days=args.days, dry_run=args.dry_run))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
