from __future__ import annotations

from collections.abc import Iterable
from enum import StrEnum


class NextFindRegion(StrEnum):
    WESTERN = "欧美"
    MAINLAND = "大陆"
    HONG_KONG_TAIWAN = "港台"
    KOREA = "韩国"
    JAPAN = "日本"
    ASIA_PACIFIC = "亚太"


NEXTFIND_REGION_ORDER = (
    NextFindRegion.WESTERN,
    NextFindRegion.MAINLAND,
    NextFindRegion.HONG_KONG_TAIWAN,
    NextFindRegion.KOREA,
    NextFindRegion.JAPAN,
    NextFindRegion.ASIA_PACIFIC,
)

_COUNTRY_CODES = {
    NextFindRegion.WESTERN: frozenset(
        {"US", "GB", "FR", "DE", "IT", "ES", "CA", "AU", "RU", "BR", "MX", "AR"}
    ),
    NextFindRegion.MAINLAND: frozenset({"CN"}),
    NextFindRegion.HONG_KONG_TAIWAN: frozenset({"HK", "TW", "MO"}),
    NextFindRegion.KOREA: frozenset({"KR"}),
    NextFindRegion.JAPAN: frozenset({"JP"}),
    NextFindRegion.ASIA_PACIFIC: frozenset({"IN", "TH", "SG", "MY"}),
}

_LANGUAGE_CODES = {
    NextFindRegion.WESTERN: frozenset(
        {"en", "fr", "de", "es", "it", "pt", "ru", "ar", "tr"}
    ),
    NextFindRegion.MAINLAND: frozenset({"zh"}),
    NextFindRegion.HONG_KONG_TAIWAN: frozenset({"zh"}),
    NextFindRegion.KOREA: frozenset({"ko"}),
    NextFindRegion.JAPAN: frozenset({"ja"}),
    NextFindRegion.ASIA_PACIFIC: frozenset({"th", "vi", "id", "hi", "tl"}),
}


def nextfind_regions(
    country_codes: Iterable[str] | None,
    original_language: str | None,
) -> list[NextFindRegion]:
    countries = {
        code.strip().upper()
        for code in country_codes or ()
        if isinstance(code, str) and code.strip()
    }
    country_matches = [
        region
        for region in NEXTFIND_REGION_ORDER
        if countries & _COUNTRY_CODES[region]
    ]
    if country_matches:
        return country_matches

    language = original_language.strip().lower() if original_language else ""
    return [
        region
        for region in NEXTFIND_REGION_ORDER
        if language in _LANGUAGE_CODES[region]
    ]
