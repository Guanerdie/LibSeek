package de.tlovex.unin.data.model

import com.google.gson.annotations.SerializedName

data class AutomationPolicyUpdateDto(
    val enabled: Boolean = false,
    @SerializedName("dry_run") val dryRun: Boolean = true,
    @SerializedName("auto_identify") val autoIdentify: Boolean = true,
    @SerializedName("scope_mode") val scopeMode: AutomationScopeMode = AutomationScopeMode.FILTERS,
    val regions: List<String> = emptyList(),
    @SerializedName("selected_media_ids") val selectedMediaIds: List<String> = emptyList(),
    @SerializedName("site_ids") val siteIds: List<String> = listOf("avistaz"),
    @SerializedName("media_types") val mediaTypes: List<MediaType> =
        listOf(MediaType.MOVIE, MediaType.TV),
    @SerializedName("minimum_score") val minimumScore: Double = 0.7,
    @SerializedName("minimum_seeders") val minimumSeeders: Int = 1,
    @SerializedName("max_size_bytes") val maxSizeBytes: Long? = null,
    @SerializedName("allow_warnings") val allowWarnings: Boolean = false,
    @SerializedName("interval_minutes") val intervalMinutes: Int = 60,
    @SerializedName("retry_delay_minutes") val retryDelayMinutes: Int = 30,
    @SerializedName("max_attempts") val maxAttempts: Int = 3,
    @SerializedName("daily_download_limit") val dailyDownloadLimit: Int = 3,
    @SerializedName("daily_download_bytes") val dailyDownloadBytes: Long? = null,
    // Backoff after a search finds nothing, in hours; the tiers must not decrease.
    @SerializedName("cooldown_tier_1_hours") val cooldownTier1Hours: Int = 24,
    @SerializedName("cooldown_tier_2_hours") val cooldownTier2Hours: Int = 72,
    @SerializedName("cooldown_tier_3_hours") val cooldownTier3Hours: Int = 168,
    // Variety shows never finish a season, so they are followed by chasing the
    // newest few episodes. 0 turns that off.
    @SerializedName("variety_recent_episodes") val varietyRecentEpisodes: Int = 5,
    @SerializedName("automate_variety") val automateVariety: Boolean = false,
    // Release-quality weights; relative, normalised server-side when scoring.
    @SerializedName("weight_resolution") val weightResolution: Int = 44,
    @SerializedName("weight_size") val weightSize: Int = 24,
    @SerializedName("weight_source") val weightSource: Int = 12,
    @SerializedName("weight_seeders") val weightSeeders: Int = 13,
    @SerializedName("weight_promotion") val weightPromotion: Int = 3,
    @SerializedName("seeder_floor") val seederFloor: Int = 3,
)

data class AutomationPolicyDto(
    val enabled: Boolean,
    @SerializedName("dry_run") val dryRun: Boolean,
    @SerializedName("auto_identify") val autoIdentify: Boolean,
    @SerializedName("scope_mode") val scopeMode: AutomationScopeMode,
    val regions: List<String>,
    @SerializedName("selected_media_ids") val selectedMediaIds: List<String>,
    @SerializedName("site_ids") val siteIds: List<String>,
    @SerializedName("media_types") val mediaTypes: List<MediaType>,
    @SerializedName("minimum_score") val minimumScore: Double,
    @SerializedName("minimum_seeders") val minimumSeeders: Int,
    @SerializedName("max_size_bytes") val maxSizeBytes: Long?,
    @SerializedName("allow_warnings") val allowWarnings: Boolean,
    @SerializedName("interval_minutes") val intervalMinutes: Int,
    @SerializedName("retry_delay_minutes") val retryDelayMinutes: Int,
    @SerializedName("max_attempts") val maxAttempts: Int,
    @SerializedName("daily_download_limit") val dailyDownloadLimit: Int,
    @SerializedName("daily_download_bytes") val dailyDownloadBytes: Long?,
    @SerializedName("cooldown_tier_1_hours") val cooldownTier1Hours: Int = 24,
    @SerializedName("cooldown_tier_2_hours") val cooldownTier2Hours: Int = 72,
    @SerializedName("cooldown_tier_3_hours") val cooldownTier3Hours: Int = 168,
    @SerializedName("variety_recent_episodes") val varietyRecentEpisodes: Int = 5,
    @SerializedName("automate_variety") val automateVariety: Boolean = false,
    @SerializedName("weight_resolution") val weightResolution: Int = 44,
    @SerializedName("weight_size") val weightSize: Int = 24,
    @SerializedName("weight_source") val weightSource: Int = 12,
    @SerializedName("weight_seeders") val weightSeeders: Int = 13,
    @SerializedName("weight_promotion") val weightPromotion: Int = 3,
    @SerializedName("seeder_floor") val seederFloor: Int = 3,
    @SerializedName("updated_at") val updatedAt: String,
    @SerializedName("last_run_at") val lastRunAt: String?,
)

/**
 * The policy as it must be sent back when saving.
 *
 * `PUT /api/automation/policy` is a whole-document write for every field the
 * body mentions, so every setting the app does not edit still has to be echoed
 * back. Leaving one out used to send this DTO's default in its place, silently
 * resetting whatever the operator had configured elsewhere -- which is why this
 * lives beside the DTOs with a test, rather than inline in the ViewModel.
 */
fun AutomationPolicyDto.toUpdate(): AutomationPolicyUpdateDto = AutomationPolicyUpdateDto(
    enabled = enabled,
    dryRun = dryRun,
    autoIdentify = autoIdentify,
    scopeMode = scopeMode,
    regions = regions,
    selectedMediaIds = selectedMediaIds,
    siteIds = siteIds,
    mediaTypes = mediaTypes,
    minimumScore = minimumScore,
    minimumSeeders = minimumSeeders,
    maxSizeBytes = maxSizeBytes,
    allowWarnings = allowWarnings,
    intervalMinutes = intervalMinutes,
    retryDelayMinutes = retryDelayMinutes,
    maxAttempts = maxAttempts,
    dailyDownloadLimit = dailyDownloadLimit,
    dailyDownloadBytes = dailyDownloadBytes,
    cooldownTier1Hours = cooldownTier1Hours,
    cooldownTier2Hours = cooldownTier2Hours,
    cooldownTier3Hours = cooldownTier3Hours,
    varietyRecentEpisodes = varietyRecentEpisodes,
    automateVariety = automateVariety,
    weightResolution = weightResolution,
    weightSize = weightSize,
    weightSource = weightSource,
    weightSeeders = weightSeeders,
    weightPromotion = weightPromotion,
    seederFloor = seederFloor,
)

enum class AutomationScopeMode {
    @SerializedName("filters") FILTERS,
    @SerializedName("selected") SELECTED,
}

enum class AutomationJobState {
    PENDING,
    RUNNING,
    RETRY_WAIT,
    SUCCEEDED,
    FAILED,
}

data class AutomationJobDto(
    val id: String,
    @SerializedName("run_id") val runId: String,
    @SerializedName("media_id") val mediaId: String,
    @SerializedName("media_title") val mediaTitle: String,
    val state: AutomationJobState,
    @SerializedName("search_id") val searchId: String?,
    @SerializedName("selected_candidate_id") val selectedCandidateId: String?,
    @SerializedName("download_id") val downloadId: String?,
    @SerializedName("retry_of_job_id") val retryOfJobId: String? = null,
    val trigger: String,
    @SerializedName("attempt_count") val attemptCount: Int,
    @SerializedName("next_attempt_at") val nextAttemptAt: String?,
    val decision: Map<String, Any?>,
    @SerializedName("error_code") val errorCode: String? = null,
    @SerializedName("error_message") val errorMessage: String?,
    @SerializedName("created_at") val createdAt: String,
    @SerializedName("finished_at") val finishedAt: String?,
    @SerializedName("superseded_at") val supersededAt: String? = null,
)

data class AutomationJobPageDto(
    val items: List<AutomationJobDto>,
    val total: Int,
    val page: Int,
    @SerializedName("page_size") val pageSize: Int,
)

enum class AutomationRunStateDto {
    PENDING,
    RUNNING,
    SUCCEEDED,
    FAILED,
}

data class AutomationRunDto(
    val id: String,
    val trigger: String,
    val state: AutomationRunStateDto,
    val created: Int,
    val succeeded: Int,
    val failed: Int,
    val deferred: Int,
    @SerializedName("error_message") val errorMessage: String?,
    @SerializedName("created_at") val createdAt: String,
    @SerializedName("started_at") val startedAt: String?,
    @SerializedName("finished_at") val finishedAt: String?,
)
