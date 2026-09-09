package de.tlovex.unin.data.repository

import de.tlovex.unin.data.model.AutomationJobDto
import de.tlovex.unin.data.model.AutomationJobPageDto
import de.tlovex.unin.data.model.AutomationPolicyDto
import de.tlovex.unin.data.model.AutomationPolicyUpdateDto
import de.tlovex.unin.data.model.AutomationRunDto
import de.tlovex.unin.data.model.AvistaZConfigurationUpdateDto
import de.tlovex.unin.data.model.ConfigurationStatusDto
import de.tlovex.unin.data.model.ConfigurationUpdateRequestDto
import de.tlovex.unin.data.model.ConnectionTestResultDto
import de.tlovex.unin.data.model.CredentialsDto
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
import de.tlovex.unin.data.model.NextFindConfigurationUpdateDto
import de.tlovex.unin.data.model.NexusPhpConfigurationUpdateDto
import de.tlovex.unin.data.model.OutboundProxyConfigurationUpdateDto
import de.tlovex.unin.data.model.PrincipalDto
import de.tlovex.unin.data.model.PtSiteArchitecture
import de.tlovex.unin.data.model.QbittorrentConfigurationUpdateDto
import de.tlovex.unin.data.model.QuickFillRequestDto
import de.tlovex.unin.data.model.QuickFillResultDto
import de.tlovex.unin.data.model.SubscriptionUpdateDto
import de.tlovex.unin.data.model.SearchDetailDto
import de.tlovex.unin.data.model.SearchRequestDto
import de.tlovex.unin.data.model.SetupStatusDto
import de.tlovex.unin.data.model.StatsDto
import de.tlovex.unin.data.model.SyncResultDto
import de.tlovex.unin.data.model.TmdbConfigurationUpdateDto
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

    suspend fun login(username: String, password: String, remember: Boolean = false): LoginDto {
        cookieJar.clear()
        api.csrf()
        return api.login(LoginRequestDto(username, password, remember))
    }

    /**
     * Replaces the account password and returns the reissued session.
     *
     * The server rotates its signing key, which invalidates this device's cookie
     * along with every other one; the response carries a fresh session that the
     * cookie jar stores, so the caller stays signed in here and nowhere else.
     */
    suspend fun changePassword(currentPassword: String, newPassword: String): LoginDto =
        api.changePassword(PasswordChangeRequestDto(currentPassword, newPassword))

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

    suspend fun updateConfiguration(
        request: ConfigurationUpdateRequestDto,
    ): ConfigurationStatusDto = api.updateConfiguration(request)

    suspend fun updateNextFindConfiguration(
        baseUrl: String,
        username: String,
        password: String? = null,
    ): ConfigurationStatusDto = updateConfiguration(
        ConfigurationUpdateRequestDto(
            nextfind = NextFindConfigurationUpdateDto(baseUrl, username, password),
        ),
    )

    suspend fun updateTmdbConfiguration(token: String?): ConfigurationStatusDto =
        updateConfiguration(
            ConfigurationUpdateRequestDto(tmdb = TmdbConfigurationUpdateDto(token)),
        )

    suspend fun updateOutboundProxyConfiguration(
        url: String,
        username: String,
        password: String? = null,
    ): ConfigurationStatusDto = updateConfiguration(
        ConfigurationUpdateRequestDto(
            outboundProxy = OutboundProxyConfigurationUpdateDto(url, username, password),
        ),
    )

    suspend fun updateQbittorrentConfiguration(
        url: String,
        username: String,
        savePath: String,
        category: String,
        allowInsecureHttp: Boolean,
        password: String? = null,
    ): ConfigurationStatusDto = updateConfiguration(
        ConfigurationUpdateRequestDto(
            qbittorrent = QbittorrentConfigurationUpdateDto(
                url = url,
                username = username,
                password = password,
                savePath = savePath,
                category = category,
                allowInsecureHttp = allowInsecureHttp,
            ),
        ),
    )

    suspend fun updateAvistaZConfiguration(
        baseUrl: String,
        username: String,
        password: String? = null,
        pid: String? = null,
    ): ConfigurationStatusDto = updateConfiguration(
        ConfigurationUpdateRequestDto(
            ptSite = AvistaZConfigurationUpdateDto(
                baseUrl = baseUrl,
                username = username,
                password = password,
                pid = pid,
            ),
        ),
    )

    suspend fun updateNexusPhpConfiguration(
        siteId: String,
        displayName: String,
        baseUrl: String,
        cookie: String? = null,
        passkey: String? = null,
    ): ConfigurationStatusDto = updateConfiguration(
        ConfigurationUpdateRequestDto(
            ptSite = NexusPhpConfigurationUpdateDto(
                siteId = siteId,
                displayName = displayName,
                baseUrl = baseUrl,
                cookie = cookie,
                passkey = passkey,
            ),
        ),
    )

    suspend fun testNextFindConnection(): ConnectionTestResultDto =
        api.testNextFindConnection()

    suspend fun testTmdbConnection(): ConnectionTestResultDto = api.testTmdbConnection()

    suspend fun testOutboundProxyConnection(): ConnectionTestResultDto =
        api.testOutboundProxyConnection()

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

    /**
     * Adds or removes one media item from the automation scope.
     *
     * The scope list already existed on the policy; this reaches it the way
     * people think about it -- "follow this show" -- rather than through a
     * multi-select holding the whole library. It deliberately does not switch
     * the policy's scope mode; the response reports whether the subscription is
     * actually in effect.
     */
    suspend fun setSubscription(mediaId: String, subscribed: Boolean): MediaDetailDto =
        api.setSubscription(mediaId, SubscriptionUpdateDto(subscribed))

    suspend fun quickFill(mediaId: String, force: Boolean = false): QuickFillResultDto =
        api.quickFill(mediaId, QuickFillRequestDto(force))

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

    suspend fun runAutomation(): AutomationRunDto = api.runAutomation()

    suspend fun latestAutomationRun(): AutomationRunDto? {
        val response = api.latestAutomationRun()
        if (!response.isSuccessful) throw HttpException(response)
        return response.body()
    }

    suspend fun automationRun(runId: String): AutomationRunDto = api.automationRun(runId)

    suspend fun retryAutomationJob(jobId: String): AutomationJobDto =
        api.retryAutomationJob(jobId)

    suspend fun stats(): StatsDto = api.stats()
}
