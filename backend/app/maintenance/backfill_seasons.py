"""Fill in per-season airing status for series identified before it was tracked.

``media_seasons`` tells automation which seasons have finished airing and which
the library is still missing.  Without it a season pack that is already
downloaded keeps being re-selected, and the seasons that are actually missing
never get a turn.

Run it against the series automation actually touches::

    docker compose exec -T api python -m app.maintenance.backfill_seasons --dry-run
    docker compose exec -T api python -m app.maintenance.backfill_seasons

This one is more expensive than the genre backfill: TMDB serves one request per
season, so a seventeen-season show costs eighteen requests.  Progress is
printed as it goes and the work is committed in batches, so an interrupted run
loses at most the current batch.
"""

from __future__ import annotations

import argparse
import asyncio
from collections.abc import Sequence

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.metadata.tmdb import TmdbProvider
from app.db.session import SessionFactory
from app.errors import AppError
from app.models.enums import MediaType
from app.simple.integrations import _sync_media_seasons, build_tmdb, close_adapter
from app.simple.models import AutomationJob, LibraryMediaItem, MediaSeason


async def _targets(
    session: AsyncSession, *, everything: bool, limit: int | None
) -> Sequence[LibraryMediaItem]:
    # Only series have seasons, and only items with a TMDB id can be looked up.
    statement = select(LibraryMediaItem).where(
        LibraryMediaItem.tmdb_id.is_not(None),
        LibraryMediaItem.media_type == MediaType.TV,
    )
    if not everything:
        statement = statement.where(
            LibraryMediaItem.id.in_(select(AutomationJob.media_id).distinct())
        )
    statement = statement.order_by(LibraryMediaItem.title)
    if limit is not None:
        statement = statement.limit(limit)
    return list(await session.scalars(statement))


async def backfill(*, everything: bool, limit: int | None, dry_run: bool) -> int:
    async with SessionFactory() as session:
        targets = await _targets(session, everything=everything, limit=limit)
        have_seasons = set(
            await session.scalars(
                select(MediaSeason.media_id).group_by(MediaSeason.media_id).having(func.count() > 0)
            )
        )
        missing = [item for item in targets if item.id not in have_seasons]
        print(f"候选剧集 {len(targets)}，其中还没有季信息的 {len(missing)}")
        if dry_run:
            for item in missing[:20]:
                print(f"  会处理: {item.title} (TMDB {item.tmdb_id})")
            if len(missing) > 20:
                print(f"  ... 以及另外 {len(missing) - 20} 部")
            print("提示：每部剧要请求 1 + 季数 次 TMDB，实际耗时取决于季数。")
            return 0
        if not missing:
            print("没有需要补的剧集。")
            return 0

        provider = build_tmdb()
        updated = 0
        failed = 0
        requests = 0
        try:
            assert isinstance(provider, TmdbProvider)
            for index, item in enumerate(missing, start=1):
                assert item.tmdb_id is not None
                try:
                    details = await provider._details(
                        MediaType.TV, item.tmdb_id, "zh-CN", append=False
                    )
                    requests += 1
                    season_count = details.number_of_seasons or 0
                    if season_count <= 0:
                        print(f"  [{index}/{len(missing)}] {item.title}: TMDB 未报告季数，跳过")
                        continue
                    _, summaries = await provider._episode_matrix(item.tmdb_id, season_count)
                    requests += season_count
                except AppError as exc:
                    failed += 1
                    print(f"  [{index}/{len(missing)}] 跳过 {item.title}: {exc.message}")
                    continue
                await _sync_media_seasons(session, item.id, summaries)
                complete = sum(1 for summary in summaries if summary.is_complete)
                updated += 1
                print(
                    f"  [{index}/{len(missing)}] {item.title[:24]}: "
                    f"{len(summaries)} 季，其中已播完 {complete} 季"
                )
                if index % 10 == 0:
                    await session.commit()
            await session.commit()
        finally:
            await close_adapter(provider)

        print(f"完成：处理 {updated} 部，失败 {failed} 部，共 {requests} 次 TMDB 请求")
        return updated


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--all",
        dest="everything",
        action="store_true",
        help="处理全部剧集，而不只是自动化碰过的",
    )
    parser.add_argument("--limit", type=int, default=None, help="最多处理多少部")
    parser.add_argument("--dry-run", action="store_true", help="只列出会处理哪些，不请求 TMDB")
    args = parser.parse_args(argv)

    asyncio.run(backfill(everything=args.everything, limit=args.limit, dry_run=args.dry_run))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
