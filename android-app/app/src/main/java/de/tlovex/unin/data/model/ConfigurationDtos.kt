package de.tlovex.unin.data.model

import com.google.gson.annotations.SerializedName

/**
 * Editable configuration snapshot. The backend only returns public identity fields and boolean
 * secret-presence flags; passwords, tokens, cookies, PID values and passkeys are never returned.
 */
data class ConfigurationStatusDto(
    val nextfind: NextFindConfigurationStatusDto,
    val tmdb: ConnectionStatusDto,
    @SerializedName("outbound_proxy")
    val outboundProxy: OutboundProxyConfigurationStatusDto,
    @SerializedName("pt_site") val ptSite: PtSiteStatusDto?,
    @SerializedName("pt_sites") val ptSites: PtSiteStatusesDto,
    val qbittorrent: QbittorrentConfigurationStatusDto,
    @SerializedName("configuration_complete") val configurationComplete: Boolean,
)

data class ConnectionStatusDto(
    val configured: Boolean,
)

data class NextFindConfigurationStatusDto(
    @SerializedName("base_url") val baseUrl: String,
    val username: String,
    @SerializedName("password_configured") val passwordConfigured: Boolean,
    val configured: Boolean,
)

data class OutboundProxyConfigurationStatusDto(
    val url: String,
    val username: String,
    @SerializedName("password_configured") val passwordConfigured: Boolean,
    val configured: Boolean,
)

data class QbittorrentConfigurationStatusDto(
    val url: String,
    val username: String,
    @SerializedName("save_path") val savePath: String,
    val category: String,
    val configured: Boolean,
    @SerializedName("allow_insecure_http") val allowInsecureHttp: Boolean,
)

data class PtSiteStatusDto(
    val architecture: PtSiteArchitecture,
    @SerializedName("base_url") val baseUrl: String = "",
    val username: String = "",
    @SerializedName("password_configured") val passwordConfigured: Boolean = false,
    @SerializedName("pid_configured") val pidConfigured: Boolean = false,
    @SerializedName("site_id") val siteId: String = "",
    @SerializedName("display_name") val displayName: String = "",
    @SerializedName("cookie_configured") val cookieConfigured: Boolean = false,
    @SerializedName("passkey_configured") val passkeyConfigured: Boolean = false,
    val configured: Boolean,
    @SerializedName("runtime_supported") val runtimeSupported: Boolean,
    @SerializedName("search_ready") val searchReady: Boolean,
)

data class PtSiteStatusesDto(
    val avistaz: PtSiteStatusDto?,
    val nexusphp: PtSiteStatusDto?,
)

enum class PtSiteArchitecture(private val wireValue: String) {
    @SerializedName("avistaz") AVISTAZ("avistaz"),
    @SerializedName("nexusphp") NEXUSPHP("nexusphp");

    override fun toString(): String = wireValue
}

data class ConnectionTestResultDto(
    val target: String,
    val healthy: Boolean,
    @SerializedName("error_code") val errorCode: String?,
    val message: String,
)

/** Request-only container for the backend's partial configuration update endpoint. */
class ConfigurationUpdateRequestDto(
    val nextfind: NextFindConfigurationUpdateDto? = null,
    val tmdb: TmdbConfigurationUpdateDto? = null,
    @SerializedName("outbound_proxy")
    val outboundProxy: OutboundProxyConfigurationUpdateDto? = null,
    @SerializedName("pt_site") val ptSite: PtSiteConfigurationUpdateDto? = null,
    val qbittorrent: QbittorrentConfigurationUpdateDto? = null,
) {
    override fun toString(): String = "ConfigurationUpdateRequestDto([REDACTED])"
}

class NextFindConfigurationUpdateDto(
    @SerializedName("base_url") val baseUrl: String? = null,
    val username: String? = null,
    val password: String? = null,
) {
    override fun toString(): String = "NextFindConfigurationUpdateDto([REDACTED])"
}

class TmdbConfigurationUpdateDto(
    val token: String? = null,
) {
    override fun toString(): String = "TmdbConfigurationUpdateDto([REDACTED])"
}

class OutboundProxyConfigurationUpdateDto(
    val url: String? = null,
    val username: String? = null,
    val password: String? = null,
) {
    override fun toString(): String = "OutboundProxyConfigurationUpdateDto([REDACTED])"
}

sealed interface PtSiteConfigurationUpdateDto {
    val architecture: PtSiteArchitecture
}

class AvistaZConfigurationUpdateDto(
    @SerializedName("base_url") val baseUrl: String,
    val username: String,
    val password: String? = null,
    val pid: String? = null,
) : PtSiteConfigurationUpdateDto {
    override val architecture: PtSiteArchitecture = PtSiteArchitecture.AVISTAZ

    override fun toString(): String = "AvistaZConfigurationUpdateDto([REDACTED])"
}

class NexusPhpConfigurationUpdateDto(
    @SerializedName("site_id") val siteId: String,
    @SerializedName("display_name") val displayName: String,
    @SerializedName("base_url") val baseUrl: String,
    val cookie: String? = null,
    val passkey: String? = null,
) : PtSiteConfigurationUpdateDto {
    override val architecture: PtSiteArchitecture = PtSiteArchitecture.NEXUSPHP

    override fun toString(): String = "NexusPhpConfigurationUpdateDto([REDACTED])"
}

class QbittorrentConfigurationUpdateDto(
    val url: String? = null,
    val username: String? = null,
    val password: String? = null,
    @SerializedName("save_path") val savePath: String? = null,
    val category: String? = null,
    @SerializedName("allow_insecure_http") val allowInsecureHttp: Boolean? = null,
) {
    override fun toString(): String = "QbittorrentConfigurationUpdateDto([REDACTED])"
}
