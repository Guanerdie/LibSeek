from __future__ import annotations

from app.schemas.adapters import normalize_language_code
from app.simple.regions import NextFindRegion, nextfind_regions


def test_nextfind_regions_follow_country_groups_and_visible_order() -> None:
    assert nextfind_regions(["JP", "US"], "ja") == [
        NextFindRegion.WESTERN,
        NextFindRegion.JAPAN,
    ]
    assert nextfind_regions(["CN", "TW"], "zh") == [
        NextFindRegion.MAINLAND,
        NextFindRegion.HONG_KONG_TAIWAN,
    ]


def test_nextfind_regions_use_language_when_country_has_no_known_group() -> None:
    assert nextfind_regions(["NZ"], "en") == [NextFindRegion.WESTERN]
    assert nextfind_regions(["PH"], "tl") == [NextFindRegion.ASIA_PACIFIC]
    assert nextfind_regions([], "zh") == [
        NextFindRegion.MAINLAND,
        NextFindRegion.HONG_KONG_TAIWAN,
    ]


def test_nextfind_regions_prefer_known_countries_over_language_fallback() -> None:
    assert nextfind_regions(["HK"], "zh") == [NextFindRegion.HONG_KONG_TAIWAN]
    assert nextfind_regions(["KR"], "en") == [NextFindRegion.KOREA]
    assert nextfind_regions(["XX"], None) == []


def test_nextfind_language_code_uses_its_two_letter_format() -> None:
    assert normalize_language_code(" JA ") == "ja"
    assert normalize_language_code("ja-JP") is None
