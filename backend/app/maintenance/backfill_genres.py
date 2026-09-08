"""Fill in TMDB genres and airing status for media identified before they existed.

Telling a weekly variety show from a drama series depends on the TMDB genre,
and the genre is only written when a media item is identified.  A library
populated before the column existed therefore has none, and every show looks
like a drama -- which means the variety rules never fire.

Run it against the items that automation actually touches::

    docker compose exec -T api python -m app.maintenance.backfill_genres --dry-run
    docker compose exec -T api python -m app.maintenance.backfill_genres

Only ``genre_ids`` and ``tmdb_status`` are written.  In particular the media
state is left exactly as it is: re-running a full identify would reset an item
that is mid-download back to READY.
"""

from __future__ import annotations

import argparse
import asyncio
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.metadata.tmdb import TmdbProvider
from app.db.session import SessionFactory
from app.errors import AppError
from app.simple.integrations import build_tmdb, close_adapter
from app.simple.models import AutomationJob, LibraryMediaItem


async def _targets(
    session: AsyncSession, *, everything: bool, limit: int | None
) -> Sequence[LibraryMediaItem]:
    statement = select(LibraryMediaItem).where(LibraryMediaItem.tmdb_id.is_not(None))
    if not everything:
        # The library can hold tens of thousands of rows that automation has
        # never looked at; fetching genres for all of them would be tens of
        # thousands of TMDB requests for no benefit.
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
        missing = [item for item in targets if not item.genre_ids]
        print(f"候选条目 {len(targets)}，其中还没有分类的 {len(missing)}")
        if dry_run:
            for item in missing[:20]:
                print(f"  会更新: {item.title} (TMDB {item.tmdb_id})")
            if len(missing) > 20:
                print(f"  ... 以及另外 {len(missing) - 20} 条")
            return 0
        if not missing:
            print("没有需要补的条目。")
            return 0

        provider = build_tmdb()
        updated = 0
        failed = 0
        try:
            assert isinstance(provider, TmdbProvider)
            for index, item in enumerate(missing, start=1):
                assert item.tmdb_id is not None
                try:
                    # Only the base details call: ``get_by_tmdb_id`` would also
                    # walk every season, which is dozens of requests for a
                    # long-running show and nothing we need here.
                    details = await provider._details(
                        item.media_type, item.tmdb_id, "zh-CN", append=False
                    )
                except AppError as exc:
                    failed += 1
                    print(f"  [{index}/{len(missing)}] 跳过 {item.title}: {exc.message}")
                    continue
                item.genre_ids = [genre.id for genre in details.genres]
                item.tmdb_status = details.status
                updated += 1
                if index % 20 == 0:
                    await session.commit()
                    print(f"  已处理 {index}/{len(missing)}")
            await session.commit()
        finally:
            await close_adapter(provider)

        print(f"完成：更新 {updated} 条，失败 {failed} 条")
        return updated


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--all",
        dest="everything",
        action="store_true",
        help="处理整个媒体库，而不只是自动化碰过的条目",
    )
    parser.add_argument("--limit", type=int, default=None, help="最多处理多少条")
    parser.add_argument("--dry-run", action="store_true", help="只列出会更新哪些，不实际请求 TMDB")
    args = parser.parse_args(argv)

    asyncio.run(
        backfill(everything=args.everything, limit=args.limit, dry_run=args.dry_run)
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
