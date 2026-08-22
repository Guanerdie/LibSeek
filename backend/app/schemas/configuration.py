from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr

from app.schemas.adapters import ProbeResult


class ConfigurationModel(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)


class NextFindConfigurationResponse(ConfigurationModel):
    base_url: str
    username: str
    password_configured: bool
    configured: bool


class TmdbConfigurationResponse(ConfigurationModel):
    configured: bool


class OutboundProxyConfigurationResponse(ConfigurationModel):
    url: str
    username: str
    password_configured: bool
    configured: bool


class AvistaZConfigurationResponse(ConfigurationModel):
    architecture: Literal["avistaz"] = "avistaz"
    base_url: str
    username: str
    password_configured: bool
    pid_configured: bool
    configured: bool
    runtime_supported: bool = True
    search_ready: bool


class NexusPhpConfigurationResponse(ConfigurationModel):
    architecture: Literal["nexusphp"] = "nexusphp"
    site_id: str
    display_name: str
    base_url: str
    profile_id: str | None = None
    cookie_configured: bool
    passkey_configured: bool
    configured: bool
    runtime_supported: bool = False
    search_ready: bool = False


PtSiteConfigurationResponse = Annotated[
    AvistaZConfigurationResponse | NexusPhpConfigurationResponse,
    Field(discriminator="architecture"),
]


class PtSiteConfigurationsResponse(ConfigurationModel):
    avistaz: AvistaZConfigurationResponse | None
    nexusphp: NexusPhpConfigurationResponse | None


class PtSiteArchitectureField(ConfigurationModel):
    name: str
    label: str
    input_type: Literal["text", "url", "password"]
    required: bool
    secret: bool


class PtSiteArchitectureOption(ConfigurationModel):
    architecture: Literal["avistaz", "nexusphp"]
    label: str
    runtime_supported: bool
    connection_test_supported: bool
    fields: tuple[PtSiteArchitectureField, ...]


class QbittorrentConfigurationResponse(ConfigurationModel):
    url: str
    username: str
    save_path: str
    category: str
    configured: bool
    allow_insecure_http: bool


class ConfigurationResponse(ConfigurationModel):
    nextfind: NextFindConfigurationResponse
    tmdb: TmdbConfigurationResponse
    outbound_proxy: OutboundProxyConfigurationResponse
    pt_site: PtSiteConfigurationResponse | None
    pt_sites: PtSiteConfigurationsResponse
    pt_site_architectures: tuple[PtSiteArchitectureOption, ...]
    qbittorrent: QbittorrentConfigurationResponse
    configuration_complete: bool


class NextFindConfigurationUpdate(ConfigurationModel):
    base_url: str | None = Field(default=None, max_length=2048)
    username: str | None = Field(default=None, max_length=120)
    password: SecretStr | None = Field(default=None, max_length=8192)


class TmdbConfigurationUpdate(ConfigurationModel):
    token: SecretStr | None = Field(default=None, max_length=8192)


class OutboundProxyConfigurationUpdate(ConfigurationModel):
    url: str | None = Field(default=None, max_length=2048)
    username: str | None = Field(default=None, max_length=120)
    password: SecretStr | None = Field(default=None, max_length=8192)


class AvistaZConfigurationUpdate(ConfigurationModel):
    architecture: Literal["avistaz"]
    base_url: str = Field(max_length=2048)
    username: str = Field(max_length=120)
    password: SecretStr | None = Field(default=None, max_length=8192)
    pid: SecretStr | None = Field(default=None, max_length=8192)


class NexusPhpConfigurationUpdate(ConfigurationModel):
    architecture: Literal["nexusphp"]
    site_id: str = Field(max_length=24, pattern=r"^[a-z0-9](?:[a-z0-9-]{0,22}[a-z0-9])?$")
    display_name: str = Field(max_length=100)
    base_url: str = Field(max_length=2048)
    cookie: SecretStr | None = Field(default=None, max_length=8192)
    passkey: SecretStr | None = Field(default=None, max_length=8192)


PtSiteConfigurationUpdate = Annotated[
    AvistaZConfigurationUpdate | NexusPhpConfigurationUpdate,
    Field(discriminator="architecture"),
]


class QbittorrentConfigurationUpdate(ConfigurationModel):
    url: str | None = Field(default=None, max_length=2048)
    username: str | None = Field(default=None, max_length=120)
    password: SecretStr | None = Field(default=None, max_length=8192)
    save_path: str | None = Field(default=None, max_length=4096)
    category: str | None = Field(default=None, max_length=300)
    allow_insecure_http: bool | None = None


class ConfigurationUpdateRequest(ConfigurationModel):
    nextfind: NextFindConfigurationUpdate | None = None
    tmdb: TmdbConfigurationUpdate | None = None
    outbound_proxy: OutboundProxyConfigurationUpdate | None = None
    pt_site: PtSiteConfigurationUpdate | None = None
    qbittorrent: QbittorrentConfigurationUpdate | None = None

    def runtime_updates(self) -> dict[str, dict[str, object]]:
        result: dict[str, dict[str, object]] = {}
        for group_name in (
            "nextfind",
            "tmdb",
            "outbound_proxy",
            "pt_site",
            "qbittorrent",
        ):
            group = getattr(self, group_name)
            if group is None:
                continue
            values = group.model_dump(exclude_unset=True)
            result[group_name] = {
                key: value.get_secret_value() if isinstance(value, SecretStr) else value
                for key, value in values.items()
            }
        return result


class ConfigurationTestResponse(ProbeResult):
    target: Literal["nextfind", "tmdb", "outbound_proxy", "pt_site", "qbittorrent"]


PT_SITE_ARCHITECTURES = (
    PtSiteArchitectureOption(
        architecture="avistaz",
        label="AvistaZ",
        runtime_supported=True,
        connection_test_supported=True,
        fields=(
            PtSiteArchitectureField(
                name="base_url", label="站点地址", input_type="url", required=True, secret=False
            ),
            PtSiteArchitectureField(
                name="username", label="用户名", input_type="text", required=True, secret=False
            ),
            PtSiteArchitectureField(
                name="password", label="密码", input_type="password", required=True, secret=True
            ),
            PtSiteArchitectureField(
                name="pid", label="PID", input_type="password", required=True, secret=True
            ),
        ),
    ),
    PtSiteArchitectureOption(
        architecture="nexusphp",
        label="NexusPHP",
        runtime_supported=False,
        connection_test_supported=True,
        fields=(
            PtSiteArchitectureField(
                name="site_id", label="站点标识", input_type="text", required=True, secret=False
            ),
            PtSiteArchitectureField(
                name="display_name",
                label="显示名称",
                input_type="text",
                required=True,
                secret=False,
            ),
            PtSiteArchitectureField(
                name="base_url", label="HTTPS 地址", input_type="url", required=True, secret=False
            ),
            PtSiteArchitectureField(
                name="cookie", label="Cookie", input_type="password", required=True, secret=True
            ),
            PtSiteArchitectureField(
                name="passkey", label="Passkey", input_type="password", required=False, secret=True
            ),
        ),
    ),
)
