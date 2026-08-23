package de.tlovex.unin.data.repository

import de.tlovex.unin.data.model.AutomationJobDto
import de.tlovex.unin.data.model.AutomationJobPageDto
import de.tlovex.unin.data.model.AutomationPolicyDto
import de.tlovex.unin.data.model.AutomationPolicyUpdateDto
import de.tlovex.unin.data.model.AutomationRunResultDto
import de.tlovex.unin.data.model.CredentialsDto
import de.tlovex.unin.data.model.ConfigurationStatusDto
import de.tlovex.unin.data.model.ConnectionTestResultDto
import de.tlovex.unin.data.model.DownloadDto
import de.tlovex.unin.data.model.DownloadPageDto
import de.tlovex.unin.data.model.DownloadRequestDto
import de.tlovex.unin.data.model.DownloadState
import de.tlovex.unin.data.model.IdentityRequestDto
import de.tlovex.unin.data.model.LoginDto
import de.tlovex.unin.data.model.MediaDetailDto
import de.tlovex.unin.data.model.MediaPageDto
import de.tlovex.unin.data.model.MediaState
import de.tlovex.unin.data.model.MediaSummaryDto
import de.tlovex.unin.data.model.MediaType
import de.tlovex.unin.data.model.PrincipalDto
import de.tlovex.unin.data.model.PtSiteArchitecture
import de.tlovex.unin.data.model.SearchDetailDto
import de.tlovex.unin.data.model.SearchRequestDto
import de.tlovex.unin.data.model.SetupStatusDto
import de.tlovex.unin.data.model.SyncResultDto
import de.tlovex.unin.data.remote.UninApi
import de.tlovex.unin.data.security.SecureCookieJar
import retrofit2.HttpException

class UninRepository(
    private val api: UninApi,
    private val cookieJar: SecureCookieJar,
) {
    suspend fun setupStatus(): SetupStatusDto = api.setupStatus()

    suspend fun setup(username: String, password: String): LoginDto =
        api.setup(CredentialsDto(username, password))

    /**
     * Restores the persisted session if it remains valid. A missing or expired session is a normal
     * signed-out state and is returned as null; transport and server failures remain visible.
     */
    suspend fun bootstrapSession(): PrincipalDto? {
        api.csrf()
        return try {
            api.me()
        } catch (error: HttpException) {
            if (error.code() == 401) {
                cookieJar.clear()
                null
            } else {
                throw error
            }
        }
    }

    suspend fun login(username: String, password: String): LoginDto {
        cookieJar.clear()
        api.csrf()
        return api.login(CredentialsDto(username, password))
    }

    suspend fun currentUser(): PrincipalDto = api.me()

    suspend fun logout() {
        try {
            api.logout()
        } finally {
            cookieJar.clear()
        }
    }

    fun clearAuth() = cookieJar.clear()

    suspend fun configurationStatus(): ConfigurationStatusDto = api.configuration()

    suspend fun testNextFindConnection(): ConnectionTestResultDto =
        api.testNextFindConnection()

    suspend fun testTmdbConnection(): ConnectionTestResultDto = api.testTmdbConnection()

    suspend fun testQbittorrentConnection(): ConnectionTestResultDto =
        api.testQbittorrentConnection()

    suspend fun testPtSiteConnection(
        architecture: PtSiteArchitecture,
    ): ConnectionTestResultDto = api.testPtSiteConnection(architecture)

    suspend fun library(
        state: MediaState? = null,
        mediaType: MediaType? = null,
        region: String? = null,
        year: Int? = null,
        query: String? = null,
        page: Int = 1,
        pageSize: Int = 30,
    ): MediaPageDto = api.library(
        state = state,
        mediaType = mediaType,
        region = region,
        year = year,
        query = query,
        page = page,
        pageSize = pageSize,
    )

    suspend fun syncLibrary(): SyncResultDto = api.syncLibrary()

    suspend fun mediaDetail(mediaId: String): MediaDetailDto = api.mediaDetail(mediaId)

    suspend fun identifyMedia(mediaId: String, tmdbId: Int? = null): MediaSummaryDto =
        api.identifyMedia(mediaId, IdentityRequestDto(tmdbId))

    suspend fun searchMedia(mediaId: String, siteIds: List<String>): SearchDetailDto =
        api.createSearch(mediaId, SearchRequestDto(siteIds))

    suspend fun search(searchId: String): SearchDetailDto = api.search(searchId)

    suspend fun downloadCandidate(
        candidateId: String,
        confirmWarnings: Boolean = false,
    ): DownloadDto = api.downloadCandidate(candidateId, DownloadRequestDto(confirmWarnings))

    suspend fun downloads(
        state: DownloadState? = null,
        page: Int = 1,
        pageSize: Int = 30,
    ): DownloadPageDto = api.downloads(state, page, pageSize)

    suspend fun retryDownload(downloadId: String): DownloadDto = api.retryDownload(downloadId)

    suspend fun syncDownloads(): SyncResultDto = api.syncDownloads()

    suspend fun automationPolicy(): AutomationPolicyDto = api.automationPolicy()

    suspend fun updateAutomationPolicy(
        policy: AutomationPolicyUpdateDto,
    ): AutomationPolicyDto = api.updateAutomationPolicy(policy)

    suspend fun automationJobs(page: Int = 1, pageSize: Int = 30): AutomationJobPageDto =
        api.automationJobs(page, pageSize)

    suspend fun runAutomation(): AutomationRunResultDto = api.runAutomation()

    suspend fun retryAutomationJob(jobId: String): AutomationJobDto =
        api.retryAutomationJob(jobId)
}
