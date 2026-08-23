package de.tlovex.unin.data.model

import com.google.gson.annotations.SerializedName

enum class DownloadState {
    SUBMITTING,
    QUEUED,
    DOWNLOADING,
    PAUSED,
    SEEDING,
    COMPLETED,
    ERROR,
    OUTCOME_UNKNOWN,
}

data class DownloadRequestDto(
    @SerializedName("confirm_warnings") val confirmWarnings: Boolean = false,
)

data class DownloadDto(
    val id: String,
    @SerializedName("media_id") val mediaId: String,
    @SerializedName("candidate_id") val candidateId: String,
    @SerializedName("info_hash") val infoHash: String?,
    val name: String,
    val state: DownloadState,
    val progress: Double,
    @SerializedName("download_speed") val downloadSpeed: Long,
    @SerializedName("upload_speed") val uploadSpeed: Long,
    val ratio: Double,
    @SerializedName("error_message") val errorMessage: String?,
    @SerializedName("created_at") val createdAt: String,
    @SerializedName("updated_at") val updatedAt: String,
)

data class DownloadPageDto(
    val items: List<DownloadDto>,
    val total: Int,
    val page: Int,
    @SerializedName("page_size") val pageSize: Int,
)
