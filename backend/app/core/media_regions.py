from __future__ import annotations

from enum import StrEnum


class MediaRegion(StrEnum):
    WESTERN = "western"
    MAINLAND = "mainland"
    HONG_KONG_TAIWAN = "hong-kong-taiwan"
    KOREA = "korea"
    JAPAN = "japan"
    ASIA_PACIFIC = "asia-pacific"


NEXTFIND_REGION_COUNTRY_CODES: dict[MediaRegion, tuple[str, ...]] = {
    MediaRegion.WESTERN: (
        "US",
        "GB",
        "FR",
        "DE",
        "IT",
        "ES",
        "CA",
        "AU",
        "RU",
        "BR",
        "MX",
        "AR",
    ),
    MediaRegion.MAINLAND: ("CN",),
    MediaRegion.HONG_KONG_TAIWAN: ("HK", "TW", "MO"),
    MediaRegion.KOREA: ("KR",),
    MediaRegion.JAPAN: ("JP",),
    MediaRegion.ASIA_PACIFIC: (
        "IN",
        "TH",
        "SG",
        "MY",
        "PH",
        "VN",
        "ID",
        "MM",
    ),
}


def country_codes_for_region(region: MediaRegion) -> tuple[str, ...]:
    return NEXTFIND_REGION_COUNTRY_CODES[region]
