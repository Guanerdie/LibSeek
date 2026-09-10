"""The stored region tags that let the library filter by region in SQL."""

from __future__ import annotations

import pytest

from app.models.enums import MediaType
from app.simple.models import LibraryMediaItem
from app.simple.regions import NextFindRegion, region_tag, region_tags


def test_region_tags_delimit_every_region_for_a_like_filter() -> None:
    assert region_tags(["JP", "US"], "ja") == "|欧美|日本|"
    assert region_tags([], None) == ""
    assert region_tag(NextFindRegion.JAPAN) in region_tags(["JP"], "ja")
    # "港台" must not match inside a longer name, hence the delimiters.
    assert region_tag(NextFindRegion.HONG_KONG_TAIWAN) not in region_tags(["CN"], "zh")


@pytest.mark.asyncio
async def test_region_tags_follow_country_and_language_changes(session_factory) -> None:
    async with session_factory() as session:
        media = LibraryMediaItem(
            source_item_id="region-tags",
            media_type=MediaType.TV,
            title="Region Tags",
            country_codes=["JP", "US"],
            original_language="ja",
        )
        session.add(media)
        await session.commit()
        assert media.region_tags == "|欧美|日本|"

        media.country_codes = ["KR"]
        await session.commit()
        assert media.region_tags == "|韩国|"

        media.country_codes = []
        media.original_language = None
        await session.commit()
        assert media.region_tags == ""
