package de.tlovex.unin.data.model

import com.google.gson.annotations.SerializedName

/**
 * `GET /api/stats`, the aggregates behind the web monitoring page.
 *
 * Read-only and viewer-visible: counts and durations the operator can already
 * see one page at a time. The app takes the summary numbers and leaves the
 * charts to the web, so `recent_runs` is deliberately not modelled -- the
 * automation list already shows that history.
 */
data class StatsDto(
    @SerializedName("window_days") val windowDays: Int = 7,
    @SerializedName("generated_at") val generatedAt: String? = null,
    @SerializedName("search_trend") val searchTrend: List<SearchTrendPointDto> = emptyList(),
    @SerializedName("site_latency") val siteLatency: List<SiteLatencyDto> = emptyList(),
    @SerializedName("library_coverage") val libraryCoverage: LibraryCoverageDto? = null,
    @SerializedName("download_health") val downloadHealth: DownloadHealthDto? = null,
)

data class SearchTrendPointDto(
    val date: String,
    val total: Int = 0,
    val succeeded: Int = 0,
    /** Null on a day with no searches; a flat zero would read as total failure. */
    @SerializedName("success_rate") val successRate: Double? = null,
)

data class SiteLatencyDto(
    @SerializedName("site_id") val siteId: String,
    val searches: Int = 0,
    @SerializedName("average_seconds") val averageSeconds: Double = 0.0,
    @SerializedName("slowest_seconds") val slowestSeconds: Double = 0.0,
)

data class LibraryCoverageDto(
    val total: Int = 0,
    /** Already downloaded or downloading: the part needing no more attention. */
    val covered: Int = 0,
    @SerializedName("coverage_rate") val coverageRate: Double? = null,
)

data class DownloadHealthDto(
    val total: Int = 0,
    val errored: Int = 0,
)
