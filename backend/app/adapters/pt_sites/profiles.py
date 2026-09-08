from __future__ import annotations

import ipaddress
import re
import string
from typing import Self
from urllib.parse import urljoin, urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models.enums import MediaType
from app.schemas.adapters import SiteId

_CATEGORY_VALUE = re.compile(r"^[A-Za-z0-9_-]{1,40}$")
_QUERY_NAME = re.compile(r"^[A-Za-z0-9_.\[\]-]{1,80}$")
_DNS_LABEL = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
_LOCAL_HOST_SUFFIXES = (".localhost", ".local", ".internal", ".home.arpa")


def normalize_public_dns_host(value: str) -> str:
    """Return one unambiguous public DNS name, never an IP or local namespace."""

    host = value.strip().casefold()
    if not host or host != value.casefold() or host.endswith("."):
        raise ValueError("host must be a normalized DNS name")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        raise ValueError("IP literals are not allowed")
    labels = host.split(".")
    if (
        len(host) > 253
        or len(labels) < 2
        or all(label.isdigit() for label in labels)
        or any(_DNS_LABEL.fullmatch(label) is None for label in labels)
        or host == "localhost"
        or host.endswith(_LOCAL_HOST_SUFFIXES)
    ):
        raise ValueError("host must be a public DNS name")
    return host


def url_origin(value: str) -> tuple[str, str, int]:
    parsed = urlsplit(value)
    return (
        parsed.scheme.casefold(),
        (parsed.hostname or "").casefold(),
        parsed.port or 443,
    )


class NexusPhpQueryRules(BaseModel):
    """Public field names used to build a tracker search query."""

    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    text: str = "search"
    category: str = "cat"
    page: str | None = "page"
    tmdb: str | None = None
    imdb: str | None = None
    tvdb: str | None = None

    @field_validator("text", "category", "page", "tmdb", "imdb", "tvdb")
    @classmethod
    def validate_query_name(cls, value: str | None) -> str | None:
        if value is not None and _QUERY_NAME.fullmatch(value) is None:
            raise ValueError("query field name has an invalid format")
        return value


class NexusPhpSelectors(BaseModel):
    """CSS selectors for a single, explicitly configured NexusPHP layout."""

    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    row: str = Field(min_length=1, max_length=240)
    details_link: str = Field(min_length=1, max_length=240)
    title: str = Field(min_length=1, max_length=240)
    size: str = Field(min_length=1, max_length=240)
    seeders: str = Field(min_length=1, max_length=240)
    leechers: str | None = Field(default=None, min_length=1, max_length=240)
    completed: str | None = Field(default=None, min_length=1, max_length=240)
    category: str | None = Field(default=None, min_length=1, max_length=240)
    published_at: str | None = Field(default=None, min_length=1, max_length=240)
    discount: str | None = Field(default=None, min_length=1, max_length=240)
    title_attribute: str | None = Field(default=None, min_length=1, max_length=80)


class NexusPhpPageStateSelectors(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    login: str = "form[action*='login']"
    captcha: str = "input[name*='captcha'], img[src*='captcha']"
    challenge: str = "#challenge-form, .cf-challenge, [data-sitekey]"


class NexusPhpSiteProfile(BaseModel):
    """Secret-free declaration for one known NexusPHP-compatible site layout.

    A profile is deliberately disabled by default. Enabling it only declares that
    its fixture contract was reviewed; runtime credentials and live-network gates
    remain separate adapter constructor inputs.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    site_id: SiteId
    display_name: str = Field(min_length=1, max_length=100)
    enabled: bool = False
    base_url: str
    search_path: str = "/torrents.php"
    download_path: str = "/download.php?id={torrent_id}"
    # Optional: only NexusPHP sites that expose a passkey feed can be polled.
    rss_path: str | None = Field(default=None, min_length=1, max_length=240)
    category_mapping: dict[MediaType, tuple[str, ...]]
    category_media_types: dict[str, MediaType] = Field(default_factory=dict)
    selectors: NexusPhpSelectors
    page_states: NexusPhpPageStateSelectors = Field(default_factory=NexusPhpPageStateSelectors)
    query: NexusPhpQueryRules = Field(default_factory=NexusPhpQueryRules)
    torrent_id_query_parameter: str = "id"
    discount_text_factors: dict[str, float] = Field(default_factory=dict)

    @field_validator("base_url")
    @classmethod
    def validate_base_url(cls, value: str) -> str:
        parsed = urlsplit(value)
        if (
            parsed.scheme.casefold() != "https"
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or parsed.path not in {"", "/"}
        ):
            raise ValueError("base_url must be a credential-free HTTPS origin")
        try:
            _ = parsed.port
        except ValueError as exc:
            raise ValueError("base_url has an invalid port") from exc
        normalize_public_dns_host(parsed.hostname)
        return value.rstrip("/")

    @field_validator("search_path", "download_path")
    @classmethod
    def validate_same_origin_path(cls, value: str) -> str:
        if (
            not value.startswith("/")
            or value.startswith("//")
            or "\\" in value
            or any(ord(character) < 32 for character in value)
        ):
            raise ValueError("site path must be an absolute same-origin path")
        parsed = urlsplit(value)
        if parsed.scheme or parsed.netloc or parsed.fragment:
            raise ValueError("site path must not select another origin or fragment")
        return value

    @field_validator("torrent_id_query_parameter")
    @classmethod
    def validate_torrent_id_query_parameter(cls, value: str) -> str:
        if _QUERY_NAME.fullmatch(value) is None:
            raise ValueError("torrent id query parameter has an invalid format")
        return value

    @field_validator("category_mapping")
    @classmethod
    def validate_category_mapping(
        cls, value: dict[MediaType, tuple[str, ...]]
    ) -> dict[MediaType, tuple[str, ...]]:
        if not value:
            raise ValueError("at least one media category mapping is required")
        for categories in value.values():
            invalid = any(_CATEGORY_VALUE.fullmatch(item) is None for item in categories)
            if not categories or invalid:
                raise ValueError("category mapping contains an invalid value")
        return value

    @field_validator("category_media_types")
    @classmethod
    def validate_category_media_types(
        cls, value: dict[str, MediaType]
    ) -> dict[str, MediaType]:
        if any(_CATEGORY_VALUE.fullmatch(item) is None for item in value):
            raise ValueError("category media type mapping contains an invalid value")
        return value

    @field_validator("discount_text_factors")
    @classmethod
    def validate_discount_factors(cls, value: dict[str, float]) -> dict[str, float]:
        if any(not key.strip() or not 0 <= factor <= 1 for key, factor in value.items()):
            raise ValueError(
                "discount factors require non-empty labels and values from zero to one"
            )
        return value

    @model_validator(mode="after")
    def validate_paths_and_template(self) -> Self:
        formatter = string.Formatter()
        try:
            parsed_template = list(formatter.parse(self.download_path))
        except ValueError as exc:
            raise ValueError("download_path has an invalid template") from exc
        if any(format_spec or conversion for _, _, format_spec, conversion in parsed_template):
            raise ValueError("download_path must not use format specs or conversions")
        fields = {
            field_name
            for _, field_name, _, _ in parsed_template
            if field_name is not None
        }
        if "torrent_id" not in fields or not fields.issubset({"torrent_id", "passkey"}):
            raise ValueError(
                "download_path must contain {torrent_id} and may only also contain {passkey}"
            )
        base_origin = url_origin(self.base_url)
        for path in (self.search_path, self.download_path):
            rendered = path.replace("{torrent_id}", "1").replace("{passkey}", "placeholder")
            if url_origin(urljoin(f"{self.base_url}/", rendered.lstrip("/"))) != base_origin:
                raise ValueError("site paths must resolve to the profile base origin")
        return self
