package de.tlovex.unin.data.model

import com.google.gson.annotations.SerializedName

enum class SearchState {
    PENDING,
    RUNNING,
    SUCCEEDED,
    FAILED,
}

data class SearchRequestDto(
    @SerializedName("site_ids") val siteIds: List<String>,
)

data class SearchSummaryDto(
    val id: String,
    @SerializedName("media_id") val mediaId: String,
    @SerializedName("site_ids") val siteIds: List<String>,
    val state: SearchState,
    @SerializedName("error_message") val errorMessage: String?,
    @SerializedName("created_at") val createdAt: String,
    @SerializedName("finished_at") val finishedAt: String?,
)

data class CandidateDto(
    val id: String,
    @SerializedName("search_id") val searchId: String,
    @SerializedName("site_id") val siteId: String,
    @SerializedName("torrent_id") val torrentId: String,
    val title: String,
    @SerializedName("details_url") val detailsUrl: String?,
    @SerializedName("size_bytes") val sizeBytes: Long?,
    val seeders: Int?,
    val resolution: String?,
    val source: String?,
    val codec: String?,
    @SerializedName("download_factor") val downloadFactor: Double?,
    @SerializedName("season_coverage") val seasonCoverage: List<Int>,
    @SerializedName("episode_coverage") val episodeCoverage: List<String>,
    val score: Double,
    val reasons: List<String>,
    val warnings: List<String>,
)

data class SearchDetailDto(
    val id: String,
    @SerializedName("media_id") val mediaId: String,
    @SerializedName("site_ids") val siteIds: List<String>,
    val state: SearchState,
    @SerializedName("error_message") val errorMessage: String?,
    @SerializedName("created_at") val createdAt: String,
    @SerializedName("finished_at") val finishedAt: String?,
    val candidates: List<CandidateDto>,
)
