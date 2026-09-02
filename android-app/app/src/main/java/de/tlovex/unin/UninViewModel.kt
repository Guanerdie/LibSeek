package de.tlovex.unin

import android.app.Application
import android.content.Context
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import de.tlovex.unin.data.UninDataLayer
import de.tlovex.unin.data.model.AuthRole
import de.tlovex.unin.data.model.ApiErrorPolicy
import de.tlovex.unin.data.model.AutomationJobDto
import de.tlovex.unin.data.model.AutomationJobState
import de.tlovex.unin.data.model.AutomationPolicyDto
import de.tlovex.unin.data.model.AutomationPolicyUpdateDto
import de.tlovex.unin.data.model.AutomationRunDto
import de.tlovex.unin.data.model.AutomationRunStateDto
import de.tlovex.unin.data.model.AutomationScopeMode
import de.tlovex.unin.data.model.CandidateDto
import de.tlovex.unin.data.model.ConfigurationStatusDto
import de.tlovex.unin.data.model.DownloadDto
import de.tlovex.unin.data.model.EpisodeState
import de.tlovex.unin.data.model.MediaDetailDto
import de.tlovex.unin.data.model.MediaState
import de.tlovex.unin.data.model.MediaSummaryDto
import de.tlovex.unin.data.model.MediaType
import de.tlovex.unin.data.model.PrincipalDto
import de.tlovex.unin.data.model.PtSiteArchitecture
import de.tlovex.unin.data.model.SearchDetailDto
import de.tlovex.unin.data.model.SearchState
import de.tlovex.unin.data.remote.peekErrorBody
import de.tlovex.unin.data.repository.UninRepository
import de.tlovex.unin.ui.AutomationPolicyUi
import de.tlovex.unin.ui.AutomationRunState
import de.tlovex.unin.ui.AutomationRunUi
import de.tlovex.unin.ui.ConnectionState
import de.tlovex.unin.ui.ConnectionUi
import de.tlovex.unin.ui.DownloadItemUi
import de.tlovex.unin.ui.DownloadState as UiDownloadState
import de.tlovex.unin.ui.MediaKind
import de.tlovex.unin.ui.MissingMediaUi
import de.tlovex.unin.ui.ResourceCandidateUi
import de.tlovex.unin.ui.UninCallbacks
import de.tlovex.unin.ui.UninDestination
import de.tlovex.unin.ui.UninUiState
import de.tlovex.unin.ui.screens.AvistaZConfigurationUi
import de.tlovex.unin.ui.screens.NextFindConfigurationUi
import de.tlovex.unin.ui.screens.NexusPhpConfigurationUi
import de.tlovex.unin.ui.screens.OutboundProxyConfigurationUi
import de.tlovex.unin.ui.screens.PtArchitectureUi
import de.tlovex.unin.ui.screens.QbittorrentConfigurationUi
import de.tlovex.unin.ui.screens.SettingsConfigurationCallbacks
import de.tlovex.unin.ui.screens.SettingsConfigurationUiState
import de.tlovex.unin.ui.screens.TmdbConfigurationUi
import java.io.IOException
import java.net.ConnectException
import java.net.URI
import java.net.UnknownHostException
import java.time.Instant
import java.time.LocalDateTime
import java.time.OffsetDateTime
import java.time.ZoneId
import java.time.format.DateTimeFormatter
import java.util.Locale
import javax.net.ssl.SSLException
import javax.net.ssl.SSLHandshakeException
import javax.net.ssl.SSLPeerUnverifiedException
import kotlin.coroutines.cancellation.CancellationException
import kotlin.math.roundToInt
import kotlinx.coroutines.Deferred
import kotlinx.coroutines.Job
import kotlinx.coroutines.async
import kotlinx.coroutines.awaitAll
import kotlinx.coroutines.coroutineScope
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import kotlinx.coroutines.supervisorScope
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import org.json.JSONObject
import retrofit2.HttpException

class UninViewModel(application: Application) : AndroidViewModel(application) {
    private val dataLayer = UninDataLayer.create(application, BuildConfig.BASE_URL)
    private val repository: UninRepository = dataLayer.repository
    private val safetyPreferences = application.getSharedPreferences(
        SAFETY_PREFERENCES_NAME,
        Context.MODE_PRIVATE,
    )
    private val serverLabel = runCatching { URI(BuildConfig.BASE_URL).host }
        .getOrNull()
        .orEmpty()
        .ifBlank { "unin.tlovex.de" }

    private val mutableUiState = MutableStateFlow(
        UninUiState(
            isBusy = true,
            serverLabel = serverLabel,
            connections = signedOutConnections(),
        ),
    )
    val uiState: StateFlow<UninUiState> = mutableUiState.asStateFlow()

    private val mutableIsInitialized = MutableStateFlow(false)
    val isInitialized: StateFlow<Boolean> = mutableIsInitialized.asStateFlow()

    private var activeOperations = 0
    private var currentRole: AuthRole? = null
    private var currentPolicy: AutomationPolicyDto? = null
    private var pendingPolicy: AutomationPolicyUpdateDto? = null
    private var hasUnknownAutomationRun = safetyPreferences.getBoolean(
        UNKNOWN_AUTOMATION_RUN_KEY,
        false,
    )
    private var unknownDownloadSubmissionMediaIds = safetyPreferences.getStringSet(
        UNKNOWN_DOWNLOAD_SUBMISSION_KEY,
        emptySet(),
    ).orEmpty().filterTo(mutableSetOf()) { it.isNotBlank() }.toSet()
    private var mediaDetailJob: Job? = null
    private var searchJob: Job? = null
    private var authenticationJob: Job? = null
    private var libraryLoadJob: Job? = null
    private var librarySyncJob: Job? = null
    private var downloadsSyncJob: Job? = null
    private val libraryOperationMutex = Mutex()
    private var automationRunPollJob: Job? = null
    private var automationRunPollId: String? = null
    private val authenticatedJobs = mutableSetOf<Job>()

    val callbacks = UninCallbacks(
        onUsernameChanged = ::updateUsername,
        onPasswordChanged = ::updatePassword,
        onLogin = ::login,
        onLogout = ::logout,
        onDestinationSelected = ::selectDestination,
        onSyncMissing = ::syncLibrary,
        onMediaSelected = ::openMedia,
        onConfirmTmdb = { media -> confirmTmdb(media) },
        onConfirmTmdbWithId = { media, tmdbId -> confirmTmdb(media, tmdbId) },
        onSearchResources = ::searchResources,
        onCandidateDownload = ::downloadCandidate,
        onRefreshDownloads = ::syncDownloads,
        onRetryDownload = ::retryDownload,
        onAutomationEnabledChanged = { value -> editPolicyUi(
            uiTransform = { it.copy(enabled = value) },
            dtoTransform = { it.copy(enabled = value) },
        ) },
        onDryRunChanged = { value -> editPolicyUi(
            uiTransform = { it.copy(dryRun = value) },
            dtoTransform = { it.copy(dryRun = value) },
        ) },
        onMinimumScoreChanged = { value ->
            val score = value.coerceIn(0, 100)
            editPolicyUi(
                uiTransform = { it.copy(minimumScore = score) },
                dtoTransform = { it.copy(minimumScore = score / 100.0) },
            )
        },
        onMinimumSeedersChanged = { value ->
            val seeders = value.coerceAtLeast(0)
            editPolicyUi(
                uiTransform = { it.copy(minimumSeeders = seeders) },
                dtoTransform = { it.copy(minimumSeeders = seeders) },
            )
        },
        onMaximumSizeChanged = { value ->
            val sizeGb = value.coerceAtLeast(1)
            editPolicyUi(
                uiTransform = { it.copy(maximumSizeGb = sizeGb) },
                dtoTransform = { it.copy(maxSizeBytes = sizeGb.toLong() * BYTES_PER_GIB) },
            )
        },
        onDailyLimitChanged = { value ->
            val limit = value.coerceIn(1, 100)
            editPolicyUi(
                uiTransform = { it.copy(dailyLimit = limit) },
                dtoTransform = { it.copy(dailyDownloadLimit = limit) },
            )
        },
        onSaveAutomationPolicy = ::saveAutomationPolicy,
        onRunAutomation = ::runAutomation,
        onRefreshAutomation = ::refreshAutomationStatus,
        onRetryAutomation = ::retryAutomation,
        onTestConnection = ::testConnection,
        settingsConfiguration = SettingsConfigurationCallbacks(
            onSaveNextFind = ::saveNextFindConfiguration,
            onSaveTmdb = ::saveTmdbConfiguration,
            onSaveOutboundProxy = ::saveOutboundProxyConfiguration,
            onSaveQbittorrent = ::saveQbittorrentConfiguration,
            onSaveAvistaZ = ::saveAvistaZConfiguration,
            onSaveNexusPhp = ::saveNexusPhpConfiguration,
        ),
        onDismissMessage = { mutableUiState.update { it.copy(snackbarMessage = null) } },
    )

    init {
        restoreSession()
    }

    fun navigateBack() {
        selectDestination(UninDestination.Missing)
    }

    private fun restoreSession() {
        lateinit var job: Job
        job = viewModelScope.launch {
            beginOperation()
            try {
                val principal = repository.bootstrapSession()
                if (principal == null) {
                    showSignedOut()
                } else {
                    showAuthenticated(principal)
                    loadInitialContent()
                }
            } catch (error: CancellationException) {
                throw error
            } catch (error: Throwable) {
                if (error.isSessionAuthenticationFailure()) {
                    expireSession("登录已过期，请重新登录")
                } else {
                    showSignedOut(errorMessage(error, "无法连接 UNIN 服务"))
                }
            } finally {
                endOperation()
                mutableIsInitialized.value = true
            }
        }
        authenticationJob = job
        job.invokeOnCompletion {
            if (authenticationJob === job) authenticationJob = null
        }
    }

    private fun updateUsername(value: String) {
        mutableUiState.update { it.copy(username = value, loginError = null) }
    }

    private fun updatePassword(value: String) {
        mutableUiState.update { it.copy(password = value, loginError = null) }
    }

    private fun login() {
        val username = uiState.value.username.trim()
        val password = uiState.value.password
        if (username.isEmpty() || password.isEmpty() || authenticationJob?.isActive == true) return

        lateinit var job: Job
        job = viewModelScope.launch {
            beginOperation()
            mutableUiState.update { it.copy(loginError = null) }
            try {
                val login = repository.login(username, password)
                showAuthenticated(PrincipalDto(login.username, login.role))
                loadInitialContent()
            } catch (error: CancellationException) {
                throw error
            } catch (error: Throwable) {
                repository.clearAuth()
                showSignedOut(errorMessage(error, "登录失败，请稍后重试"))
            } finally {
                endOperation()
            }
        }
        authenticationJob = job
        job.invokeOnCompletion {
            if (authenticationJob === job) authenticationJob = null
        }
    }

    private fun logout() {
        cancelAuthenticationJob()
        cancelAuthenticatedJobs()
        viewModelScope.launch {
            beginOperation()
            var warning: String? = null
            try {
                repository.logout()
            } catch (error: CancellationException) {
                throw error
            } catch (error: Throwable) {
                warning = "本地会话已清除；${errorMessage(error, "服务器暂时未响应")}"
            } finally {
                searchJob?.cancel()
                mediaDetailJob?.cancel()
                showSignedOut(warning)
                endOperation()
            }
        }
    }

    private suspend fun loadInitialContent() = supervisorScope {
        mutableUiState.update { it.copy(isLibraryLoading = true) }
        val libraryRequest = async {
            try {
                libraryOperationMutex.withLock {
                    val items = loadRecentLibrary()
                    mutableUiState.update { state ->
                        if (state.isAuthenticated) {
                            state.copy(missingMedia = items.toMissingMedia(state.missingMedia))
                        } else {
                            state
                        }
                    }
                }
            } finally {
                mutableUiState.update { it.copy(isLibraryLoading = false) }
            }
        }
        val downloadsRequest = async { loadAllDownloads() }
        val policyRequest = async { repository.automationPolicy() }
        val jobsRequest = async { loadAllAutomationJobs() }
        val latestRunRequest = async { repository.latestAutomationRun() }
        val configurationRequest = if (currentRole == AuthRole.ADMIN) {
            async { repository.configurationStatus() }
        } else {
            null
        }

        val libraryResult = libraryRequest.awaitResult()
        val downloadsResult = downloadsRequest.awaitResult()
        val policyResult = policyRequest.awaitResult()
        val jobsResult = jobsRequest.awaitResult()
        val latestRunResult = latestRunRequest.awaitResult()
        val configurationResult = configurationRequest?.awaitResult()
        val failures = listOfNotNull(
            libraryResult.exceptionOrNull(),
            downloadsResult.exceptionOrNull(),
            policyResult.exceptionOrNull(),
            jobsResult.exceptionOrNull(),
            latestRunResult.exceptionOrNull(),
            configurationResult?.exceptionOrNull(),
        )

        if (failures.any { it.isSessionAuthenticationFailure() }) {
            expireSession("登录已过期，请重新登录")
            return@supervisorScope
        }

        policyResult.getOrNull()?.let { policy ->
            currentPolicy = policy
            pendingPolicy = policy.toUpdate()
        }
        if (!uiState.value.isAuthenticated) return@supervisorScope

        mutableUiState.update { state ->
            state.copy(
                downloads = downloadsResult.getOrNull()?.map { it.toUi() }
                    ?: state.downloads,
                automationPolicy = policyResult.getOrNull()?.toUi()
                    ?: state.automationPolicy,
                automationRuns = jobsResult.getOrNull()?.map { it.toUi() }
                    ?: state.automationRuns,
                connections = configurationResult?.getOrNull()?.toConnections()
                    ?: authenticatedConnections(),
                settingsConfiguration = configurationResult?.getOrNull()?.toSettingsConfiguration()
                    ?: state.settingsConfiguration,
                snackbarMessage = failures.firstOrNull()?.let {
                    errorMessage(it, "部分数据暂时无法读取")
                },
            )
        }

        if (latestRunResult.isSuccess) {
            val latestRun = latestRunResult.getOrNull()
            if (latestRun == null) {
                clearUnknownAutomationRun()
                mutableUiState.update {
                    it.copy(
                        isAutomationRunInProgress = false,
                        automationRunOutcomeUnknown = false,
                    )
                }
            } else if (latestRun.isActive) {
                startAutomationRunPolling(latestRun)
            } else {
                clearUnknownAutomationRun()
                mutableUiState.update {
                    it.copy(
                        isAutomationRunInProgress = false,
                        automationRunOutcomeUnknown = false,
                    )
                }
            }
        }
    }

    private fun selectDestination(destination: UninDestination) {
        mutableUiState.update { it.copy(currentDestination = destination) }
        when (destination) {
            UninDestination.Missing -> loadLibrary()
            UninDestination.Downloads -> syncDownloads()
            UninDestination.Automation -> loadAutomation()
            UninDestination.Resources -> Unit
            UninDestination.Settings -> refreshConfiguration()
        }
    }

    private fun loadLibrary() {
        if (
            uiState.value.isLibraryLoading ||
            uiState.value.isLibrarySyncing ||
            libraryLoadJob?.isActive == true ||
            librarySyncJob?.isActive == true
        ) return

        mutableUiState.update { it.copy(isLibraryLoading = true) }
        lateinit var job: Job
        job = launchAction("无法读取缺失影视") {
            try {
                libraryOperationMutex.withLock {
                    val items = loadRecentLibrary()
                    mutableUiState.update { state ->
                        state.copy(missingMedia = items.toMissingMedia(state.missingMedia))
                    }
                }
            } finally {
                mutableUiState.update { it.copy(isLibraryLoading = false) }
            }
        }
        libraryLoadJob = job
        job.invokeOnCompletion {
            if (libraryLoadJob === job) libraryLoadJob = null
        }
    }

    private fun syncLibrary() {
        if (currentRole == null || currentRole == AuthRole.VIEWER) {
            mutableUiState.update { it.copy(snackbarMessage = "当前账号没有同步媒体库的权限") }
            return
        }
        if (uiState.value.isLibrarySyncing || librarySyncJob?.isActive == true) return

        libraryLoadJob?.cancel()
        libraryLoadJob = null
        mutableUiState.update {
            it.copy(
                isLibraryLoading = false,
                isLibrarySyncing = true,
            )
        }
        lateinit var job: Job
        job = launchAction("无法同步缺失影视") {
            try {
                libraryOperationMutex.withLock {
                    val result = repository.syncLibrary()
                    mutableUiState.update { state ->
                        state.copy(
                            lastSyncedText = "刚刚同步",
                            snackbarMessage = "同步完成：新增 ${result.created} 项，更新 ${result.updated} 项",
                            connections = state.connections.withConnection(
                                key = CONNECTION_NEXTFIND,
                                connectionState = ConnectionState.Connected,
                                description = "NextFind 媒体库同步正常",
                            ),
                        )
                    }

                    try {
                        val items = loadRecentLibrary()
                        mutableUiState.update { state ->
                            state.copy(missingMedia = items.toMissingMedia(state.missingMedia))
                        }
                    } catch (error: CancellationException) {
                        throw error
                    } catch (error: Throwable) {
                        if (error.isSessionAuthenticationFailure()) throw error
                        mutableUiState.update {
                            it.copy(
                                snackbarMessage = "同步已完成，但列表刷新失败；请稍后重新进入缺失页面",
                            )
                        }
                    }
                }
            } finally {
                mutableUiState.update { it.copy(isLibrarySyncing = false) }
            }
        }
        librarySyncJob = job
        job.invokeOnCompletion {
            if (librarySyncJob === job) librarySyncJob = null
        }
    }

    private fun openMedia(media: MissingMediaUi) {
        searchJob?.cancel()
        mediaDetailJob?.cancel()
        mutableUiState.update {
            it.copy(
                selectedMedia = media,
                candidates = emptyList(),
                currentDestination = UninDestination.Resources,
            )
        }
        mediaDetailJob = launchAction("无法读取影视详情") {
            val detail = repository.mediaDetail(media.id)
            val selected = detail.toUi(candidateCount = media.candidateCount)
            mutableUiState.update { state ->
                state.copy(
                    selectedMedia = selected,
                    missingMedia = state.missingMedia.replace(selected),
                )
            }
            detail.latestSearch?.let { summary ->
                val search = awaitSearch(repository.search(summary.id))
                applySearch(search)
            }
        }
    }

    private fun confirmTmdb(media: MissingMediaUi, tmdbId: Int? = null) {
        launchAction("无法确认 TMDB 影视信息") {
            val identified = repository.identifyMedia(media.id, tmdbId)
            val updated = identified.toUi(candidateCount = media.candidateCount)
            mutableUiState.update { state ->
                state.copy(
                    selectedMedia = if (state.selectedMedia?.id == updated.id) updated else state.selectedMedia,
                    missingMedia = state.missingMedia.replace(updated),
                    snackbarMessage = "已确认《${updated.title}》的 TMDB 信息",
                )
            }
        }
    }

    private fun searchResources(media: MissingMediaUi) {
        if (media.downloadActive) {
            mutableUiState.update {
                it.copy(
                    currentDestination = UninDestination.Downloads,
                    snackbarMessage = "该影视已有下载任务，请先查看下载状态",
                )
            }
            return
        }
        searchJob?.cancel()
        mutableUiState.update {
            it.copy(
                selectedMedia = media,
                candidates = emptyList(),
                currentDestination = UninDestination.Resources,
            )
        }
        searchJob = launchAction("无法搜索候选资源") {
            val sites = currentPolicy?.siteIds?.takeIf(List<String>::isNotEmpty)
                ?: listOf(DEFAULT_PT_SITE)
            val search = awaitSearch(repository.searchMedia(media.id, sites))
            applySearch(search)
            mutableUiState.update { state ->
                state.copy(
                    connections = state.connections.withConnection(
                        key = CONNECTION_PT,
                        connectionState = ConnectionState.Connected,
                        description = "AvistaZ 资源搜索正常",
                    ),
                )
            }
        }
    }

    private suspend fun awaitSearch(initial: SearchDetailDto): SearchDetailDto {
        var search = initial
        repeat(SEARCH_POLL_LIMIT) {
            when (search.state) {
                SearchState.SUCCEEDED -> return search
                SearchState.FAILED -> throw UserFacingException(
                    search.errorMessage ?: "资源搜索失败，请检查 PT 站点连接",
                )
                SearchState.PENDING,
                SearchState.RUNNING,
                -> {
                    delay(SEARCH_POLL_INTERVAL_MS)
                    search = repository.search(search.id)
                }
            }
        }
        throw UserFacingException("资源搜索仍在进行，请稍后重新打开该影视查看结果")
    }

    private fun applySearch(search: SearchDetailDto) {
        val candidates = search.candidates.map { it.toUi() }
        mutableUiState.update { state ->
            val selectedMatches = state.selectedMedia?.id == search.mediaId
            val selected = state.selectedMedia?.takeIf { selectedMatches }
                ?.copy(candidateCount = candidates.size)
            val mediaItems = state.missingMedia.map { media ->
                if (media.id == search.mediaId) {
                    media.copy(candidateCount = candidates.size)
                } else {
                    media
                }
            }
            state.copy(
                selectedMedia = selected ?: state.selectedMedia,
                candidates = if (selectedMatches) candidates else state.candidates,
                missingMedia = mediaItems,
                snackbarMessage = if (!selectedMatches) {
                    state.snackbarMessage
                } else if (candidates.isEmpty()) {
                    "搜索完成，暂未找到可用资源"
                } else {
                    "搜索完成，找到 ${candidates.size} 个候选资源"
                },
            )
        }
    }

    private fun downloadCandidate(candidate: ResourceCandidateUi) {
        val selectedMedia = uiState.value.selectedMedia
        val mediaId = selectedMedia?.id
        if (mediaId == null) {
            mutableUiState.update { it.copy(snackbarMessage = "请先选择要补全的影视") }
            return
        }
        if (selectedMedia.downloadActive) {
            mutableUiState.update {
                it.copy(
                    currentDestination = UninDestination.Downloads,
                    snackbarMessage = "该影视已有下载任务，不能重复提交其他资源",
                )
            }
            return
        }
        val unknownForSelectedMedia = mediaId in unknownDownloadSubmissionMediaIds
        if (
            uiState.value.submittingCandidateId != null ||
            unknownForSelectedMedia
        ) {
            mutableUiState.update {
                it.copy(
                    downloadSubmissionUnknownMediaIds = unknownDownloadSubmissionMediaIds,
                    snackbarMessage = if (unknownForSelectedMedia) {
                        "请先在下载页同步上次提交结果"
                    } else {
                        "已有资源正在提交，请等待结果"
                    },
                )
            }
            return
        }
        mutableUiState.update { it.copy(submittingCandidateId = candidate.id) }
        launchAction("无法提交下载任务") {
            try {
                repository.downloadCandidate(
                    candidateId = candidate.id,
                    confirmWarnings = candidate.riskMessage != null,
                )
                val downloads = loadAllDownloads()
                mutableUiState.update { state ->
                    val selected = state.selectedMedia?.copy(downloadActive = true)
                    state.copy(
                        downloads = downloads.map { it.toUi() },
                        selectedMedia = selected,
                        missingMedia = selected?.let { state.missingMedia.replace(it) }
                            ?: state.missingMedia,
                        candidates = emptyList(),
                        currentDestination = UninDestination.Downloads,
                        snackbarMessage = "已将《${candidate.title}》提交到 qBittorrent",
                        connections = state.connections.withConnection(
                            key = CONNECTION_QB,
                            connectionState = ConnectionState.Connected,
                            description = "qBittorrent 任务提交正常",
                        ),
                    )
                }
            } catch (error: CancellationException) {
                throw error
            } catch (error: Throwable) {
                if (error.mayLeaveWriteOutcomeUnknown()) {
                    rememberUnknownDownloadSubmission(mediaId)
                    val downloads = runCatching { loadAllDownloads() }.getOrNull()
                    val matching = downloads?.firstOrNull { it.candidateId == candidate.id }
                    val resolved = matching != null && matching.state !in setOf(
                        de.tlovex.unin.data.model.DownloadState.SUBMITTING,
                        de.tlovex.unin.data.model.DownloadState.OUTCOME_UNKNOWN,
                    )
                    if (resolved) {
                        clearUnknownDownloadSubmission(mediaId)
                    }
                    mutableUiState.update { state ->
                        val downloadActive = matching?.state != null && matching.state !in setOf(
                            de.tlovex.unin.data.model.DownloadState.ERROR,
                            de.tlovex.unin.data.model.DownloadState.COMPLETED,
                        )
                        val selected = state.selectedMedia?.let {
                            if (downloadActive) it.copy(downloadActive = true) else it
                        }
                        state.copy(
                            downloads = downloads?.map { it.toUi() } ?: state.downloads,
                            selectedMedia = selected,
                            missingMedia = selected?.let { state.missingMedia.replace(it) }
                                ?: state.missingMedia,
                            candidates = if (
                                downloadActive || mediaId in unknownDownloadSubmissionMediaIds
                            ) {
                                emptyList()
                            } else {
                                state.candidates
                            },
                            currentDestination = UninDestination.Downloads,
                            downloadSubmissionUnknownMediaIds = unknownDownloadSubmissionMediaIds,
                            snackbarMessage = when {
                                !resolved -> "提交响应中断，服务端可能仍在处理；请同步状态后再选择资源"
                                matching?.state == de.tlovex.unin.data.model.DownloadState.ERROR ->
                                    "服务端已记录提交失败，请在下载页查看原因"
                                else -> "提交响应中断，但已通过服务器记录确认任务"
                            },
                        )
                    }
                } else {
                    throw error
                }
            } finally {
                mutableUiState.update { state ->
                    if (state.submittingCandidateId == candidate.id) {
                        state.copy(submittingCandidateId = null)
                    } else {
                        state
                    }
                }
            }
        }
    }

    private fun retryDownload(item: DownloadItemUi) {
        val submissionUnknownForItem = item.mediaId in unknownDownloadSubmissionMediaIds
        if (submissionUnknownForItem || item.state != UiDownloadState.Failed) {
            mutableUiState.update {
                it.copy(
                    downloadSubmissionUnknownMediaIds = unknownDownloadSubmissionMediaIds,
                    snackbarMessage = "提交结果未知的任务必须先同步 qBittorrent 状态",
                )
            }
            return
        }
        launchAction("无法重试下载任务") {
            repository.retryDownload(item.id)
            val downloads = loadAllDownloads()
            mutableUiState.update { state ->
                state.copy(
                    downloads = downloads.map { it.toUi() },
                    snackbarMessage = "下载任务已重新提交",
                )
            }
        }
    }

    private fun syncDownloads() {
        if (downloadsSyncJob?.isActive == true) return

        lateinit var job: Job
        job = launchAction("无法同步下载状态") {
            libraryOperationMutex.withLock {
                val result = repository.syncDownloads()
                val downloads = loadAllDownloads()
                val library = loadRecentLibrary()
                val unresolvedMediaIds = downloads.filter {
                    it.state == de.tlovex.unin.data.model.DownloadState.SUBMITTING ||
                        it.state == de.tlovex.unin.data.model.DownloadState.OUTCOME_UNKNOWN
                }.mapTo(mutableSetOf()) { it.mediaId }
                val resolvedMediaIds = unknownDownloadSubmissionMediaIds - unresolvedMediaIds
                resolvedMediaIds.forEach(::clearUnknownDownloadSubmission)
                mutableUiState.update { state ->
                    state.copy(
                        downloads = downloads.map { it.toUi() },
                        missingMedia = library.toMissingMedia(state.missingMedia),
                        downloadSubmissionUnknownMediaIds = unknownDownloadSubmissionMediaIds,
                        snackbarMessage = when {
                            resolvedMediaIds.isNotEmpty() && unknownDownloadSubmissionMediaIds.isEmpty() ->
                                "已确认上次提交结果，可以继续选择资源"
                            resolvedMediaIds.isNotEmpty() ->
                                "已确认部分提交结果，其余任务仍需稍后同步"
                            unknownDownloadSubmissionMediaIds.isNotEmpty() ->
                                "上次提交仍未确认，请稍后再次同步"
                            result.updated > 0 -> "已更新 ${result.updated} 个下载任务"
                            else -> "下载状态已同步"
                        },
                        connections = state.connections.withConnection(
                            key = CONNECTION_QB,
                            connectionState = ConnectionState.Connected,
                            description = "qBittorrent 状态同步正常",
                        ),
                    )
                }
            }
        }
        downloadsSyncJob = job
        job.invokeOnCompletion {
            if (downloadsSyncJob === job) downloadsSyncJob = null
        }
    }

    private fun loadAutomation() {
        launchAction("无法读取自动化配置") {
            val policy = repository.automationPolicy()
            val jobs = loadAllAutomationJobs()
            val latestRun = repository.latestAutomationRun()
            currentPolicy = policy
            pendingPolicy = policy.toUpdate()
            mutableUiState.update {
                it.copy(
                    automationPolicy = policy.toUi(),
                    automationRuns = jobs.map { it.toUi() },
                )
            }
            if (latestRun == null) {
                clearUnknownAutomationRun()
                automationRunPollJob?.cancel()
                mutableUiState.update {
                    it.copy(
                        isAutomationRunInProgress = false,
                        automationRunOutcomeUnknown = false,
                    )
                }
            } else if (latestRun.isActive) {
                startAutomationRunPolling(latestRun)
            } else {
                clearUnknownAutomationRun()
                automationRunPollJob?.cancel()
                mutableUiState.update {
                    it.copy(
                        isAutomationRunInProgress = false,
                        automationRunOutcomeUnknown = false,
                    )
                }
            }
        }
    }

    private fun editPolicyUi(
        uiTransform: (AutomationPolicyUi) -> AutomationPolicyUi,
        dtoTransform: (AutomationPolicyUpdateDto) -> AutomationPolicyUpdateDto,
    ) {
        val source = pendingPolicy ?: currentPolicy?.toUpdate()
        if (source == null) {
            mutableUiState.update { it.copy(snackbarMessage = "自动化策略尚未加载") }
            return
        }

        pendingPolicy = dtoTransform(source)
        mutableUiState.update { it.copy(automationPolicy = uiTransform(it.automationPolicy)) }
    }

    private fun saveAutomationPolicy() {
        launchAction("无法保存自动化策略") {
            persistPendingPolicy()
            mutableUiState.update { it.copy(snackbarMessage = "自动化策略已保存") }
        }
    }

    private fun runAutomation() {
        val state = uiState.value
        if (!state.automationPolicy.enabled) {
            mutableUiState.update { it.copy(snackbarMessage = "请先启用自动化策略") }
            return
        }
        if (state.isAutomationRunInProgress) {
            mutableUiState.update { it.copy(snackbarMessage = "自动化任务正在运行，请等待结果") }
            return
        }
        if (state.automationRunOutcomeUnknown || hasUnknownAutomationRun) {
            mutableUiState.update {
                it.copy(
                    automationRunOutcomeUnknown = true,
                    snackbarMessage = "请先检查上次运行状态，避免重复提交下载",
                )
            }
            return
        }
        mutableUiState.update { it.copy(isAutomationRunInProgress = true) }
        launchAction("无法运行自动化任务") {
            var policy: AutomationPolicyDto? = null
            try {
                val savedPolicy = persistPendingPolicy()
                policy = savedPolicy
                val run = repository.runAutomation()
                clearUnknownAutomationRun()
                startAutomationRunPolling(run)
                mutableUiState.update {
                    it.copy(
                        automationRunOutcomeUnknown = false,
                        snackbarMessage = if (savedPolicy.dryRun) {
                            "预演已进入后台，正在等待处理结果"
                        } else {
                            "自动化已进入后台，正在等待处理结果"
                        },
                    )
                }
            } catch (error: CancellationException) {
                throw error
            } catch (error: Throwable) {
                if (
                    policy != null &&
                    (error.mayLeaveWriteOutcomeUnknown() || error is HttpException && error.code() == 409)
                ) {
                    reconcileUnknownAutomationRun()
                } else {
                    mutableUiState.update { it.copy(isAutomationRunInProgress = false) }
                    throw error
                }
            }
        }
    }

    private fun refreshAutomationStatus() {
        launchAction("无法检查自动化运行状态") {
            val policy = repository.automationPolicy()
            val jobs = loadAllAutomationJobs()
            val latestRun = repository.latestAutomationRun()
            currentPolicy = policy
            pendingPolicy = policy.toUpdate()

            val resolved = hasUnknownAutomationRun && latestRun?.isActive != true
            if (resolved) clearUnknownAutomationRun()

            mutableUiState.update {
                it.copy(
                    automationPolicy = policy.toUi(),
                    automationRuns = jobs.map { job -> job.toUi() },
                    isAutomationRunInProgress = latestRun?.isActive == true,
                    automationRunOutcomeUnknown = hasUnknownAutomationRun && !resolved,
                    snackbarMessage = when {
                        latestRun?.isActive == true -> latestRun.progressMessage()
                        resolved -> "服务器记录已确认上次运行结束，重复运行保护已解除"
                        hasUnknownAutomationRun -> "服务器尚未确认上次运行结束，已继续锁定重复运行"
                        else -> "自动化运行记录已刷新"
                    },
                )
            }
            if (latestRun?.isActive == true) startAutomationRunPolling(latestRun)
        }
    }

    private suspend fun reconcileUnknownAutomationRun() {
        rememberUnknownAutomationRun()
        val policy = runCatching { repository.automationPolicy() }.getOrNull()
        val jobs = runCatching { loadAllAutomationJobs() }.getOrNull()
        val latestRunResult = runCatching { repository.latestAutomationRun() }
        val latestRun = latestRunResult.getOrNull()
        val resolved = latestRunResult.isSuccess && latestRun?.isActive != true
        if (resolved) clearUnknownAutomationRun()

        if (policy != null) {
            currentPolicy = policy
            pendingPolicy = policy.toUpdate()
        }
        mutableUiState.update { state ->
            state.copy(
                automationPolicy = policy?.toUi() ?: state.automationPolicy,
                automationRuns = jobs?.map { it.toUi() } ?: state.automationRuns,
                isAutomationRunInProgress = latestRun?.isActive == true,
                automationRunOutcomeUnknown = hasUnknownAutomationRun,
                snackbarMessage = when {
                    latestRun?.isActive == true -> "已找到服务端运行记录，将继续跟踪后台进度"
                    resolved -> "服务端未发现活跃运行，重复运行保护已解除"
                    else -> "运行响应中断，任务可能仍在服务器执行；已锁定重复运行，请稍后检查状态"
                },
            )
        }
        if (latestRun != null) startAutomationRunPolling(latestRun)
    }

    private fun startAutomationRunPolling(initialRun: AutomationRunDto) {
        startAutomationRunPolling(runId = initialRun.id, initialRun = initialRun)
    }

    private fun startAutomationRunPolling(runId: String) {
        startAutomationRunPolling(runId = runId, initialRun = null)
    }

    private fun startAutomationRunPolling(runId: String, initialRun: AutomationRunDto?) {
        val shouldTrack = initialRun?.isActive ?: true
        if (
            shouldTrack &&
            automationRunPollJob?.isActive == true &&
            automationRunPollId == runId &&
            uiState.value.isAutomationRunInProgress
        ) {
            return
        }
        automationRunPollId = runId

        automationRunPollJob?.cancel()
        mutableUiState.update {
            it.copy(
                isAutomationRunInProgress = shouldTrack,
                automationRunOutcomeUnknown = false,
            )
        }

        lateinit var pollingJob: Job
        pollingJob = viewModelScope.launch {
            var lastProgressMessage: String? = null
            try {
                var loadedRun = initialRun
                while (loadedRun == null) {
                    loadedRun = try {
                        repository.automationRun(runId)
                    } catch (error: CancellationException) {
                        throw error
                    } catch (error: Throwable) {
                        if (error.isSessionAuthenticationFailure()) {
                            expireSession("登录已过期，请重新登录")
                            return@launch
                        }
                        mutableUiState.update {
                            it.copy(snackbarMessage = "暂时无法读取自动化进度，将继续后台重试")
                        }
                        delay(AUTOMATION_RUN_POLL_INTERVAL_MS)
                        null
                    }
                }
                var run = requireNotNull(loadedRun)
                while (run.isActive) {
                    val progressMessage = run.progressMessage()
                    if (progressMessage != lastProgressMessage) {
                        mutableUiState.update { it.copy(snackbarMessage = progressMessage) }
                        lastProgressMessage = progressMessage
                    }
                    delay(AUTOMATION_RUN_POLL_INTERVAL_MS)
                    run = try {
                        repository.automationRun(run.id)
                    } catch (error: CancellationException) {
                        throw error
                    } catch (error: Throwable) {
                        if (error.isSessionAuthenticationFailure()) {
                            expireSession("登录已过期，请重新登录")
                            return@launch
                        }
                        mutableUiState.update {
                            it.copy(snackbarMessage = "暂时无法读取自动化进度，将继续后台重试")
                        }
                        continue
                    }
                }

                clearUnknownAutomationRun()
                val jobs = try {
                    loadAllAutomationJobs()
                } catch (error: CancellationException) {
                    throw error
                } catch (error: Throwable) {
                    if (error.isSessionAuthenticationFailure()) {
                        expireSession("登录已过期，请重新登录")
                        return@launch
                    }
                    null
                }
                mutableUiState.update { state ->
                    state.copy(
                        automationRuns = jobs?.map { it.toUi() } ?: state.automationRuns,
                        automationRunOutcomeUnknown = false,
                        snackbarMessage = run.completionMessage(jobsRefreshed = jobs != null),
                    )
                }
            } catch (error: CancellationException) {
                throw error
            } finally {
                if (automationRunPollJob === pollingJob) {
                    automationRunPollJob = null
                    automationRunPollId = null
                    mutableUiState.update { it.copy(isAutomationRunInProgress = false) }
                }
            }
        }
        automationRunPollJob = pollingJob
        authenticatedJobs += pollingJob
        pollingJob.invokeOnCompletion { authenticatedJobs -= pollingJob }
    }

    private suspend fun persistPendingPolicy(): AutomationPolicyDto {
        val snapshot = pendingPolicy ?: throw UserFacingException("自动化策略尚未加载")
        val saved = currentPolicy?.takeIf { it.toUpdate() == snapshot }
            ?: repository.updateAutomationPolicy(snapshot)
        currentPolicy = saved
        pendingPolicy = saved.toUpdate()
        mutableUiState.update { it.copy(automationPolicy = saved.toUi()) }
        return saved
    }

    private fun retryAutomation(run: AutomationRunUi) {
        if (hasUnknownAutomationRun || uiState.value.isAutomationRunInProgress) {
            mutableUiState.update {
                it.copy(
                    automationRunOutcomeUnknown = hasUnknownAutomationRun,
                    snackbarMessage = "请先确认上次自动化运行状态，再重试失败任务",
                )
            }
            return
        }
        mutableUiState.update { it.copy(isAutomationRunInProgress = true) }
        launchAction("无法重试自动化任务") {
            var policy: AutomationPolicyDto? = null
            try {
                policy = persistPendingPolicy()
                val retryJob = repository.retryAutomationJob(run.id)
                startAutomationRunPolling(retryJob.runId)
                val jobs = try {
                    loadAllAutomationJobs()
                } catch (error: CancellationException) {
                    throw error
                } catch (_: Throwable) {
                    null
                }
                mutableUiState.update { state ->
                    state.copy(
                        automationRuns = jobs?.map { job -> job.toUi() } ?: state.automationRuns,
                        snackbarMessage = if (jobs == null) {
                            "《${run.title}》已开始重试，任务列表稍后刷新"
                        } else {
                            "《${run.title}》已开始重试"
                        },
                    )
                }
            } catch (error: CancellationException) {
                throw error
            } catch (error: Throwable) {
                if (
                    policy != null &&
                    (error.mayLeaveWriteOutcomeUnknown() || error is HttpException && error.code() == 409)
                ) {
                    reconcileUnknownAutomationRun()
                } else {
                    mutableUiState.update { it.copy(isAutomationRunInProgress = false) }
                    throw error
                }
            }
        }
    }

    private fun refreshConfiguration() {
        if (currentRole != AuthRole.ADMIN || uiState.value.settingsConfiguration.isSaving) return
        launchAction("无法读取服务配置") {
            val configuration = repository.configurationStatus()
            mutableUiState.update {
                it.copy(
                    connections = configuration.toConnections(),
                    settingsConfiguration = configuration.toSettingsConfiguration(),
                )
            }
        }
    }

    private fun saveNextFindConfiguration(
        baseUrl: String,
        username: String,
        replacementPassword: String?,
    ) = saveConfiguration("NextFind") {
        repository.updateNextFindConfiguration(baseUrl, username, replacementPassword)
    }

    private fun saveTmdbConfiguration(replacementToken: String) {
        if (replacementToken.isBlank()) {
            mutableUiState.update { it.copy(snackbarMessage = "TMDB Access Token 不能为空") }
            return
        }
        saveConfiguration("TMDB") {
            repository.updateTmdbConfiguration(replacementToken)
        }
    }

    private fun saveOutboundProxyConfiguration(
        url: String,
        username: String,
        replacementPassword: String?,
    ) = saveConfiguration(if (url.isBlank()) "出站代理停用设置" else "出站代理") {
        repository.updateOutboundProxyConfiguration(url, username, replacementPassword)
    }

    private fun saveQbittorrentConfiguration(
        baseUrl: String,
        username: String,
        replacementPassword: String?,
        savePath: String,
        category: String,
        allowInsecureHttp: Boolean,
    ) = saveConfiguration("qBittorrent") {
        repository.updateQbittorrentConfiguration(
            url = baseUrl,
            username = username,
            savePath = savePath,
            category = category,
            allowInsecureHttp = allowInsecureHttp,
            password = replacementPassword,
        )
    }

    private fun saveAvistaZConfiguration(
        baseUrl: String,
        username: String,
        replacementPassword: String?,
        replacementPid: String?,
    ) = saveConfiguration("AvistaZ") {
        repository.updateAvistaZConfiguration(
            baseUrl = baseUrl,
            username = username,
            password = replacementPassword,
            pid = replacementPid,
        )
    }

    private fun saveNexusPhpConfiguration(
        siteId: String,
        displayName: String,
        baseUrl: String,
        replacementCookie: String?,
        replacementPasskey: String?,
    ) = saveConfiguration("NexusPHP") {
        repository.updateNexusPhpConfiguration(
            siteId = siteId,
            displayName = displayName,
            baseUrl = baseUrl,
            cookie = replacementCookie,
            passkey = replacementPasskey,
        )
    }

    private fun saveConfiguration(
        serviceName: String,
        update: suspend () -> ConfigurationStatusDto,
    ) {
        if (currentRole != AuthRole.ADMIN) {
            mutableUiState.update { it.copy(snackbarMessage = "只有管理员可以修改服务配置") }
            return
        }
        if (uiState.value.settingsConfiguration.isSaving) return
        mutableUiState.update {
            it.copy(settingsConfiguration = it.settingsConfiguration.copy(isSaving = true))
        }
        launchAction("无法保存 $serviceName 配置") {
            try {
                val configuration = update()
                mutableUiState.update {
                    it.copy(
                        connections = configuration.toConnections(),
                        settingsConfiguration = configuration.toSettingsConfiguration(isSaving = false),
                        snackbarMessage = "$serviceName 配置已保存；可执行连接测试确认服务状态",
                    )
                }
            } finally {
                mutableUiState.update {
                    it.copy(settingsConfiguration = it.settingsConfiguration.copy(isSaving = false))
                }
            }
        }
    }

    private fun testConnection(connection: ConnectionUi) {
        if (connection.key != CONNECTION_SERVER && currentRole != AuthRole.ADMIN) {
            mutableUiState.update {
                it.copy(snackbarMessage = "连接测试仅允许管理员执行；服务凭据仍由 UNIN 服务端保管")
            }
            return
        }

        mutableUiState.update { state ->
            state.copy(
                connections = state.connections.withConnection(
                    key = connection.key,
                    connectionState = ConnectionState.Checking,
                    description = "正在执行服务端连接测试…",
                ),
            )
        }
        launchAction("无法测试 ${connection.name} 连接") {
            try {
                if (connection.key == CONNECTION_SERVER) {
                    val principal = repository.currentUser()
                    mutableUiState.update { state ->
                        state.copy(
                            accountDisplayName = principal.displayName(),
                            connections = state.connections.withConnection(
                                key = CONNECTION_SERVER,
                                connectionState = ConnectionState.Connected,
                                description = "HTTPS API 与登录会话正常",
                            ),
                            snackbarMessage = "UNIN 服务连接正常",
                        )
                    }
                    return@launchAction
                }

                val result = when (connection.key) {
                    CONNECTION_NEXTFIND -> repository.testNextFindConnection()
                    CONNECTION_TMDB -> repository.testTmdbConnection()
                    CONNECTION_PROXY -> repository.testOutboundProxyConnection()
                    CONNECTION_PT -> repository.testPtSiteConnection(
                        when (uiState.value.settingsConfiguration.activePtArchitecture) {
                            PtArchitectureUi.AvistaZ -> PtSiteArchitecture.AVISTAZ
                            PtArchitectureUi.NexusPhp -> PtSiteArchitecture.NEXUSPHP
                        },
                    )
                    CONNECTION_QB -> repository.testQbittorrentConnection()
                    else -> throw UserFacingException("不支持测试该连接")
                }
                mutableUiState.update { state ->
                    state.copy(
                        connections = state.connections.withConnection(
                            key = connection.key,
                            connectionState = if (result.healthy) {
                                ConnectionState.Connected
                            } else {
                                ConnectionState.Disconnected
                            },
                            description = result.message,
                        ),
                        snackbarMessage = result.message,
                    )
                }
            } catch (error: CancellationException) {
                throw error
            } catch (error: Throwable) {
                mutableUiState.update { state ->
                    state.copy(
                        connections = state.connections.withConnection(
                            key = connection.key,
                            connectionState = if (error is HttpException && error.code() == 409) {
                                ConnectionState.NotConfigured
                            } else {
                                ConnectionState.Disconnected
                            },
                            description = errorMessage(error, "连接测试失败"),
                        ),
                    )
                }
                throw error
            }
        }
    }

    private fun launchAction(
        fallbackMessage: String,
        block: suspend () -> Unit,
    ): Job {
        lateinit var job: Job
        job = viewModelScope.launch {
            beginOperation()
            try {
                block()
            } catch (error: CancellationException) {
                throw error
            } catch (error: Throwable) {
                if (error.isSessionAuthenticationFailure()) {
                    expireSession("登录已过期，请重新登录")
                } else {
                    mutableUiState.update {
                        it.copy(snackbarMessage = errorMessage(error, fallbackMessage))
                    }
                }
            } finally {
                endOperation()
            }
        }
        authenticatedJobs += job
        job.invokeOnCompletion { authenticatedJobs -= job }
        return job
    }

    private fun beginOperation() {
        activeOperations += 1
        mutableUiState.update { it.copy(isBusy = true) }
    }

    private fun endOperation() {
        activeOperations = (activeOperations - 1).coerceAtLeast(0)
        mutableUiState.update { it.copy(isBusy = activeOperations > 0) }
    }

    private fun showAuthenticated(principal: PrincipalDto) {
        currentRole = principal.role
        mutableUiState.update {
            UninUiState(
                isAuthenticated = true,
                isBusy = activeOperations > 0,
                canSyncLibrary = principal.role != AuthRole.VIEWER,
                accountDisplayName = principal.displayName(),
                serverLabel = serverLabel,
                connections = authenticatedConnections(),
                settingsConfiguration = SettingsConfigurationUiState(
                    canEdit = principal.role == AuthRole.ADMIN,
                ),
                downloadSubmissionUnknownMediaIds = unknownDownloadSubmissionMediaIds,
                automationRunOutcomeUnknown = hasUnknownAutomationRun,
            )
        }
    }

    private fun showSignedOut(error: String? = null) {
        val username = uiState.value.username
        mutableUiState.value = UninUiState(
            isBusy = activeOperations > 0,
            username = username,
            loginError = error,
            serverLabel = serverLabel,
            connections = signedOutConnections(),
        )
        currentPolicy = null
        pendingPolicy = null
        currentRole = null
    }

    private fun expireSession(message: String) {
        cancelAuthenticationJob()
        cancelAuthenticatedJobs()
        searchJob?.cancel()
        mediaDetailJob?.cancel()
        repository.clearAuth()
        showSignedOut(message)
    }

    private fun cancelAuthenticatedJobs() {
        authenticatedJobs.toList().forEach { it.cancel() }
        authenticatedJobs.clear()
        libraryLoadJob = null
        librarySyncJob = null
        downloadsSyncJob = null
        automationRunPollJob = null
        automationRunPollId = null
    }

    private fun cancelAuthenticationJob() {
        authenticationJob?.cancel()
        authenticationJob = null
    }

    private suspend fun loadRecentLibrary(): List<MediaSummaryDto> = coroutineScope {
        LibraryWindow.combine(
            LibraryWindow.states.map { state ->
                async {
                    val first = repository.library(
                        state = state,
                        page = 1,
                        pageSize = LibraryWindow.pageSize,
                    )
                    buildList {
                        addAll(first.items)
                        LibraryWindow.additionalPages(first.total).forEach { page ->
                            addAll(
                                repository.library(
                                    state = state,
                                    page = page,
                                    pageSize = LibraryWindow.pageSize,
                                ).items,
                            )
                        }
                    }
                }
            }.awaitAll(),
        )
    }

    private suspend fun loadAllDownloads(): List<DownloadDto> {
        val first = repository.downloads(page = 1, pageSize = PAGE_SIZE)
        val items = first.items.toMutableList()
        val pages = (first.total + PAGE_SIZE - 1) / PAGE_SIZE
        for (page in 2..pages) {
            items += repository.downloads(page = page, pageSize = PAGE_SIZE).items
        }
        return items
    }

    private suspend fun loadAllAutomationJobs(): List<AutomationJobDto> {
        val first = repository.automationJobs(page = 1, pageSize = PAGE_SIZE)
        val items = first.items.toMutableList()
        val pages = (first.total + PAGE_SIZE - 1) / PAGE_SIZE
        for (page in 2..pages) {
            items += repository.automationJobs(page = page, pageSize = PAGE_SIZE).items
        }
        return items
    }

    private fun List<MediaSummaryDto>.toMissingMedia(
        previous: List<MissingMediaUi>,
    ): List<MissingMediaUi> {
        val candidateCounts = previous.associate { it.id to it.candidateCount }
        return map { it.toUi(candidateCounts[it.id] ?: 0) }
    }

    private fun MediaSummaryDto.toUi(candidateCount: Int = 0) = MissingMediaUi(
        id = id,
        title = title,
        originalTitle = originalTitle.orEmpty(),
        year = year,
        kind = if (mediaType == MediaType.MOVIE) MediaKind.Movie else MediaKind.Series,
        missingDescription = attentionReason ?: state.description(),
        posterUrl = posterPath,
        tmdbConfirmed = tmdbId != null,
        candidateCount = candidateCount,
        downloadActive = state == MediaState.DOWNLOADING,
    )

    private fun MediaDetailDto.toUi(candidateCount: Int = 0): MissingMediaUi {
        val missingEpisodes = episodes.count { it.state == EpisodeState.MISSING }
        val summary = MediaSummaryDto(
            id = id,
            source = source,
            sourceItemId = sourceItemId,
            mediaType = mediaType,
            tmdbId = tmdbId,
            title = title,
            originalTitle = originalTitle,
            countryCodes = countryCodes,
            originalLanguage = originalLanguage,
            regions = regions,
            year = year,
            posterPath = posterPath,
            state = state,
            attentionReason = attentionReason,
            discoveredAt = discoveredAt,
            updatedAt = updatedAt,
        )
        val mapped = summary.toUi(candidateCount)
        return if (mediaType == MediaType.TV && missingEpisodes > 0 && attentionReason == null) {
            mapped.copy(missingDescription = "缺失 $missingEpisodes 集")
        } else {
            mapped
        }
    }

    private fun CandidateDto.toUi(): ResourceCandidateUi {
        val coverage = when {
            episodeCoverage.isNotEmpty() -> episodeCoverage.joinToString("、")
            seasonCoverage.isNotEmpty() -> seasonCoverage.joinToString("、") { "第 ${it} 季" }
            else -> "完整资源"
        }
        val scorePercent = if (score <= 1.0) score * 100 else score
        return ResourceCandidateUi(
            id = id,
            title = title,
            siteName = siteId.siteDisplayName(),
            releaseGroup = releaseGroup(title),
            resolution = resolution ?: "分辨率未知",
            source = source ?: "来源未知",
            codec = codec ?: "编码未知",
            sizeText = sizeBytes.formatBytes(),
            seeders = seeders ?: 0,
            matchScore = scorePercent.roundToInt().coerceIn(0, 100),
            freeLeech = downloadFactor != null && downloadFactor <= 0.0,
            coversMissing = coverage,
            riskMessage = warnings.takeIf(List<String>::isNotEmpty)?.joinToString("；"),
        )
    }

    private fun DownloadDto.toUi(): DownloadItemUi {
        val uiState = when (state) {
            de.tlovex.unin.data.model.DownloadState.SUBMITTING,
            de.tlovex.unin.data.model.DownloadState.QUEUED,
            de.tlovex.unin.data.model.DownloadState.PAUSED,
            -> UiDownloadState.Queued
            de.tlovex.unin.data.model.DownloadState.DOWNLOADING -> UiDownloadState.Downloading
            de.tlovex.unin.data.model.DownloadState.SEEDING -> UiDownloadState.Seeding
            de.tlovex.unin.data.model.DownloadState.COMPLETED -> UiDownloadState.Completed
            de.tlovex.unin.data.model.DownloadState.OUTCOME_UNKNOWN -> UiDownloadState.OutcomeUnknown
            de.tlovex.unin.data.model.DownloadState.ERROR -> UiDownloadState.Failed
        }
        val fallbackError = if (
            state == de.tlovex.unin.data.model.DownloadState.OUTCOME_UNKNOWN &&
            errorMessage.isNullOrBlank()
        ) {
            "提交结果未知，请先在 qBittorrent 中确认"
        } else {
            null
        }
        return DownloadItemUi(
            id = id,
            title = name,
            mediaId = mediaId,
            subtitle = "更新于 ${formatServerTime(updatedAt)}",
            progress = progress.toFloat().coerceIn(0f, 1f),
            state = uiState,
            speedText = if (downloadSpeed > 0) "↓ ${downloadSpeed.formatRate()}" else "",
            ratioText = if (ratio > 0.0) String.format(Locale.US, "分享率 %.2f", ratio) else "",
            errorMessage = errorMessage ?: fallbackError,
        )
    }

    private fun AutomationPolicyDto.toUi() = AutomationPolicyUi(
        enabled = enabled,
        dryRun = dryRun,
        autoIdentify = autoIdentify,
        siteNames = siteIds.joinToString("、") { it.siteDisplayName() }.ifBlank { "未设置" },
        mediaTypesText = mediaTypes.joinToString("、") {
            if (it == MediaType.MOVIE) "电影" else "电视剧"
        }.ifBlank { "未设置" },
        scopeText = when (scopeMode) {
            AutomationScopeMode.FILTERS -> "按筛选条件"
            AutomationScopeMode.SELECTED -> "手动选择 ${selectedMediaIds.size} 项"
        },
        regionsText = if (scopeMode == AutomationScopeMode.SELECTED) {
            "不适用（手动选择）"
        } else {
            regions.joinToString("、").ifBlank { "全部地区" }
        },
        minimumScore = (minimumScore * 100).roundToInt().coerceIn(0, 100),
        minimumSeeders = minimumSeeders,
        maximumSizeGb = maxSizeBytes?.let { (it.toDouble() / BYTES_PER_GIB).roundToInt() },
        allowWarnings = allowWarnings,
        intervalMinutes = intervalMinutes,
        retryDelayMinutes = retryDelayMinutes,
        maxAttempts = maxAttempts,
        dailyLimit = dailyDownloadLimit,
        dailyDownloadSizeGb = dailyDownloadBytes?.let {
            (it.toDouble() / BYTES_PER_GIB).roundToInt()
        },
    )

    private fun AutomationPolicyDto.toUpdate() = AutomationPolicyUpdateDto(
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
    )

    private fun AutomationJobDto.toUi(): AutomationRunUi {
        val mode = decision["mode"] as? String
        val candidateCount = (decision["candidate_count"] as? Number)?.toInt() ?: 0
        val selectedTitle = decision["selected_title"] as? String
        val skipped = decision["download_skipped"] as? String
        val details = buildList {
            add("候选 $candidateCount 个")
            if (!selectedTitle.isNullOrBlank()) add("选择：$selectedTitle")
            if (!skipped.isNullOrBlank()) add(skipped)
            if (!errorMessage.isNullOrBlank()) add(errorMessage)
        }.joinToString(" · ")
        val uiState = if (supersededAt != null) {
            AutomationRunState.Superseded
        } else {
            when (state) {
                AutomationJobState.PENDING,
                AutomationJobState.RUNNING,
                -> AutomationRunState.Running
                AutomationJobState.RETRY_WAIT -> AutomationRunState.Waiting
                AutomationJobState.SUCCEEDED -> if (mode == "dry-run") {
                    AutomationRunState.DryRun
                } else {
                    AutomationRunState.Success
                }
                AutomationJobState.FAILED -> AutomationRunState.Failed
            }
        }
        return AutomationRunUi(
            id = id,
            title = mediaTitle,
            detail = details,
            timeText = formatServerTime(createdAt),
            state = uiState,
            canRetry = supersededAt == null &&
                (state == AutomationJobState.FAILED || state == AutomationJobState.RETRY_WAIT),
        )
    }

    private val AutomationRunDto.isActive: Boolean
        get() = state == AutomationRunStateDto.PENDING || state == AutomationRunStateDto.RUNNING

    private fun AutomationRunDto.progressMessage(): String =
        "自动化后台执行中：已创建 $created 项，成功 $succeeded 项，失败 $failed 项，等待 $deferred 项"

    private fun AutomationRunDto.completionMessage(jobsRefreshed: Boolean): String {
        val prefix = if (state == AutomationRunStateDto.FAILED) "自动化运行失败" else "自动化运行完成"
        val detail = "$prefix：处理 $created 项，成功 $succeeded 项，失败 $failed 项，延后 $deferred 项"
        val error = errorMessage?.takeIf { it.isNotBlank() }?.let { "；$it" }.orEmpty()
        val refreshWarning = if (jobsRefreshed) "" else "；任务记录刷新失败，可稍后手动刷新"
        return detail + error + refreshWarning
    }

    private fun MediaState.description(): String = when (this) {
        MediaState.MISSING -> "等待补全资源"
        MediaState.IDENTIFYING -> "正在识别 TMDB 信息"
        MediaState.READY -> "可以开始搜索资源"
        MediaState.SEARCHING -> "正在搜索 PT 资源"
        MediaState.CANDIDATES -> "已有候选资源待选择"
        MediaState.DOWNLOADING -> "资源正在下载"
        MediaState.COMPLETE -> "资源已补全"
        MediaState.NEEDS_ATTENTION -> "需要人工处理"
    }

    private fun PrincipalDto.displayName(): String {
        val roleLabel = when (role) {
            AuthRole.ADMIN -> "管理员"
            AuthRole.OPERATOR -> "操作员"
            AuthRole.VIEWER -> "访客"
        }
        return "$username · $roleLabel"
    }

    private fun authenticatedConnections() = listOf(
        ConnectionUi(
            key = CONNECTION_SERVER,
            name = "UNIN 服务",
            description = "HTTPS API 与登录会话正常",
            state = ConnectionState.Connected,
        ),
        ConnectionUi(
            key = CONNECTION_NEXTFIND,
            name = "NextFind",
            description = "凭据由服务端管理；同步后显示最近状态",
            state = ConnectionState.NotConfigured,
        ),
        ConnectionUi(
            key = CONNECTION_TMDB,
            name = "TMDB",
            description = if (currentRole == AuthRole.ADMIN) {
                "元数据凭据由服务端管理；点击测试验证连接"
            } else {
                "需管理员账号查看连接状态"
            },
            state = ConnectionState.NotConfigured,
        ),
        ConnectionUi(
            key = CONNECTION_PROXY,
            name = "出站代理",
            description = "可选；用于 NextFind、TMDB 与 PT 的外部请求",
            state = ConnectionState.NotConfigured,
        ),
        ConnectionUi(
            key = CONNECTION_PT,
            name = "PT 站点",
            description = "PT 凭据由服务端管理；搜索后显示最近状态",
            state = ConnectionState.NotConfigured,
        ),
        ConnectionUi(
            key = CONNECTION_QB,
            name = "qBittorrent",
            description = "连接配置由服务端管理；同步后显示最近状态",
            state = ConnectionState.NotConfigured,
        ),
    )

    private fun ConfigurationStatusDto.toConnections(): List<ConnectionUi> {
        val activePt = ptSite
        val activePtName = when (activePt?.architecture) {
            PtSiteArchitecture.NEXUSPHP -> "NexusPHP"
            else -> "AvistaZ"
        }
        return listOf(
            ConnectionUi(
                key = CONNECTION_SERVER,
                name = "UNIN 服务",
                description = if (configurationComplete) {
                    "HTTPS API、登录会话与服务端配置正常"
                } else {
                    "HTTPS API 正常；部分服务尚未配置"
                },
                state = ConnectionState.Connected,
            ),
            ConnectionUi(
                key = CONNECTION_NEXTFIND,
                name = "NextFind",
                description = if (nextfind.configured) "服务端已配置，点击测试验证连接" else "服务端尚未配置",
                    state = if (nextfind.configured) ConnectionState.Configured else ConnectionState.NotConfigured,
            ),
            ConnectionUi(
                key = CONNECTION_TMDB,
                name = "TMDB",
                description = if (tmdb.configured) "服务端已配置，点击测试验证连接" else "服务端尚未配置",
                    state = if (tmdb.configured) ConnectionState.Configured else ConnectionState.NotConfigured,
            ),
            ConnectionUi(
                key = CONNECTION_PROXY,
                name = "出站代理",
                description = if (outboundProxy.configured) {
                    "服务端已启用，点击测试验证代理出口"
                } else {
                    "可选；当前未启用"
                },
                state = if (outboundProxy.configured) {
                    ConnectionState.Configured
                } else {
                    ConnectionState.NotConfigured
                },
            ),
            ConnectionUi(
                key = CONNECTION_PT,
                name = "PT 站点 · $activePtName",
                description = when {
                    activePt == null || !activePt.configured -> "服务端尚未配置"
                    !activePt.runtimeSupported -> "配置可测试，但当前服务端尚不能用它搜索资源"
                    !activePt.searchReady -> "已配置，但尚未满足资源搜索条件"
                    else -> "服务端已配置且可搜索，点击测试验证连接"
                },
                state = when {
                    activePt == null || !activePt.configured -> ConnectionState.NotConfigured
                    activePt.runtimeSupported && activePt.searchReady -> ConnectionState.Configured
                    else -> ConnectionState.Disconnected
                },
            ),
            ConnectionUi(
                key = CONNECTION_QB,
                name = "qBittorrent",
                description = if (qbittorrent.configured) "服务端已配置，点击测试验证连接" else "服务端尚未配置",
                    state = if (qbittorrent.configured) ConnectionState.Configured else ConnectionState.NotConfigured,
            ),
        )
    }

    private fun ConfigurationStatusDto.toSettingsConfiguration(
        isSaving: Boolean = false,
    ): SettingsConfigurationUiState {
        val avistaZ = ptSites.avistaz ?: ptSite?.takeIf {
            it.architecture == PtSiteArchitecture.AVISTAZ
        }
        val nexusPhp = ptSites.nexusphp ?: ptSite?.takeIf {
            it.architecture == PtSiteArchitecture.NEXUSPHP
        }
        return SettingsConfigurationUiState(
            canEdit = currentRole == AuthRole.ADMIN,
            isSaving = isSaving,
            nextFind = NextFindConfigurationUi(
                baseUrl = nextfind.baseUrl,
                username = nextfind.username,
                passwordConfigured = nextfind.passwordConfigured,
                configured = nextfind.configured,
            ),
            tmdb = TmdbConfigurationUi(tokenConfigured = tmdb.configured),
            outboundProxy = OutboundProxyConfigurationUi(
                url = outboundProxy.url,
                username = outboundProxy.username,
                passwordConfigured = outboundProxy.passwordConfigured,
                configured = outboundProxy.configured,
            ),
            qbittorrent = QbittorrentConfigurationUi(
                baseUrl = qbittorrent.url,
                username = qbittorrent.username,
                passwordConfigured = qbittorrent.configured,
                savePath = qbittorrent.savePath,
                category = qbittorrent.category,
                allowInsecureHttp = qbittorrent.allowInsecureHttp,
                configured = qbittorrent.configured,
            ),
            avistaZ = AvistaZConfigurationUi(
                baseUrl = avistaZ?.baseUrl.orEmpty(),
                username = avistaZ?.username.orEmpty(),
                passwordConfigured = avistaZ?.passwordConfigured == true,
                pidConfigured = avistaZ?.pidConfigured == true,
                configured = avistaZ?.configured == true,
                runtimeSupported = avistaZ?.runtimeSupported ?: true,
            ),
            nexusPhp = NexusPhpConfigurationUi(
                siteId = nexusPhp?.siteId.orEmpty(),
                displayName = nexusPhp?.displayName.orEmpty(),
                baseUrl = nexusPhp?.baseUrl.orEmpty(),
                cookieConfigured = nexusPhp?.cookieConfigured == true,
                passkeyConfigured = nexusPhp?.passkeyConfigured == true,
                configured = nexusPhp?.configured == true,
                runtimeSupported = nexusPhp?.runtimeSupported ?: false,
            ),
            activePtArchitecture = when (ptSite?.architecture) {
                PtSiteArchitecture.NEXUSPHP -> PtArchitectureUi.NexusPhp
                else -> PtArchitectureUi.AvistaZ
            },
        )
    }

    private fun signedOutConnections() = listOf(
        ConnectionUi(
            key = CONNECTION_SERVER,
            name = "UNIN 服务",
            description = "等待登录后检查连接",
            state = ConnectionState.Checking,
        ),
    )

    private fun List<ConnectionUi>.withConnection(
        key: String,
        connectionState: ConnectionState,
        description: String,
    ): List<ConnectionUi> = map {
        if (it.key == key) it.copy(state = connectionState, description = description) else it
    }

    private fun List<MissingMediaUi>.replace(media: MissingMediaUi): List<MissingMediaUi> =
        map { if (it.id == media.id) media else it }

    private fun Throwable.isSessionAuthenticationFailure(): Boolean =
        this is HttpException &&
            ApiErrorPolicy.isSessionAuthenticationFailure(
                statusCode = code(),
                errorCode = errorBodySnapshot()?.optString("error_code"),
            )

    private fun HttpException.errorBodySnapshot(): JSONObject? = runCatching {
        val body = peekErrorBody(MAX_ERROR_BODY_BYTES).orEmpty()
        JSONObject(body)
    }.getOrNull()

    private fun Throwable.mayLeaveWriteOutcomeUnknown(): Boolean = when (this) {
        is UnknownHostException,
        is ConnectException,
        is SSLHandshakeException,
        is SSLPeerUnverifiedException,
        -> false
        is IOException -> true
        is HttpException -> code() >= 500
        else -> false
    }

    private fun rememberUnknownAutomationRun() {
        hasUnknownAutomationRun = true
        safetyPreferences.edit()
            .putBoolean(UNKNOWN_AUTOMATION_RUN_KEY, true)
            .apply()
    }

    private fun clearUnknownAutomationRun() {
        hasUnknownAutomationRun = false
        safetyPreferences.edit().remove(UNKNOWN_AUTOMATION_RUN_KEY).apply()
    }

    private fun rememberUnknownDownloadSubmission(mediaId: String) {
        unknownDownloadSubmissionMediaIds = unknownDownloadSubmissionMediaIds + mediaId
        safetyPreferences.edit()
            .putStringSet(UNKNOWN_DOWNLOAD_SUBMISSION_KEY, unknownDownloadSubmissionMediaIds)
            .apply()
    }

    private fun clearUnknownDownloadSubmission(mediaId: String) {
        unknownDownloadSubmissionMediaIds = unknownDownloadSubmissionMediaIds - mediaId
        safetyPreferences.edit().apply {
            if (unknownDownloadSubmissionMediaIds.isEmpty()) {
                remove(UNKNOWN_DOWNLOAD_SUBMISSION_KEY)
            } else {
                putStringSet(UNKNOWN_DOWNLOAD_SUBMISSION_KEY, unknownDownloadSubmissionMediaIds)
            }
        }.apply()
    }

    private fun errorMessage(error: Throwable, fallback: String): String {
        if (error is UserFacingException) return error.message.orEmpty().ifBlank { fallback }
        if (error is SSLException) return "安全连接验证失败，请检查服务器证书和设备时间"
        if (error is IOException) return "无法连接 $serverLabel，请检查网络后重试"
        if (error is HttpException) {
            val serviceMessage = error.errorBodySnapshot()
                ?.optString("message")
                ?.trim()
                ?.take(MAX_ERROR_MESSAGE_LENGTH)
                .orEmpty()
            if (serviceMessage.isNotBlank()) return serviceMessage
            return when (error.code()) {
                400, 422 -> "请求内容无效，请检查输入"
                401 -> "用户名或密码错误"
                403 -> "当前账号没有执行此操作的权限"
                404 -> "请求的内容不存在或已被删除"
                409 -> "当前状态不允许执行此操作"
                429 -> "操作过于频繁，请稍后重试"
                in 500..599 -> "UNIN 服务暂时异常，请稍后重试"
                else -> fallback
            }
        }
        return fallback
    }

    private suspend fun <T> Deferred<T>.awaitResult(): Result<T> = try {
        Result.success(await())
    } catch (error: CancellationException) {
        throw error
    } catch (error: Throwable) {
        Result.failure(error)
    }

    private fun String.siteDisplayName(): String = when (lowercase()) {
        "avistaz" -> "AvistaZ"
        else -> this
    }

    private fun releaseGroup(title: String): String {
        val suffix = title.substringAfterLast('-', "").trim()
        return suffix.takeIf { it.isNotEmpty() && it.none(Char::isWhitespace) } ?: "发布组未知"
    }

    private fun Long?.formatBytes(): String {
        val bytes = this ?: return "体积未知"
        if (bytes < 1024) return "$bytes B"
        val units = arrayOf("KB", "MB", "GB", "TB")
        var value = bytes.toDouble()
        var unit = -1
        do {
            value /= 1024.0
            unit += 1
        } while (value >= 1024 && unit < units.lastIndex)
        return String.format(Locale.US, if (value >= 10) "%.1f %s" else "%.2f %s", value, units[unit])
    }

    private fun Long.formatRate(): String = "${this.formatBytes()}/s"

    private fun formatServerTime(value: String): String {
        val instant = runCatching { Instant.parse(value) }.getOrNull()
            ?: runCatching { OffsetDateTime.parse(value).toInstant() }.getOrNull()
        if (instant != null) {
            return TIME_FORMATTER.format(instant.atZone(SHANGHAI_ZONE))
        }
        return runCatching {
            TIME_FORMATTER.format(LocalDateTime.parse(value))
        }.getOrDefault(value.take(16).replace('T', ' '))
    }

    private class UserFacingException(message: String) : Exception(message)

    private companion object {
        const val PAGE_SIZE = 100
        const val DEFAULT_PT_SITE = "avistaz"
        const val BYTES_PER_GIB = 1024L * 1024L * 1024L
        const val SEARCH_POLL_LIMIT = 60
        const val SEARCH_POLL_INTERVAL_MS = 1_000L
        const val AUTOMATION_RUN_POLL_INTERVAL_MS = 2_000L
        const val MAX_ERROR_MESSAGE_LENGTH = 240
        const val MAX_ERROR_BODY_BYTES = 16_384L
        const val CONNECTION_SERVER = "unin"
        const val CONNECTION_NEXTFIND = "nextfind"
        const val CONNECTION_TMDB = "tmdb"
        const val CONNECTION_PROXY = "outbound_proxy"
        const val CONNECTION_PT = "pt_site"
        const val CONNECTION_QB = "qbittorrent"
        const val SAFETY_PREFERENCES_NAME = "unin_operation_safety"
        const val UNKNOWN_AUTOMATION_RUN_KEY = "unknown_automation_run"
        const val UNKNOWN_DOWNLOAD_SUBMISSION_KEY = "unknown_download_submission"
        val SHANGHAI_ZONE: ZoneId = ZoneId.of("Asia/Shanghai")
        val TIME_FORMATTER: DateTimeFormatter = DateTimeFormatter.ofPattern("MM-dd HH:mm")
    }
}
