package de.tlovex.unin.ui

import androidx.compose.runtime.Immutable

enum class UninDestination {
    Missing,
    Resources,
    Downloads,
    Automation,
    Settings,
}

enum class MediaKind { Movie, Series }
enum class DownloadState { Queued, Downloading, Seeding, Completed, OutcomeUnknown, Failed }
enum class ConnectionState { Connected, Configured, Checking, Disconnected, NotConfigured }
enum class AutomationRunState { Success, DryRun, Running, Failed }

@Immutable
data class MissingMediaUi(
    val id: String,
    val title: String,
    val originalTitle: String = "",
    val year: Int? = null,
    val kind: MediaKind,
    val missingDescription: String,
    val posterUrl: String? = null,
    val tmdbConfirmed: Boolean = false,
    val candidateCount: Int = 0,
    val downloadActive: Boolean = false,
)

@Immutable
data class ResourceCandidateUi(
    val id: String,
    val title: String,
    val siteName: String,
    val releaseGroup: String = "",
    val resolution: String,
    val source: String,
    val codec: String,
    val sizeText: String,
    val seeders: Int,
    val matchScore: Int,
    val freeLeech: Boolean = false,
    val coversMissing: String,
    val riskMessage: String? = null,
)

@Immutable
data class DownloadItemUi(
    val id: String,
    val title: String,
    val subtitle: String,
    val progress: Float,
    val state: DownloadState,
    val speedText: String = "",
    val etaText: String = "",
    val ratioText: String = "",
    val errorMessage: String? = null,
    val mediaId: String = "",
)

@Immutable
data class AutomationPolicyUi(
    val enabled: Boolean = false,
    val dryRun: Boolean = true,
    val autoIdentify: Boolean = true,
    val siteNames: String = "AvistaZ",
    val mediaTypesText: String = "电影、电视剧",
    val minimumScore: Int = 70,
    val minimumSeeders: Int = 1,
    val maximumSizeGb: Int? = null,
    val allowWarnings: Boolean = false,
    val intervalMinutes: Int = 60,
    val retryDelayMinutes: Int = 30,
    val maxAttempts: Int = 3,
    val dailyLimit: Int = 3,
    val dailyDownloadSizeGb: Int? = null,
)

@Immutable
data class AutomationRunUi(
    val id: String,
    val title: String,
    val detail: String,
    val timeText: String,
    val state: AutomationRunState,
)

@Immutable
data class ConnectionUi(
    val key: String,
    val name: String,
    val description: String,
    val state: ConnectionState,
)

@Immutable
data class UninUiState(
    val isAuthenticated: Boolean = false,
    val isBusy: Boolean = false,
    val currentDestination: UninDestination = UninDestination.Missing,
    val username: String = "",
    val password: String = "",
    val loginError: String? = null,
    val accountDisplayName: String = "",
    val serverLabel: String = "unin.tlovex.de",
    val missingMedia: List<MissingMediaUi> = emptyList(),
    val selectedMedia: MissingMediaUi? = null,
    val candidates: List<ResourceCandidateUi> = emptyList(),
    val submittingCandidateId: String? = null,
    val downloadSubmissionUnknownMediaIds: Set<String> = emptySet(),
    val downloads: List<DownloadItemUi> = emptyList(),
    val automationPolicy: AutomationPolicyUi = AutomationPolicyUi(),
    val automationRuns: List<AutomationRunUi> = emptyList(),
    val isAutomationRunInProgress: Boolean = false,
    val automationRunOutcomeUnknown: Boolean = false,
    val connections: List<ConnectionUi> = emptyList(),
    val lastSyncedText: String = "尚未同步",
    val snackbarMessage: String? = null,
)

@Immutable
data class UninCallbacks(
    val onUsernameChanged: (String) -> Unit = {},
    val onPasswordChanged: (String) -> Unit = {},
    val onLogin: () -> Unit = {},
    val onLogout: () -> Unit = {},
    val onDestinationSelected: (UninDestination) -> Unit = {},
    val onSyncMissing: () -> Unit = {},
    val onMediaSelected: (MissingMediaUi) -> Unit = {},
    val onConfirmTmdb: (MissingMediaUi) -> Unit = {},
    val onConfirmTmdbWithId: (MissingMediaUi, Int) -> Unit = { _, _ -> },
    val onSearchResources: (MissingMediaUi) -> Unit = {},
    val onCandidateDownload: (ResourceCandidateUi) -> Unit = {},
    val onRefreshDownloads: () -> Unit = {},
    val onRetryDownload: (DownloadItemUi) -> Unit = {},
    val onAutomationEnabledChanged: (Boolean) -> Unit = {},
    val onDryRunChanged: (Boolean) -> Unit = {},
    val onMinimumScoreChanged: (Int) -> Unit = {},
    val onMinimumSeedersChanged: (Int) -> Unit = {},
    val onMaximumSizeChanged: (Int) -> Unit = {},
    val onDailyLimitChanged: (Int) -> Unit = {},
    val onSaveAutomationPolicy: () -> Unit = {},
    val onRunAutomation: () -> Unit = {},
    val onRefreshAutomation: () -> Unit = {},
    val onRetryAutomation: (AutomationRunUi) -> Unit = {},
    val onTestConnection: (ConnectionUi) -> Unit = {},
    val onDismissMessage: () -> Unit = {},
)
