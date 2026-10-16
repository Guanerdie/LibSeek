package de.tlovex.unin.ui

import androidx.compose.runtime.Immutable
import de.tlovex.unin.data.model.ActivityCategory
import de.tlovex.unin.ui.screens.SettingsConfigurationCallbacks
import de.tlovex.unin.ui.screens.SettingsConfigurationUiState

enum class UninDestination {
    Missing,
    Resources,
    // Reached from the missing list; back returns there.
    Import,
    Downloads,
    // The second tab of the downloads page.
    Cleanup,
    Automation,
    Activity,
    Settings,
}

enum class MediaKind { Movie, Series }
enum class DownloadState { Queued, Downloading, Seeding, Completed, OutcomeUnknown, Failed }
enum class ConnectionState { Connected, Configured, Checking, Disconnected, NotConfigured }
enum class AutomationRunState { Success, DryRun, Running, Waiting, Superseded, Failed }

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
    // Only the detail response carries these; list rows leave them at their
    // defaults rather than pretending to know.
    val subscribed: Boolean = false,
    // False when subscribed but the policy still runs on filters, so the
    // subscription list is not what automation is reading.
    val subscriptionActive: Boolean = false,
    val automationSummary: String? = null,
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
    // Space reclaim: what will happen to this download's files. Null when the
    // server does not report it.
    val cleanupText: String? = null,
    val held: Boolean = false,
    // Held and unmarked downloads can be switched here; a marked one is kept by
    // removing its tag in qBittorrent, and a deleted one is gone.
    val canChangeHold: Boolean = false,
)

@Immutable
data class AutomationPolicyUi(
    val enabled: Boolean = false,
    val dryRun: Boolean = true,
    val autoIdentify: Boolean = true,
    val siteNames: String = "AvistaZ",
    val mediaTypesText: String = "电影、电视剧",
    val scopeText: String = "按筛选条件",
    val regionsText: String = "全部地区",
    val minimumScore: Int = 70,
    val minimumSeeders: Int = 1,
    val maximumSizeGb: Int? = null,
    val allowWarnings: Boolean = false,
    val intervalMinutes: Int = 60,
    val retryDelayMinutes: Int = 30,
    val maxAttempts: Int = 3,
    val dailyLimit: Int = 3,
    val dailyDownloadSizeGb: Int? = null,
    val yearsText: String = "不限年份",
    // Read-only here: these are edited on the web, where the score histogram
    // gives them context. Shown so the app is honest about what is in effect.
    val cooldownText: String = "24 / 72 / 168 小时",
    val varietyText: String = "已屏蔽",
    val qualityWeightsText: String = "",
    val seederFloor: Int = 3,
)

/**
 * The monitoring numbers, already worded.
 *
 * The web page draws these as charts; on a phone the useful part is the four
 * headline figures, so the formatting happens once in the ViewModel rather
 * than being re-derived in the UI.
 */
@Immutable
data class AutomationStatsUi(
    val windowDays: Int = 7,
    val libraryCoverageText: String,
    val searchSuccessText: String,
    val downloadHealthText: String,
    val siteLatencyText: String,
)

@Immutable
data class AutomationRunUi(
    val id: String,
    val title: String,
    val detail: String,
    val timeText: String,
    val state: AutomationRunState,
    val canRetry: Boolean = false,
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
    val isLibraryLoading: Boolean = false,
    val isLibrarySyncing: Boolean = false,
    val canSyncLibrary: Boolean = false,
    // Operator or admin: may download, hold, release and import.
    val canOperate: Boolean = false,
    val currentDestination: UninDestination = UninDestination.Missing,
    val username: String = "",
    val password: String = "",
    val rememberDevice: Boolean = false,
    val loginError: String? = null,
    val accountDisplayName: String = "",
    val canChangePassword: Boolean = false,
    val isChangingPassword: Boolean = false,
    // Bumped after every successful change so the form can clear itself without
    // the password ever living in this state.
    val passwordChangeCount: Int = 0,
    val serverLabel: String = "unin.tlovex.de",
    val missingMedia: List<MissingMediaUi> = emptyList(),
    val selectedMedia: MissingMediaUi? = null,
    val candidates: List<ResourceCandidateUi> = emptyList(),
    val submittingCandidateId: String? = null,
    val quickFillMediaId: String? = null,
    val downloadSubmissionUnknownMediaIds: Set<String> = emptySet(),
    val downloads: List<DownloadItemUi> = emptyList(),
    val automationPolicy: AutomationPolicyUi = AutomationPolicyUi(),
    val automationRuns: List<AutomationRunUi> = emptyList(),
    val automationStats: AutomationStatsUi? = null,
    val isAutomationRunInProgress: Boolean = false,
    val automationRunOutcomeUnknown: Boolean = false,
    val cleanup: CleanupUi = CleanupUi(),
    val activity: ActivityUi = ActivityUi(),
    val bulkImport: ImportUi = ImportUi(),
    val connections: List<ConnectionUi> = emptyList(),
    val settingsConfiguration: SettingsConfigurationUiState = SettingsConfigurationUiState(),
    val lastSyncedText: String = "尚未同步",
    val snackbarMessage: String? = null,
)

@Immutable
data class UninCallbacks(
    val onUsernameChanged: (String) -> Unit = {},
    val onPasswordChanged: (String) -> Unit = {},
    val onRememberDeviceChanged: (Boolean) -> Unit = {},
    val onLogin: () -> Unit = {},
    val onLogout: () -> Unit = {},
    /**
     * Passwords are callback arguments rather than state fields, for the same
     * reason the configuration secrets are: the host submits them immediately
     * and discards them.
     */
    val onChangePassword: (currentPassword: String, newPassword: String) -> Unit = { _, _ -> },
    val onDestinationSelected: (UninDestination) -> Unit = {},
    val onSyncMissing: () -> Unit = {},
    val onMediaSelected: (MissingMediaUi) -> Unit = {},
    val onConfirmTmdb: (MissingMediaUi) -> Unit = {},
    val onConfirmTmdbWithId: (MissingMediaUi, Int) -> Unit = { _, _ -> },
    val onSearchResources: (MissingMediaUi) -> Unit = {},
    val onQuickFill: (MissingMediaUi) -> Unit = {},
    val onToggleSubscription: (MissingMediaUi, Boolean) -> Unit = { _, _ -> },
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
    val onRefreshCleanup: () -> Unit = {},
    val onCleanupSettingsChanged: (CleanupSettingsUi) -> Unit = {},
    val onSaveCleanupSettings: () -> Unit = {},
    // True keeps the download out of automatic cleanup; false lets it back in.
    val onSetCleanupHold: (downloadId: String, held: Boolean) -> Unit = { _, _ -> },
    val onCleanupSelectionChanged: (Set<String>) -> Unit = {},
    val onReleaseSelected: () -> Unit = {},
    val onActivityCategorySelected: (ActivityCategory?) -> Unit = {},
    val onActivityPageSelected: (Int) -> Unit = {},
    val onRefreshActivity: () -> Unit = {},
    val onImportMatch: (text: String) -> Unit = {},
    val onImportPick: (lineIndex: Int, mediaId: String?) -> Unit = { _, _ -> },
    val onImportAgain: (lineIndex: Int, again: Boolean) -> Unit = { _, _ -> },
    val onImportAgainAll: (Boolean) -> Unit = {},
    val onImportStart: () -> Unit = {},
    val onImportCancel: () -> Unit = {},
    val onImportDiscard: () -> Unit = {},
    val settingsConfiguration: SettingsConfigurationCallbacks = SettingsConfigurationCallbacks(),
    val onDismissMessage: () -> Unit = {},
)
