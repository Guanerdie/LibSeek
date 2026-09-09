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
    @SerializedName("latest_automation") val latestAutomation: AutomationOutcomeDto? = null,
    @SerializedName("minimum_score_override") val minimumScoreOverride: Double? = null,
    val subscribed: Boolean = false,
    // False when subscribed but the policy scope is still "filters", i.e. the
    // subscription list is not what automation is reading.
    @SerializedName("subscription_active") val subscriptionActive: Boolean = false,
)

/** A candidate automation turned down, and the server's reasons for it. */
data class RejectedCandidateDto(
    @SerializedName("candidate_id") val candidateId: String? = null,
    val title: String? = null,
    val reasons: List<String> = emptyList(),
)

/** Why the last automation attempt did or did not download anything. */
data class AutomationOutcomeDto(
    @SerializedName("job_id") val jobId: String,
    val state: AutomationJobState,
    @SerializedName("created_at") val createdAt: String,
    @SerializedName("finished_at") val finishedAt: String? = null,
    @SerializedName("error_code") val errorCode: String? = null,
    @SerializedName("error_message") val errorMessage: String? = null,
    @SerializedName("candidate_count") val candidateCount: Int = 0,
    @SerializedName("selected_title") val selectedTitle: String? = null,
    @SerializedName("selected_score") val selectedScore: Double? = null,
    @SerializedName("search_cooldown_until") val searchCooldownUntil: String? = null,
    @SerializedName("download_skipped") val downloadSkipped: String? = null,
    val rejected: List<RejectedCandidateDto> = emptyList(),
)

data class SubscriptionUpdateDto(
    val subscribed: Boolean,
)

data class QuickFillRequestDto(
    /** Skip the server's five-minute result cache and ask the site again. */
    val force: Boolean = false,
)

/**
 * The outcome of one click: a search was run, the best candidate picked, and --
 * unless the policy said otherwise -- submitted.
 *
 * Every candidate is still returned, so the operator can override the pick by
 * hand; this is a shortcut over the manual path, not a replacement for it.
 */
data class QuickFillResultDto(
    val search: SearchDetailDto,
    @SerializedName("selected_candidate_id") val selectedCandidateId: String? = null,
    val download: DownloadDto? = null,
    val rejected: List<RejectedCandidateDto> = emptyList(),
)

data class IdentityRequestDto(
    @SerializedName("tmdb_id") val tmdbId: Int? = null,
)

data class SyncResultDto(
    val created: Int = 0,
    val updated: Int = 0,
)
