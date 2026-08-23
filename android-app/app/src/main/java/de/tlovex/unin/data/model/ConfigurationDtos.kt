package de.tlovex.unin.data.model

import com.google.gson.annotations.SerializedName

/**
 * Deliberately minimal view of the configuration response. Credential-adjacent fields returned by
 * the web API (such as usernames and service URLs) are not represented in the Android client.
 */
data class ConfigurationStatusDto(
    val nextfind: ConnectionStatusDto,
    val tmdb: ConnectionStatusDto,
    @SerializedName("pt_site") val ptSite: PtSiteStatusDto?,
    @SerializedName("pt_sites") val ptSites: PtSiteStatusesDto,
    val qbittorrent: ConnectionStatusDto,
    @SerializedName("configuration_complete") val configurationComplete: Boolean,
)

data class ConnectionStatusDto(
    val configured: Boolean,
)

data class PtSiteStatusDto(
    val architecture: PtSiteArchitecture,
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
