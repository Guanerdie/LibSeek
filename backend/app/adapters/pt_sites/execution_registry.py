from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Protocol

from pydantic import TypeAdapter, ValidationError

from app.adapters.pt_sites.catalog import PtSiteCatalog, PtSiteDeclaration
from app.errors import AppError
from app.models.enums import MediaType
from app.schemas.adapters import (
    AdapterManifest,
    PtSearchMode,
    SiteId,
    TorrentCandidate,
    TorrentSearchRequest,
)


class PtExecutionAdapter(Protocol):
    def manifest(self) -> AdapterManifest: ...

    def set_before_request_guard(
        self, guard: Callable[[], Awaitable[None]] | None
    ) -> None: ...

    async def search(self, request: TorrentSearchRequest) -> list[TorrentCandidate]: ...

    async def fetch_torrent(self, torrent_id: str) -> bytes: ...

    async def aclose(self) -> None: ...


PtExecutionFactory = Callable[[], PtExecutionAdapter]
_SITE_ID_ADAPTER = TypeAdapter(SiteId)


@dataclass(frozen=True)
class PtExecutionRegistration:
    site_id: str
    factory: PtExecutionFactory = field(repr=False, compare=False)


class PtExecutionRegistry:
    """Execution-only PT factories, resolved strictly by immutable plan site_id."""

    def __init__(self, catalog: PtSiteCatalog) -> None:
        self._catalog = catalog
        self._registrations: dict[str, PtExecutionRegistration] = {}

    @staticmethod
    def validate_site_id(site_id: str) -> str:
        try:
            return _SITE_ID_ADAPTER.validate_python(site_id)
        except ValidationError as exc:
            raise AppError(
                "PT_SITE_ID_INVALID", "PT 站点标识格式无效", status_code=400
            ) from exc

    def register(self, site_id: str, factory: PtExecutionFactory) -> None:
        validated = self.validate_site_id(site_id)
        if validated in self._registrations:
            raise ValueError(f"PT execution site is already registered: {validated}")
        self._registrations[validated] = PtExecutionRegistration(validated, factory)

    @property
    def registered_site_ids(self) -> tuple[str, ...]:
        return tuple(self._registrations)

    def require_registered(
        self,
        site_id: str,
        *,
        media_type: MediaType | None = None,
    ) -> PtExecutionRegistration:
        validated = self.validate_site_id(site_id)
        self._catalog.require_fetchable(validated, media_type=media_type)
        registration = self._registrations.get(validated)
        if registration is None:
            raise AppError(
                "PT_SITE_EXECUTOR_UNSUPPORTED",
                "当前下载执行器未注册该 PT 站点；不会回退到其他站点",
                status_code=409,
            )
        return registration

    def require_declaration(
        self,
        site_id: str,
        *,
        media_type: MediaType | None = None,
    ) -> PtSiteDeclaration:
        validated = self.validate_site_id(site_id)
        return self._catalog.require_fetchable(validated, media_type=media_type)

    def search_modes(
        self,
        site_id: str,
        *,
        media_type: MediaType | None = None,
    ) -> tuple[PtSearchMode, ...]:
        return self.require_declaration(site_id, media_type=media_type).search_modes

    async def create(
        self,
        site_id: str,
        *,
        media_type: MediaType | None = None,
    ) -> PtExecutionAdapter:
        registration = self.require_registered(site_id, media_type=media_type)
        declaration = self.require_declaration(site_id, media_type=media_type)
        try:
            adapter = registration.factory()
        except AppError:
            raise
        except Exception as exc:
            raise AppError(
                "PT_SITE_EXECUTION_ADAPTER_FACTORY_FAILED",
                "PT 站点下载执行适配器创建失败",
                status_code=409,
            ) from exc

        try:
            manifest = adapter.manifest()
        except Exception as exc:
            await self._safe_close(adapter)
            raise AppError(
                "PT_SITE_EXECUTION_ADAPTER_MANIFEST_INVALID",
                "PT 站点下载执行适配器能力声明无效",
                status_code=409,
            ) from exc

        if manifest.id != registration.site_id:
            await self._safe_close(adapter)
            raise AppError(
                "PT_SITE_EXECUTION_ADAPTER_ID_MISMATCH",
                "PT 站点下载执行适配器身份与注册项不一致",
                status_code=409,
            )
        search_capabilities = {
            PtSearchMode.TMDB_ID: "tmdb_search",
            PtSearchMode.IMDB_ID: "imdb_search",
            PtSearchMode.TEXT: "text_search",
        }
        if (
            manifest.adapter_type != "pt_site"
            or manifest.enabled is not True
            or manifest.capabilities.get("fetch_torrent_enabled") is not True
            or any(
                manifest.capabilities.get(search_capabilities[mode]) is not True
                for mode in declaration.search_modes
            )
        ):
            await self._safe_close(adapter)
            raise AppError(
                "PT_SITE_EXECUTION_ADAPTER_CAPABILITY_MISMATCH",
                "PT 站点下载执行适配器的搜索或取种能力与目录声明不一致",
                status_code=409,
            )
        return adapter

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
            pass
