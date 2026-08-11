from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from pydantic import TypeAdapter, ValidationError

from app.adapters.base import PtSiteAdapter
from app.adapters.pt_sites.profiles import NexusPhpSiteProfile
from app.errors import AppError
from app.schemas.adapters import SiteId

PtSiteFactory = Callable[[], PtSiteAdapter]
_SITE_ID_ADAPTER = TypeAdapter(SiteId)


@dataclass(frozen=True)
class PtSiteRegistration:
    site_id: str
    enabled: bool
    factory: PtSiteFactory = field(repr=False, compare=False)
    disabled_error_code: str = "PT_SITE_DISABLED"
    disabled_message: str = "该 PT 站点适配器默认关闭"


class PtSiteRegistry:
    """In-process registry; it stores factories, never credential values."""

    def __init__(self) -> None:
        self._registrations: dict[str, PtSiteRegistration] = {}

    @staticmethod
    def validate_site_id(site_id: str) -> str:
        try:
            return _SITE_ID_ADAPTER.validate_python(site_id)
        except ValidationError as exc:
            raise AppError("PT_SITE_ID_INVALID", "PT 站点标识格式无效", status_code=400) from exc

    def register(
        self,
        site_id: str,
        factory: PtSiteFactory,
        *,
        enabled: bool,
        disabled_error_code: str = "PT_SITE_DISABLED",
        disabled_message: str = "该 PT 站点适配器默认关闭",
    ) -> None:
        validated = self.validate_site_id(site_id)
        if validated in self._registrations:
            raise ValueError(f"PT site is already registered: {validated}")
        self._registrations[validated] = PtSiteRegistration(
            site_id=validated,
            enabled=enabled,
            factory=factory,
            disabled_error_code=disabled_error_code,
            disabled_message=disabled_message,
        )

    def register_nexusphp_profile(
        self,
        profile: NexusPhpSiteProfile,
        factory: PtSiteFactory,
    ) -> None:
        self.register(
            profile.site_id,
            factory,
            enabled=profile.enabled,
            disabled_error_code="NEXUSPHP_SITE_DISABLED",
            disabled_message="该 NexusPHP 站点 Profile 尚未启用",
        )

    @property
    def registered_site_ids(self) -> tuple[str, ...]:
        return tuple(self._registrations)

    def require_enabled(self, site_id: str) -> PtSiteRegistration:
        validated = self.validate_site_id(site_id)
        registration = self._registrations.get(validated)
        if registration is None:
            raise AppError(
                "PT_SITE_NOT_REGISTERED",
                "PT 站点未注册；不会回退到其他站点",
                status_code=409,
            )
        if not registration.enabled:
            raise AppError(
                registration.disabled_error_code,
                registration.disabled_message,
                status_code=409,
            )
        return registration

    def create(self, site_id: str) -> PtSiteAdapter:
        return self.require_enabled(site_id).factory()


def default_pt_site_registry(
    avistaz_factory: PtSiteFactory,
    *,
    enabled: bool = True,
    disabled_error_code: str = "AVISTAZ_LIVE_DISABLED",
    disabled_message: str = "AvistaZ 真实只读搜索默认关闭",
) -> PtSiteRegistry:
    """Build the default registry. No generic NexusPHP site is implied here."""

    registry = PtSiteRegistry()
    registry.register(
        "avistaz",
        avistaz_factory,
        enabled=enabled,
        disabled_error_code=disabled_error_code,
        disabled_message=disabled_message,
    )
    return registry
