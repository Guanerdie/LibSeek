package de.tlovex.unin.data.model

import com.google.gson.annotations.SerializedName

enum class MediaType(private val wireValue: String) {
    @SerializedName("movie") MOVIE("movie"),
    @SerializedName("tv") TV("tv");

    override fun toString(): String = wireValue
}

enum class MediaState {
    MISSING,
    IDENTIFYING,
    READY,
    SEARCHING,
    CANDIDATES,
    DOWNLOADING,
    COMPLETE,
    NEEDS_ATTENTION,
}

enum class EpisodeState {
    MISSING,
    AVAILABLE,
    DOWNLOADING,
}

data class MediaSummaryDto(
    val id: String,
    val source: String,
    @SerializedName("source_item_id") val sourceItemId: String,
    @SerializedName("media_type") val mediaType: MediaType,
    @SerializedName("tmdb_id") val tmdbId: Int?,
    val title: String,
    @SerializedName("original_title") val originalTitle: String?,
    @SerializedName("country_codes") val countryCodes: List<String>,
    @SerializedName("original_language") val originalLanguage: String?,
    val regions: List<String>,
    val year: Int?,
    @SerializedName("poster_path") val posterPath: String?,
    val state: MediaState,
    @SerializedName("attention_reason") val attentionReason: String?,
    @SerializedName("discovered_at") val discoveredAt: String,
    @SerializedName("updated_at") val updatedAt: String,
)

data class MediaFilterOptionsDto(
    @SerializedName("media_types") val mediaTypes: List<MediaType>,
    val regions: List<String>,
    val states: List<MediaState>,
    val years: List<Int>,
)

data class MediaPageDto(
    val items: List<MediaSummaryDto>,
    val total: Int,
    val page: Int,
    @SerializedName("page_size") val pageSize: Int,
    @SerializedName("filter_options") val filterOptions: MediaFilterOptionsDto,
)

data class EpisodeDto(
    val id: String,
    @SerializedName("season_number") val seasonNumber: Int,
    @SerializedName("episode_number") val episodeNumber: Int,
    val title: String?,
    @SerializedName("air_date") val airDate: String?,
    val state: EpisodeState,
)

data class MediaDetailDto(
    val id: String,
    val source: String,
    @SerializedName("source_item_id") val sourceItemId: String,
    @SerializedName("media_type") val mediaType: MediaType,
    @SerializedName("tmdb_id") val tmdbId: Int?,
    val title: String,
    @SerializedName("original_title") val originalTitle: String?,
    @SerializedName("country_codes") val countryCodes: List<String>,
    @SerializedName("original_language") val originalLanguage: String?,
    val regions: List<String>,
    val year: Int?,
    @SerializedName("poster_path") val posterPath: String?,
    val state: MediaState,
    @SerializedName("attention_reason") val attentionReason: String?,
    @SerializedName("discovered_at") val discoveredAt: String,
    @SerializedName("updated_at") val updatedAt: String,
    val episodes: List<EpisodeDto>,
    @SerializedName("latest_search") val latestSearch: SearchSummaryDto?,
)

data class IdentityRequestDto(
    @SerializedName("tmdb_id") val tmdbId: Int? = null,
)

data class SyncResultDto(
    val created: Int = 0,
    val updated: Int = 0,
)
