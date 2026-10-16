package de.tlovex.unin.data.model

import com.google.gson.annotations.SerializedName

/*
 * Space reclaim, the activity record and bulk import: the parts of the web
 * console that arrived after the app's last update. Fields a server of that
 * age may leave out are nullable, because Gson fills a missing field with
 * null whatever the Kotlin type says.
 */

enum class DownloadCleanupState {
    NONE,
    MARKED,
    DELETED,
    VANISHED,
    HELD,
}

data class CleanupPreviewEntryDto(
    @SerializedName("download_id") val downloadId: String,
    @SerializedName("media_title") val mediaTitle: String,
    val name: String,
    @SerializedName("size_bytes") val sizeBytes: Long,
    @SerializedName("completed_at") val completedAt: String?,
    @SerializedName("seeding_days") val seedingDays: Double,
    @SerializedName("required_seeding_days") val requiredSeedingDays: Int,
    @SerializedName("cleanup_state") val cleanupState: DownloadCleanupState,
    @SerializedName("deletes_at") val deletesAt: String?,
    @SerializedName("blocked_reason") val blockedReason: String?,
    // BACKLOG, MANUAL or TAG_REMOVED; null unless the download is held.
    @SerializedName("hold_reason") val holdReason: String?,
)

data class CleanupPreviewDto(
    val enabled: Boolean,
    @SerializedName("dry_run") val dryRun: Boolean,
    @SerializedName("delete_authorized") val deleteAuthorized: Boolean,
    @SerializedName("reclaimable_bytes") val reclaimableBytes: Long,
    @SerializedName("held_reclaimable_bytes") val heldReclaimableBytes: Long?,
    val items: List<CleanupPreviewEntryDto>,
)

data class CleanupHoldUpdateDto(val held: Boolean)

data class CleanupHoldBulkUpdateDto(
    @SerializedName("download_ids") val downloadIds: List<String>,
    val held: Boolean,
)

data class CleanupHoldSkippedDto(
    @SerializedName("download_id") val downloadId: String,
    val reason: String,
)

data class CleanupHoldBulkResultDto(
    val changed: Int,
    val skipped: List<CleanupHoldSkippedDto>?,
)

/**
 * Only the space-reclaim half of the policy.
 *
 * The server applies the fields a request carries and leaves the rest, so
 * saving this cannot touch the search settings, and the automation screen's
 * save cannot touch these.
 */
data class CleanupPolicyUpdateDto(
    @SerializedName("cleanup_enabled") val enabled: Boolean,
    @SerializedName("cleanup_dry_run") val dryRun: Boolean,
    @SerializedName("cleanup_after_days") val afterDays: Int,
    @SerializedName("cleanup_min_seeding_days") val minSeedingDays: Int,
    @SerializedName("cleanup_grace_days") val graceDays: Int,
    @SerializedName("cleanup_require_library_confirmed") val requireLibraryConfirmed: Boolean,
    @SerializedName("cleanup_daily_limit") val dailyLimit: Int,
    @SerializedName("cleanup_release_backlog") val releaseBacklog: Boolean,
)

enum class ActivityCategory(val wireName: String) {
    @SerializedName("download") DOWNLOAD("download"),
    @SerializedName("cleanup") CLEANUP("cleanup"),
    @SerializedName("library") LIBRARY("library"),
    @SerializedName("search") SEARCH("search"),
    @SerializedName("settings") SETTINGS("settings"),
    ;

    // Retrofit writes a query enum with toString(); the server wants lower case.
    override fun toString(): String = wireName
}

data class ActivityDto(
    val id: String,
    @SerializedName("created_at") val createdAt: String,
    val event: String,
    val category: ActivityCategory?,
    val message: String,
    // Null on rows written before the actor was recorded.
    val actor: String?,
    // MANUAL or AUTO; null on old rows.
    val trigger: String?,
    val reason: String?,
    val details: Map<String, Any?>?,
    @SerializedName("media_id") val mediaId: String?,
    @SerializedName("media_title") val mediaTitle: String?,
)

data class ActivityPageDto(
    val items: List<ActivityDto>,
    val total: Int,
    val page: Int,
    @SerializedName("page_size") val pageSize: Int,
)

data class ImportMatchRequestDto(val text: String)

data class ImportMediaChoiceDto(
    @SerializedName("media_id") val mediaId: String,
    val title: String,
    @SerializedName("original_title") val originalTitle: String?,
    val year: Int?,
    @SerializedName("media_type") val mediaType: MediaType,
    @SerializedName("tmdb_id") val tmdbId: Int?,
    @SerializedName("in_library") val inLibrary: Boolean?,
)

enum class ImportLineStatus {
    MATCHED,
    AMBIGUOUS,
    NOT_MISSING,
    DOWNLOADING,
    NOT_FOUND,
    DUPLICATE,
}

data class ImportMatchLineDto(
    val raw: String,
    val status: ImportLineStatus,
    val media: ImportMediaChoiceDto?,
    val choices: List<ImportMediaChoiceDto>?,
    val note: String?,
)

data class ImportMatchResultDto(
    val lines: List<ImportMatchLineDto>,
    val truncated: Boolean?,
)

data class ImportStartRequestDto(
    @SerializedName("media_ids") val mediaIds: List<String>,
    // The ones already in the library that were asked for anyway; the server
    // refuses any other title that is not missing.
    @SerializedName("existing_ids") val existingIds: List<String>,
)

enum class ImportOutcome {
    PENDING,
    RUNNING,
    DOWNLOADED,
    ALREADY_PRESENT,
    NO_CANDIDATE,
    FAILED,
    CANCELLED,
}

data class ImportBatchItemDto(
    @SerializedName("media_id") val mediaId: String,
    val title: String,
    val outcome: ImportOutcome,
    val message: String?,
    @SerializedName("selected_title") val selectedTitle: String?,
    @SerializedName("download_id") val downloadId: String?,
    @SerializedName("in_library") val inLibrary: Boolean?,
)

enum class ImportBatchState {
    IDLE,
    RUNNING,
    FINISHED,
    CANCELLED,
}

data class ImportBatchDto(
    val id: String,
    val state: ImportBatchState,
    @SerializedName("started_by") val startedBy: String?,
    @SerializedName("started_at") val startedAt: String?,
    @SerializedName("finished_at") val finishedAt: String?,
    @SerializedName("cancel_requested") val cancelRequested: Boolean?,
    val items: List<ImportBatchItemDto>?,
)
