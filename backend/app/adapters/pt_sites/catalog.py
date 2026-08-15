from __future__ import annotations

from types import MappingProxyType
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError, model_validator

from app.adapters.pt_sites.profiles import NexusPhpSiteProfile
from app.core.config import Settings
from app.errors import AppError
from app.models.enums import MediaType
from app.schemas.adapters import (
    PtSearchMode,
    PtSiteCatalogEntry,
    PtSiteCatalogResponse,
    SiteId,
)

_SITE_ID_ADAPTER = TypeAdapter(SiteId)


class PtSiteDeclaration(BaseModel):
    """Immutable, secret-free declaration shared by API and Worker wiring."""

    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    site_id: SiteId
    display_name: str = Field(min_length=1, max_length=100)
    description: str = Field(min_length=1, max_length=300)
    search_modes: tuple[PtSearchMode, ...] = Field(min_length=1)
    media_types: tuple[MediaType, ...] = Field(min_length=1)
    search_enabled: bool = False
    runtime_ready: bool = False
    manual_only: bool = True
    promotion_metadata: bool = False
    hit_and_run_metadata: bool = False
    torrent_fetch_enabled: bool = False
    disabled_error_code: str = Field(
        default="PT_SITE_SEARCH_DISABLED",
        pattern=r"^[A-Z][A-Z0-9_]{2,79}$",
    )
    not_ready_error_code: str = Field(
        default="PT_SITE_NOT_CONFIGURED",
        pattern=r"^[A-Z][A-Z0-9_]{2,79}$",
    )

    @model_validator(mode="after")
    def validate_closed_capabilities(self) -> Self:
        if len(set(self.search_modes)) != len(self.search_modes):
            raise ValueError("PT search modes must be unique")
        if len(set(self.media_types)) != len(self.media_types):
            raise ValueError("PT media types must be unique")
        return self

    @property
    def available_for_search(self) -> bool:
        return self.search_enabled and self.runtime_ready

    @property
    def unavailable_reason(self) -> tuple[str | None, str | None]:
        if not self.search_enabled:
            return (
                self.disabled_error_code,
                f"{self.display_name} 只读搜索默认关闭",
            )
        if not self.runtime_ready:
            return (
                self.not_ready_error_code,
                f"{self.display_name} 运行时凭据未配置",
            )
        return None, None

    def public_entry(self) -> PtSiteCatalogEntry:
        unavailable_code, unavailable_message = self.unavailable_reason
        return PtSiteCatalogEntry(
            site_id=self.site_id,
            display_name=self.display_name,
            description=self.description,
            available_for_search=self.available_for_search,
            unavailable_reason_code=unavailable_code,
            unavailable_reason_message=unavailable_message,
            mode=(
                "LIVE_READ_ONLY_SEARCH"
                if self.available_for_search
                else "DISABLED_BY_DEFAULT"
            ),
            search_modes=self.search_modes,
            media_types=self.media_types,
            manual_only=self.manual_only,
            promotion_metadata=self.promotion_metadata,
            hit_and_run_metadata=self.hit_and_run_metadata,
            torrent_fetch_enabled=self.torrent_fetch_enabled,
        )


class PtSiteCatalog:
    """Closed catalog of non-sensitive PT site declarations."""

    def __init__(
        self,
        declarations: tuple[PtSiteDeclaration, ...] = (),
        *,
        default_site_id: str | None = None,
    ) -> None:
        entries: dict[str, PtSiteDeclaration] = {}
        for declaration in declarations:
            if declaration.site_id in entries:
                raise ValueError(f"PT site is already declared: {declaration.site_id}")
            entries[declaration.site_id] = declaration
        if default_site_id is not None:
            validated_default = self.validate_site_id(default_site_id)
            if validated_default not in entries:
                raise ValueError(
                    f"default PT site has no catalog declaration: {validated_default}"
                )
        else:
            validated_default = None
        self._entries = MappingProxyType(entries)
        self.default_site_id = validated_default

    @staticmethod
    def validate_site_id(site_id: str) -> str:
        try:
            return _SITE_ID_ADAPTER.validate_python(site_id)
        except ValidationError as exc:
            raise AppError(
                "PT_SITE_ID_INVALID",
                "PT 站点标识格式无效",
                status_code=400,
            ) from exc

    @property
    def registered_site_ids(self) -> tuple[str, ...]:
        return tuple(self._entries)

    def get(self, site_id: str) -> PtSiteDeclaration | None:
        validated = self.validate_site_id(site_id)
        return self._entries.get(validated)

    def list_public(self) -> tuple[PtSiteCatalogEntry, ...]:
        return tuple(entry.public_entry() for entry in self._entries.values())

    def public_response(self) -> PtSiteCatalogResponse:
        return PtSiteCatalogResponse(
            default_site_id=self.default_site_id,
            sites=self.list_public(),
        )

    def require_searchable(
        self,
        site_id: str,
        *,
        media_type: MediaType | None = None,
    ) -> PtSiteDeclaration:
        validated = self.validate_site_id(site_id)
        declaration = self._entries.get(validated)
        if declaration is None:
            raise AppError(
                "PT_SITE_NOT_REGISTERED",
                "PT 站点未注册；不会回退到其他站点",
                status_code=409,
            )
        if not declaration.search_enabled:
            raise AppError(
                declaration.disabled_error_code,
                f"{declaration.display_name} 只读搜索默认关闭",
                status_code=409,
            )
        if not declaration.runtime_ready:
            raise AppError(
                declaration.not_ready_error_code,
                f"{declaration.display_name} 运行时凭据未配置",
                status_code=409,
            )
        if media_type is not None and media_type not in declaration.media_types:
            raise AppError(
                "PT_SITE_MEDIA_TYPE_UNSUPPORTED",
                "PT 站点不支持该影视类型",
                status_code=409,
            )
        return declaration

    def require_fetchable(
        self,
        site_id: str,
        *,
        media_type: MediaType | None = None,
    ) -> PtSiteDeclaration:
        declaration = self.require_searchable(site_id, media_type=media_type)
        if not declaration.torrent_fetch_enabled:
            raise AppError(
                "PT_SITE_TORRENT_FETCH_UNSUPPORTED",
                "PT 站点当前未启用种子获取能力",
                status_code=409,
            )
        return declaration


def avistaz_site_declaration(
    *,
    search_enabled: bool,
    runtime_ready: bool,
    torrent_fetch_enabled: bool = False,
) -> PtSiteDeclaration:
    return PtSiteDeclaration(
        site_id="avistaz",
        display_name="AvistaZ",
        description="真实只读 PT 候选搜索；运行时 Secret 不进入目录或响应",
        search_modes=(PtSearchMode.TMDB_ID, PtSearchMode.IMDB_ID, PtSearchMode.TEXT),
        media_types=(MediaType.MOVIE, MediaType.TV),
        search_enabled=search_enabled,
        runtime_ready=runtime_ready,
        manual_only=False,
        promotion_metadata=True,
        hit_and_run_metadata=True,
        torrent_fetch_enabled=torrent_fetch_enabled,
        disabled_error_code="AVISTAZ_LIVE_DISABLED",
        not_ready_error_code="AVISTAZ_NOT_CONFIGURED",
    )


def nexusphp_site_declaration(
    profile: NexusPhpSiteProfile,
    *,
    runtime_ready: bool,
) -> PtSiteDeclaration:
    search_modes: list[PtSearchMode] = []
    if profile.query.tmdb is not None:
        search_modes.append(PtSearchMode.TMDB_ID)
    if profile.query.imdb is not None:
        search_modes.append(PtSearchMode.IMDB_ID)
    search_modes.append(PtSearchMode.TEXT)
    return PtSiteDeclaration(
        site_id=profile.site_id,
        display_name=profile.display_name,
        description="经过 fixture 验证的声明式 NexusPHP 只读候选搜索",
        search_modes=tuple(search_modes),
        media_types=tuple(profile.category_mapping),
        search_enabled=profile.enabled,
        runtime_ready=runtime_ready,
        manual_only=True,
        promotion_metadata=profile.selectors.discount is not None,
        hit_and_run_metadata=False,
        torrent_fetch_enabled=False,
        disabled_error_code="NEXUSPHP_SITE_DISABLED",
        not_ready_error_code="NEXUSPHP_SESSION_NOT_CONFIGURED",
    )


def build_pt_site_catalog(settings: Settings) -> PtSiteCatalog:
    """Build production declarations without retaining Settings or Secret values."""

    return PtSiteCatalog(
        (
            avistaz_site_declaration(
                search_enabled=(
                    settings.pt_site_architecture == "avistaz"
                    and settings.enable_avistaz_live_search
                ),
                runtime_ready=(
                    settings.pt_site_architecture == "avistaz"
                    and settings.pt_site_runtime_supported
                    and settings.pt_site_configured
                ),
                torrent_fetch_enabled=(
                    settings.pt_site_architecture == "avistaz"
                    and settings.enable_avistaz_torrent_fetch
                ),
            ),
        ),
        default_site_id="avistaz",
    )
