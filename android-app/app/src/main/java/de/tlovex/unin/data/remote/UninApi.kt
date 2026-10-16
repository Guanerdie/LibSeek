package de.tlovex.unin.data.remote

import de.tlovex.unin.data.model.ActivityCategory
import de.tlovex.unin.data.model.ActivityPageDto
import de.tlovex.unin.data.model.AutomationJobDto
import de.tlovex.unin.data.model.CleanupHoldBulkResultDto
import de.tlovex.unin.data.model.CleanupHoldBulkUpdateDto
import de.tlovex.unin.data.model.CleanupHoldUpdateDto
import de.tlovex.unin.data.model.CleanupPolicyUpdateDto
import de.tlovex.unin.data.model.CleanupPreviewDto
import de.tlovex.unin.data.model.ImportBatchDto
import de.tlovex.unin.data.model.ImportMatchRequestDto
import de.tlovex.unin.data.model.ImportMatchResultDto
import de.tlovex.unin.data.model.ImportStartRequestDto
import de.tlovex.unin.data.model.AutomationJobPageDto
import de.tlovex.unin.data.model.AutomationPolicyDto
import de.tlovex.unin.data.model.AutomationPolicyUpdateDto
import de.tlovex.unin.data.model.AutomationRunDto
import de.tlovex.unin.data.model.ConfigurationStatusDto
import de.tlovex.unin.data.model.ConfigurationUpdateRequestDto
import de.tlovex.unin.data.model.ConnectionTestResultDto
import de.tlovex.unin.data.model.CredentialsDto
import de.tlovex.unin.data.model.CsrfDto
import de.tlovex.unin.data.model.DownloadDto
import de.tlovex.unin.data.model.DownloadPageDto
import de.tlovex.unin.data.model.DownloadRequestDto
import de.tlovex.unin.data.model.DownloadState
import de.tlovex.unin.data.model.IdentityRequestDto
import de.tlovex.unin.data.model.LoginDto
import de.tlovex.unin.data.model.LoginRequestDto
import de.tlovex.unin.data.model.PasswordChangeRequestDto
import de.tlovex.unin.data.model.MediaDetailDto
import de.tlovex.unin.data.model.MediaPageDto
import de.tlovex.unin.data.model.MediaState
import de.tlovex.unin.data.model.MediaSummaryDto
import de.tlovex.unin.data.model.MediaType
import de.tlovex.unin.data.model.PrincipalDto
import de.tlovex.unin.data.model.PtSiteArchitecture
import de.tlovex.unin.data.model.QuickFillRequestDto
import de.tlovex.unin.data.model.QuickFillResultDto
import de.tlovex.unin.data.model.SubscriptionUpdateDto
import de.tlovex.unin.data.model.SearchDetailDto
import de.tlovex.unin.data.model.SearchRequestDto
import de.tlovex.unin.data.model.SetupStatusDto
import de.tlovex.unin.data.model.StatsDto
import de.tlovex.unin.data.model.SyncResultDto
import retrofit2.Response
import retrofit2.http.Body
import retrofit2.http.GET
import retrofit2.http.POST
import retrofit2.http.PUT
import retrofit2.http.Path
import retrofit2.http.Query

interface UninApi {
    @GET("api/auth/setup-status")
    suspend fun setupStatus(): SetupStatusDto

    @POST("api/auth/setup")
    suspend fun setup(@Body credentials: CredentialsDto): LoginDto

    @GET("api/auth/csrf")
    suspend fun csrf(): CsrfDto

    @POST("api/auth/login")
    suspend fun login(@Body credentials: LoginRequestDto): LoginDto

    @POST("api/auth/password")
    suspend fun changePassword(@Body request: PasswordChangeRequestDto): LoginDto

    @GET("api/auth/me")
    suspend fun me(): PrincipalDto

    @POST("api/auth/logout")
    suspend fun logout(): Response<Unit>

    @GET("api/configuration")
    suspend fun configuration(): ConfigurationStatusDto

    @PUT("api/configuration")
    suspend fun updateConfiguration(
        @Body request: ConfigurationUpdateRequestDto,
    ): ConfigurationStatusDto

    @POST("api/configuration/tests/nextfind")
    suspend fun testNextFindConnection(): ConnectionTestResultDto

    @POST("api/configuration/tests/tmdb")
    suspend fun testTmdbConnection(): ConnectionTestResultDto

    @POST("api/configuration/tests/outbound-proxy")
    suspend fun testOutboundProxyConnection(): ConnectionTestResultDto

    @POST("api/configuration/tests/qbittorrent")
    suspend fun testQbittorrentConnection(): ConnectionTestResultDto

    @POST("api/configuration/tests/pt-sites/{architecture}")
    suspend fun testPtSiteConnection(
        @Path("architecture") architecture: PtSiteArchitecture,
    ): ConnectionTestResultDto

    @GET("api/library")
    suspend fun library(
        @Query("state") state: MediaState? = null,
        @Query("media_type") mediaType: MediaType? = null,
        @Query("region") region: String? = null,
        @Query("year") year: Int? = null,
        @Query("query") query: String? = null,
        @Query("page") page: Int = 1,
        @Query("page_size") pageSize: Int = 30,
    ): MediaPageDto

    @POST("api/library/sync")
    suspend fun syncLibrary(): SyncResultDto

    @GET("api/library/{mediaId}")
    suspend fun mediaDetail(@Path("mediaId") mediaId: String): MediaDetailDto

    @POST("api/library/{mediaId}/identify")
    suspend fun identifyMedia(
        @Path("mediaId") mediaId: String,
        @Body request: IdentityRequestDto,
    ): MediaSummaryDto

    @PUT("api/library/{mediaId}/subscription")
    suspend fun setSubscription(
        @Path("mediaId") mediaId: String,
        @Body request: SubscriptionUpdateDto,
    ): MediaDetailDto

    @POST("api/library/{mediaId}/quick-fill")
    suspend fun quickFill(
        @Path("mediaId") mediaId: String,
        @Body request: QuickFillRequestDto,
    ): QuickFillResultDto

    @POST("api/library/{mediaId}/searches")
    suspend fun createSearch(
        @Path("mediaId") mediaId: String,
        @Body request: SearchRequestDto,
    ): SearchDetailDto

    @GET("api/searches/{searchId}")
    suspend fun search(@Path("searchId") searchId: String): SearchDetailDto

    @POST("api/candidates/{candidateId}/download")
    suspend fun downloadCandidate(
        @Path("candidateId") candidateId: String,
        @Body request: DownloadRequestDto,
    ): DownloadDto

    @GET("api/downloads")
    suspend fun downloads(
        @Query("state") state: DownloadState? = null,
        @Query("page") page: Int = 1,
        @Query("page_size") pageSize: Int = 30,
    ): DownloadPageDto

    @POST("api/downloads/{downloadId}/retry")
    suspend fun retryDownload(@Path("downloadId") downloadId: String): DownloadDto

    @POST("api/downloads/sync")
    suspend fun syncDownloads(): SyncResultDto

    @GET("api/automation/policy")
    suspend fun automationPolicy(): AutomationPolicyDto

    @PUT("api/automation/policy")
    suspend fun updateAutomationPolicy(
        @Body policy: AutomationPolicyUpdateDto,
    ): AutomationPolicyDto

    @GET("api/automation/jobs")
    suspend fun automationJobs(
        @Query("page") page: Int = 1,
        @Query("page_size") pageSize: Int = 30,
    ): AutomationJobPageDto

    @POST("api/automation/runs")
    suspend fun runAutomation(): AutomationRunDto

    @GET("api/automation/runs/latest")
    suspend fun latestAutomationRun(): Response<AutomationRunDto>

    @GET("api/automation/runs/{runId}")
    suspend fun automationRun(@Path("runId") runId: String): AutomationRunDto

    @POST("api/automation/jobs/{jobId}/retry")
    suspend fun retryAutomationJob(@Path("jobId") jobId: String): AutomationJobDto

    @GET("api/stats")
    suspend fun stats(): StatsDto

    @GET("api/downloads/cleanup-preview")
    suspend fun cleanupPreview(): CleanupPreviewDto

    @PUT("api/downloads/{downloadId}/cleanup-hold")
    suspend fun setCleanupHold(
        @Path("downloadId") downloadId: String,
        @Body request: CleanupHoldUpdateDto,
    ): DownloadDto

    @POST("api/downloads/cleanup-hold")
    suspend fun setCleanupHolds(@Body request: CleanupHoldBulkUpdateDto): CleanupHoldBulkResultDto

    @PUT("api/automation/policy")
    suspend fun updateCleanupPolicy(@Body policy: CleanupPolicyUpdateDto): AutomationPolicyDto

    @GET("api/activity")
    suspend fun activity(
        @Query("category") category: ActivityCategory? = null,
        @Query("trigger") trigger: String? = null,
        @Query("query") query: String? = null,
        @Query("page") page: Int = 1,
        @Query("page_size") pageSize: Int = 30,
    ): ActivityPageDto

    @POST("api/library/import/match")
    suspend fun matchImport(@Body request: ImportMatchRequestDto): ImportMatchResultDto

    @POST("api/library/import")
    suspend fun startImport(@Body request: ImportStartRequestDto): ImportBatchDto

    @GET("api/library/import")
    suspend fun importStatus(): ImportBatchDto

    @POST("api/library/import/cancel")
    suspend fun cancelImport(): ImportBatchDto
}
