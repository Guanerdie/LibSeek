from __future__ import annotations

import inspect
from collections.abc import Callable
from dataclasses import dataclass, field

from app.adapters.base import PtSiteAdapter
from app.adapters.pt_sites.catalog import (
    PtSiteCatalog,
    PtSiteDeclaration,
    avistaz_site_declaration,
)
from app.adapters.pt_sites.profiles import NexusPhpSiteProfile
from app.errors import AppError
from app.schemas.adapters import PtSearchMode

PtSiteFactory = Callable[[], PtSiteAdapter]


@dataclass(frozen=True)
class PtSiteRegistration:
    site_id: str
    factory: PtSiteFactory = field(repr=False, compare=False)


class PtSiteRegistry:
    """Worker factories bound to one immutable, secret-free site catalog."""

    def __init__(self, catalog: PtSiteCatalog | None = None) -> None:
        self.catalog = catalog or PtSiteCatalog()
        self._registrations: dict[str, PtSiteRegistration] = {}

    @staticmethod
    def validate_site_id(site_id: str) -> str:
        return PtSiteCatalog.validate_site_id(site_id)

    def register(self, site_id: str, factory: PtSiteFactory) -> None:
        validated = self.validate_site_id(site_id)
        if self.catalog.get(validated) is None:
            raise ValueError(f"PT site has no catalog declaration: {validated}")
        if validated in self._registrations:
            raise ValueError(f"PT site is already registered: {validated}")
        self._registrations[validated] = PtSiteRegistration(
            site_id=validated,
            factory=factory,
        )

    def register_nexusphp_profile(
        self,
        profile: NexusPhpSiteProfile,
        factory: PtSiteFactory,
    ) -> None:
        declaration = self.catalog.get(profile.site_id)
        if declaration is None or declaration.site_id != profile.site_id:
            raise ValueError(f"NexusPHP profile has no catalog declaration: {profile.site_id}")
        self.register(profile.site_id, factory)

    @property
    def registered_site_ids(self) -> tuple[str, ...]:
        return tuple(self._registrations)

    def require_enabled(self, site_id: str) -> PtSiteRegistration:
        declaration = self.catalog.require_searchable(site_id)
        registration = self._registrations.get(declaration.site_id)
        if registration is None:
            raise AppError(
                "PT_SITE_FACTORY_NOT_REGISTERED",
                "PT 站点 Worker 工厂未注册；不会回退到其他站点",
                status_code=409,
            )
        return registration

    def assert_complete(self) -> None:
        expected = {
            site_id
            for site_id in self.catalog.registered_site_ids
            if (declaration := self.catalog.get(site_id)) is not None
            and declaration.available_for_search
        }
        actual = set(self._registrations)
        missing = expected - actual
        unexpected = actual - set(self.catalog.registered_site_ids)
        if missing or unexpected:
            raise ValueError(
                "PT site catalog and Worker factories differ: "
                f"missing={sorted(missing)!r}, unexpected={sorted(unexpected)!r}"
            )

    async def create(self, site_id: str) -> PtSiteAdapter:
        registration = self.require_enabled(site_id)
        declaration = self.catalog.require_searchable(site_id)
        adapter = registration.factory()
        try:
            manifest = adapter.manifest()
        except Exception as exc:
            await self._safe_close(adapter)
            raise AppError(
                "PT_SITE_ADAPTER_MANIFEST_INVALID",
                "PT 站点适配器能力声明无效",
                status_code=409,
            ) from exc
        if (
            manifest.id != registration.site_id
            or manifest.adapter_type != "pt_site"
        ):
            await self._safe_close(adapter)
            raise AppError(
                "PT_SITE_ADAPTER_ID_MISMATCH",
                "PT 站点适配器身份与注册项不一致",
                status_code=409,
            )
        if manifest.enabled is not True:
            await self._safe_close(adapter)
            raise AppError(
                "PT_SITE_ADAPTER_DISABLED",
                "PT 站点适配器自声明为不可用",
                status_code=409,
            )
        if not self._manifest_supports(declaration, manifest.capabilities):
            await self._safe_close(adapter)
            raise AppError(
                "PT_SITE_ADAPTER_CAPABILITY_MISMATCH",
                "PT 站点适配器能力与目录声明不一致",
                status_code=409,
            )
        return adapter

    @staticmethod
    def _manifest_supports(
        declaration: PtSiteDeclaration,
        capabilities: dict[str, bool],
    ) -> bool:
        capability_names = {
            PtSearchMode.TMDB_ID: "tmdb_search",
            PtSearchMode.IMDB_ID: "imdb_search",
            PtSearchMode.TEXT: "text_search",
        }
        return all(
            capabilities.get(capability_names[mode]) is True
            for mode in declaration.search_modes
        )

    @staticmethod
    async def _safe_close(adapter: object) -> None:
        close = getattr(adapter, "aclose", None)
        if not callable(close):
            close = getattr(adapter, "close", None)
        if not callable(close):
            return
        try:
            result = close()
            if inspect.isawaitable(result):
                await result
        except Exception:
            # Cleanup must not replace the stable registration error.
            pass


def default_pt_site_registry(
    avistaz_factory: PtSiteFactory,
    *,
    catalog: PtSiteCatalog | None = None,
    enabled: bool = True,
    runtime_ready: bool = True,
) -> PtSiteRegistry:
    """Build the production registry. No generic NexusPHP site is implied."""

    effective_catalog = catalog or PtSiteCatalog(
        (
            avistaz_site_declaration(
                search_enabled=enabled,
                runtime_ready=runtime_ready,
            ),
        ),
        default_site_id="avistaz",
    )
    registry = PtSiteRegistry(effective_catalog)
    registry.register("avistaz", avistaz_factory)
    registry.assert_complete()
    return registry
