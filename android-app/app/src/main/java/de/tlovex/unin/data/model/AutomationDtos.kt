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
    @SerializedName("updated_at") val updatedAt: String,
    @SerializedName("last_run_at") val lastRunAt: String?,
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
    val trigger: String,
    @SerializedName("attempt_count") val attemptCount: Int,
    @SerializedName("next_attempt_at") val nextAttemptAt: String?,
    val decision: Map<String, Any?>,
    @SerializedName("error_message") val errorMessage: String?,
    @SerializedName("created_at") val createdAt: String,
    @SerializedName("finished_at") val finishedAt: String?,
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
