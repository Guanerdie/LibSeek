from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Literal, TextIO
from urllib.parse import urlparse

import httpx
from pydantic import BaseModel, ConfigDict, Field

from app.adapters.media_sources.nextfind import NextFindAdapter
from app.core.config import Settings, get_settings
from app.core.security import validate_external_url
from app.errors import AppError

JsonValueType = Literal["null", "boolean", "integer", "number", "string", "array", "object"]
ResponseFormat = Literal["json", "ndjson"]

_TYPE_ORDER: tuple[JsonValueType, ...] = (
    "null",
    "boolean",
    "integer",
    "number",
    "string",
    "array",
    "object",
)
_SAFE_FIELD_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,63}$", re.ASCII)
_SECRET_LIKE_FRAGMENTS = (
    "password",
    "passwd",
    "secret",
    "token",
    "cookie",
    "authorization",
    "credential",
    "passkey",
    "apikey",
    "accesskey",
    "privatekey",
    "session",
    "csrf",
    "username",
    "downloadurl",
    "announce",
    "tracker",
)
_SECRET_LIKE_EXACT_NAMES = frozenset({"pwd", "jwt", "sid", "pid", "email"})

_ENVELOPE_FIELD_PATHS = (
    "$.data",
    "$.items",
    "$.results",
    "$.result",
    "$.records",
    "$.rows",
)
_PAGINATION_FIELD_NAMES = (
    "next_cursor",
    "nextCursor",
    "cursor",
    "next_page",
    "nextPage",
    "page",
    "page_number",
    "pageNumber",
    "page_size",
    "pageSize",
    "per_page",
    "perPage",
    "limit",
    "offset",
    "total",
    "total_count",
    "totalCount",
    "total_pages",
    "totalPages",
    "has_next",
    "hasNext",
    "has_more",
    "hasMore",
)
_PAGINATION_FIELD_PATHS = tuple(
    f"{prefix}.{name}"
    for prefix in ("$", "$.meta", "$.pagination", "$.page_info", "$.pageInfo")
    for name in _PAGINATION_FIELD_NAMES
)
_ALLOWED_SCHEMA_FIELD_NAMES = frozenset(
    {
        "data",
        "items",
        "results",
        "result",
        "records",
        "rows",
        "list",
        "media",
        "payload",
        "meta",
        "metadata",
        "pagination",
        "page_info",
        "pageInfo",
        "success",
        "ok",
        "code",
        "message",
        "details",
        "available",
        "library",
        "local_library",
        "localLibrary",
        "id",
        "source_id",
        "sourceId",
        "source_item_id",
        "sourceItemId",
        "tmdb_id",
        "tmdbId",
        "themoviedb_id",
        "imdb_id",
        "imdbId",
        "tvdb_id",
        "tvdbId",
        "douban_id",
        "doubanId",
        "bangumi_id",
        "bangumiId",
        "external_ids",
        "externalIds",
        "type",
        "media_type",
        "mediaType",
        "raw_type",
        "status",
        "title",
        "name",
        "original_title",
        "originalTitle",
        "original_name",
        "originalName",
        "english_title",
        "englishTitle",
        "english_name",
        "englishName",
        "title_en",
        "name_en",
        "aliases",
        "alternative_titles",
        "alternativeTitles",
        "year",
        "release_year",
        "releaseYear",
        "release_date",
        "releaseDate",
        "first_air_date",
        "firstAirDate",
        "created_at",
        "createdAt",
        "updated_at",
        "updatedAt",
        "poster",
        "poster_path",
        "posterPath",
        "backdrop",
        "backdrop_path",
        "backdropPath",
        "images",
        "season",
        "seasons",
        "season_number",
        "seasonNumber",
        "season_count",
        "seasonCount",
        "episode",
        "episodes",
        "episode_number",
        "episodeNumber",
        "episode_count",
        "episodeCount",
        "local_episodes",
        "localEpisodes",
        "existing_episodes",
        "existingEpisodes",
        "local_episode_matrix",
        "localEpisodeMatrix",
        "existing_episode_matrix",
        "existingEpisodeMatrix",
        "total_episodes",
        "totalEpisodes",
        "aired_episodes",
        "airedEpisodes",
        "missing_episodes",
        "missingEpisodes",
        "original_language",
        "originalLanguage",
        "languages",
        "country",
        "countries",
        "genres",
        "runtime",
        "overview",
        "description",
        *_PAGINATION_FIELD_NAMES,
    }
)
_PRIORITY_FIELD_NAMES = frozenset(
    {
        "data",
        "items",
        "results",
        "result",
        "meta",
        "pagination",
        "next_cursor",
        "cursor",
        "next_page",
        "page",
        "page_size",
        "per_page",
        "total",
        "total_pages",
        "has_next",
        "id",
        "source_item_id",
        "tmdb_id",
        "imdb_id",
        "tvdb_id",
        "type",
        "media_type",
        "title",
        "name",
        "original_title",
        "original_name",
        "english_title",
        "year",
        "season",
        "season_number",
        "episode",
        "episode_number",
        "local_episodes",
        "total_episodes",
        "aired_episodes",
        "missing_episodes",
    }
)


class ProbeLimits(BaseModel):
    model_config = ConfigDict(frozen=True)

    max_response_bytes: int = Field(default=1024 * 1024, ge=1, le=10 * 1024 * 1024)
    max_ndjson_line_bytes: int = Field(default=256 * 1024, ge=1, le=1024 * 1024)
    max_ndjson_lines: int = Field(default=500, ge=1, le=5000)
    max_field_paths: int = Field(default=512, ge=1, le=4096)
    max_fields_per_object: int = Field(default=128, ge=1, le=1024)
    max_depth: int = Field(default=10, ge=1, le=32)
    max_path_chars: int = Field(default=512, ge=16, le=2048)
    max_array_items: int = Field(default=500, ge=1, le=5000)
    max_nodes: int = Field(default=20_000, ge=1, le=100_000)


class FieldPathSummary(BaseModel):
    model_config = ConfigDict(frozen=True)

    path: str
    types: tuple[JsonValueType, ...]
    occurrences: int = Field(ge=1)
    parent_observations: int = Field(ge=1)
    presence_rate: float = Field(ge=0, le=1)


class KnownFieldPresence(BaseModel):
    model_config = ConfigDict(frozen=True)

    path: str
    present: bool
    occurrences: int = Field(ge=0)


class HiddenFieldSummary(BaseModel):
    model_config = ConfigDict(frozen=True)

    secret_like_name_occurrences: int = Field(ge=0)
    unsafe_name_occurrences: int = Field(ge=0)
    unrecognized_name_occurrences: int = Field(ge=0)


class LimitEventSummary(BaseModel):
    model_config = ConfigDict(frozen=True)

    ndjson_line_limit: int = Field(ge=0)
    field_path_limit: int = Field(ge=0)
    fields_per_object_limit: int = Field(ge=0)
    depth_limit: int = Field(ge=0)
    path_length_limit: int = Field(ge=0)
    array_item_limit: int = Field(ge=0)
    node_limit: int = Field(ge=0)


class NextFindContractSummary(BaseModel):
    model_config = ConfigDict(frozen=True)

    schema_version: Literal["nextfind-contract-probe/v1"] = "nextfind-contract-probe/v1"
    mode: Literal["READ_ONLY_SCHEMA_ONLY"] = "READ_ONLY_SCHEMA_ONLY"
    request_scope: Literal["LOGIN_AND_DISCOVER_FIRST_PAGE_ONLY"] = (
        "LOGIN_AND_DISCOVER_FIRST_PAGE_ONLY"
    )
    response_format: ResponseFormat
    valid_document_count: int = Field(ge=1)
    bad_ndjson_line_count: int = Field(ge=0)
    oversized_ndjson_line_count: int = Field(ge=0)
    blank_ndjson_line_count: int = Field(ge=0)
    root_object_count: int = Field(ge=0)
    root_array_count: int = Field(ge=0)
    field_paths: tuple[FieldPathSummary, ...]
    envelope_fields: tuple[KnownFieldPresence, ...]
    pagination_fields: tuple[KnownFieldPresence, ...]
    hidden_fields: HiddenFieldSummary
    limits: ProbeLimits
    limit_events: LimitEventSummary


@dataclass(slots=True)
class _FieldStats:
    parent_path: str
    occurrences: int = 0
    types: set[JsonValueType] = field(default_factory=set)


@dataclass(slots=True)
class _ParseResult:
    response_format: ResponseFormat
    documents: list[object]
    bad_ndjson_lines: int = 0
    oversized_ndjson_lines: int = 0
    blank_ndjson_lines: int = 0
    ndjson_line_limit_events: int = 0


def _is_secret_like_field_name(name: str) -> bool:
    normalized = re.sub(r"[^a-z0-9]", "", name.casefold())
    return normalized in _SECRET_LIKE_EXACT_NAMES or any(
        fragment in normalized for fragment in _SECRET_LIKE_FRAGMENTS
    )


def _json_type(value: object) -> JsonValueType:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    return "object"


def _decode_json(raw: bytes) -> tuple[bool, object]:
    try:
        payload: object = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return False, None
    return True, payload


def _parse_response(
    *, content_type: str, payload_bytes: bytes, limits: ProbeLimits
) -> _ParseResult:
    if not payload_bytes.strip():
        raise AppError("CONTRACT_PROBE_EMPTY_RESPONSE", "NextFind 契约探针收到空响应")

    if "ndjson" not in content_type.casefold():
        decoded, payload = _decode_json(payload_bytes)
        if decoded:
            return _ParseResult(response_format="json", documents=[payload])

    documents: list[object] = []
    bad_lines = 0
    oversized_lines = 0
    blank_lines = 0
    line_limit_events = 0
    processed_lines = 0
    for raw_line in payload_bytes.splitlines():
        if not raw_line.strip():
            blank_lines += 1
            continue
        if processed_lines >= limits.max_ndjson_lines:
            line_limit_events += 1
            continue
        processed_lines += 1
        if len(raw_line) > limits.max_ndjson_line_bytes:
            bad_lines += 1
            oversized_lines += 1
            continue
        decoded, payload = _decode_json(raw_line)
        if not decoded:
            bad_lines += 1
            continue
        documents.append(payload)

    if not documents:
        raise AppError(
            "CONTRACT_PROBE_NO_VALID_DOCUMENTS",
            "NextFind 契约探针未读取到有效 JSON 文档",
        )
    return _ParseResult(
        response_format="ndjson",
        documents=documents,
        bad_ndjson_lines=bad_lines,
        oversized_ndjson_lines=oversized_lines,
        blank_ndjson_lines=blank_lines,
        ndjson_line_limit_events=line_limit_events,
    )


class _SchemaCollector:
    def __init__(self, limits: ProbeLimits) -> None:
        self.limits = limits
        self.field_stats: dict[str, _FieldStats] = {}
        self.parent_observations: dict[str, int] = {}
        self.secret_like_name_occurrences = 0
        self.unsafe_name_occurrences = 0
        self.unrecognized_name_occurrences = 0
        self.field_path_limit_events = 0
        self.fields_per_object_limit_events = 0
        self.depth_limit_events = 0
        self.path_length_limit_events = 0
        self.array_item_limit_events = 0
        self.node_limit_events = 0
        self.root_object_count = 0
        self.root_array_count = 0
        self._visited_nodes = 0
        self._node_limit_reported = False

    def collect_document(self, payload: object) -> None:
        if isinstance(payload, dict):
            self.root_object_count += 1
        elif isinstance(payload, list):
            self.root_array_count += 1
        self._collect(payload, path="$", depth=0)

    def _collect(self, value: object, *, path: str, depth: int) -> None:
        if depth > self.limits.max_depth:
            self.depth_limit_events += 1
            return
        if self._visited_nodes >= self.limits.max_nodes:
            if not self._node_limit_reported:
                self.node_limit_events += 1
                self._node_limit_reported = True
            return
        self._visited_nodes += 1

        if isinstance(value, dict):
            self.parent_observations[path] = self.parent_observations.get(path, 0) + 1
            safe_items: list[tuple[str, object]] = []
            for key, child in value.items():
                if _is_secret_like_field_name(key):
                    self.secret_like_name_occurrences += 1
                elif _SAFE_FIELD_NAME.fullmatch(key) is None:
                    self.unsafe_name_occurrences += 1
                elif key not in _ALLOWED_SCHEMA_FIELD_NAMES:
                    self.unrecognized_name_occurrences += 1
                else:
                    safe_items.append((key, child))
            safe_items.sort(key=lambda item: (item[0] not in _PRIORITY_FIELD_NAMES, item[0]))
            if len(safe_items) > self.limits.max_fields_per_object:
                self.fields_per_object_limit_events += (
                    len(safe_items) - self.limits.max_fields_per_object
                )
                safe_items = safe_items[: self.limits.max_fields_per_object]
            for key, child in safe_items:
                child_path = f"{path}.{key}"
                if len(child_path) > self.limits.max_path_chars:
                    self.path_length_limit_events += 1
                    continue
                if not self._register_field(child_path, path, child):
                    continue
                if isinstance(child, (dict, list)):
                    self._collect(child, path=child_path, depth=depth + 1)
            return

        if isinstance(value, list):
            item_path = f"{path}[]"
            if len(item_path) > self.limits.max_path_chars:
                self.path_length_limit_events += 1
                return
            if len(value) > self.limits.max_array_items:
                self.array_item_limit_events += len(value) - self.limits.max_array_items
            for child in value[: self.limits.max_array_items]:
                if isinstance(child, (dict, list)):
                    self._collect(child, path=item_path, depth=depth + 1)

    def _register_field(self, path: str, parent_path: str, value: object) -> bool:
        stats = self.field_stats.get(path)
        if stats is None:
            if len(self.field_stats) >= self.limits.max_field_paths:
                self.field_path_limit_events += 1
                return False
            stats = _FieldStats(parent_path=parent_path)
            self.field_stats[path] = stats
        stats.occurrences += 1
        stats.types.add(_json_type(value))
        return True

    def field_summaries(self) -> tuple[FieldPathSummary, ...]:
        summaries: list[FieldPathSummary] = []
        for path, stats in sorted(self.field_stats.items()):
            parent_count = self.parent_observations.get(stats.parent_path, stats.occurrences)
            rate = round(stats.occurrences / parent_count, 6)
            summaries.append(
                FieldPathSummary(
                    path=path,
                    types=tuple(item for item in _TYPE_ORDER if item in stats.types),
                    occurrences=stats.occurrences,
                    parent_observations=parent_count,
                    presence_rate=rate,
                )
            )
        return tuple(summaries)

    def known_field_presence(
        self, paths: tuple[str, ...]
    ) -> tuple[KnownFieldPresence, ...]:
        return tuple(
            KnownFieldPresence(
                path=path,
                present=path in self.field_stats,
                occurrences=self.field_stats[path].occurrences if path in self.field_stats else 0,
            )
            for path in paths
        )


def summarize_contract_response(
    *,
    content_type: str,
    payload_bytes: bytes,
    limits: ProbeLimits | None = None,
) -> NextFindContractSummary:
    selected_limits = limits or ProbeLimits()
    if len(payload_bytes) > selected_limits.max_response_bytes:
        raise AppError(
            "UPSTREAM_RESPONSE_TOO_LARGE",
            "NextFind 响应体超过契约探针安全限制",
        )
    parsed = _parse_response(
        content_type=content_type,
        payload_bytes=payload_bytes,
        limits=selected_limits,
    )
    collector = _SchemaCollector(selected_limits)
    for document in parsed.documents:
        collector.collect_document(document)
    return NextFindContractSummary(
        response_format=parsed.response_format,
        valid_document_count=len(parsed.documents),
        bad_ndjson_line_count=parsed.bad_ndjson_lines,
        oversized_ndjson_line_count=parsed.oversized_ndjson_lines,
        blank_ndjson_line_count=parsed.blank_ndjson_lines,
        root_object_count=collector.root_object_count,
        root_array_count=collector.root_array_count,
        field_paths=collector.field_summaries(),
        envelope_fields=collector.known_field_presence(_ENVELOPE_FIELD_PATHS),
        pagination_fields=collector.known_field_presence(_PAGINATION_FIELD_PATHS),
        hidden_fields=HiddenFieldSummary(
            secret_like_name_occurrences=collector.secret_like_name_occurrences,
            unsafe_name_occurrences=collector.unsafe_name_occurrences,
            unrecognized_name_occurrences=collector.unrecognized_name_occurrences,
        ),
        limits=selected_limits,
        limit_events=LimitEventSummary(
            ndjson_line_limit=parsed.ndjson_line_limit_events,
            field_path_limit=collector.field_path_limit_events,
            fields_per_object_limit=collector.fields_per_object_limit_events,
            depth_limit=collector.depth_limit_events,
            path_length_limit=collector.path_length_limit_events,
            array_item_limit=collector.array_item_limit_events,
            node_limit=collector.node_limit_events,
        ),
    )


async def run_contract_probe(
    *,
    confirm_read_only: bool,
    settings: Settings,
    transport: httpx.AsyncBaseTransport | None = None,
    limits: ProbeLimits | None = None,
) -> NextFindContractSummary:
    if not confirm_read_only:
        raise AppError(
            "READ_ONLY_CONFIRMATION_REQUIRED",
            "必须显式确认后才能运行 NextFind 只读契约探针",
        )
    selected_limits = limits or ProbeLimits()
    if (
        settings.external_max_response_bytes <= 0
        or settings.external_max_ndjson_line_bytes <= 0
        or settings.external_connect_timeout_seconds <= 0
        or settings.external_read_timeout_seconds <= 0
    ):
        raise AppError(
            "CONTRACT_PROBE_CONFIGURATION_INVALID",
            "NextFind 契约探针安全限制配置无效",
        )
    effective_limits = selected_limits.model_copy(
        update={
            "max_response_bytes": min(
                selected_limits.max_response_bytes,
                settings.external_max_response_bytes,
            ),
            "max_ndjson_line_bytes": min(
                selected_limits.max_ndjson_line_bytes,
                settings.external_max_ndjson_line_bytes,
                selected_limits.max_response_bytes,
                settings.external_max_response_bytes,
            ),
        }
    )

    validated_base_url = validate_external_url(
        settings.nextfind_base_url,
        settings.allowed_external_hosts,
    )
    validated_base_url = validate_external_url(
        validated_base_url,
        settings.nextfind_allowed_hosts,
    )
    nextfind_host = urlparse(validated_base_url).hostname
    if nextfind_host is None:
        raise AppError("INVALID_EXTERNAL_URL", "NextFind 地址格式无效", status_code=400)
    try:
        credentials = settings.nextfind_credentials()
    except OSError as exc:
        raise AppError(
            "NEXTFIND_NOT_CONFIGURED",
            "NextFind 尚未配置运行时凭据",
        ) from exc
    if credentials is None:
        raise AppError("NEXTFIND_NOT_CONFIGURED", "NextFind 尚未配置运行时凭据")
    username, password = credentials
    async with NextFindAdapter(
        base_url=validated_base_url,
        allowed_hosts=(nextfind_host.casefold(),),
        username=username,
        password=password,
        transport=transport,
        max_response_bytes=effective_limits.max_response_bytes,
        max_line_bytes=effective_limits.max_ndjson_line_bytes,
        connect_timeout=settings.external_connect_timeout_seconds,
        read_timeout=settings.external_read_timeout_seconds,
    ) as adapter:
        content_type, payload_bytes = (
            await adapter.read_first_discover_page_for_contract_probe()
        )
    return summarize_contract_response(
        content_type=content_type,
        payload_bytes=payload_bytes,
        limits=effective_limits,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="NextFind first-page read-only, schema-only contract probe"
    )
    parser.add_argument(
        "--confirm-read-only",
        action="store_true",
        help="confirm one login and one read-only first-page discover request",
    )
    return parser


def _write_json(stream: TextIO, payload: object) -> None:
    json.dump(payload, stream, ensure_ascii=False, separators=(",", ":"))
    stream.write("\n")


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if not bool(args.confirm_read_only):
        _write_json(
            sys.stderr,
            {"status": "error", "error_code": "READ_ONLY_CONFIRMATION_REQUIRED"},
        )
        return 2
    try:
        summary = asyncio.run(
            run_contract_probe(
                confirm_read_only=True,
                settings=get_settings(),
            )
        )
    except AppError as exc:
        _write_json(sys.stderr, {"status": "error", "error_code": exc.error_code})
        return 2 if exc.error_code == "NEXTFIND_NOT_CONFIGURED" else 1
    except Exception:
        _write_json(
            sys.stderr,
            {"status": "error", "error_code": "CONTRACT_PROBE_FAILED"},
        )
        return 1
    _write_json(sys.stdout, summary.model_dump(mode="json"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
