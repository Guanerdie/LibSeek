package de.tlovex.unin.ui.screens

import androidx.compose.runtime.Immutable

enum class PtArchitectureUi {
    AvistaZ,
    NexusPhp,
}

@Immutable
data class NextFindConfigurationUi(
    val baseUrl: String = "",
    val username: String = "",
    val passwordConfigured: Boolean = false,
    val configured: Boolean = false,
)

@Immutable
data class TmdbConfigurationUi(
    val tokenConfigured: Boolean = false,
)

@Immutable
data class OutboundProxyConfigurationUi(
    val url: String = "",
    val username: String = "",
    val passwordConfigured: Boolean = false,
    val configured: Boolean = false,
)

@Immutable
data class QbittorrentConfigurationUi(
    val baseUrl: String = "",
    val username: String = "",
    val passwordConfigured: Boolean = false,
    val savePath: String = "",
    val category: String = "",
    val allowInsecureHttp: Boolean = false,
    val configured: Boolean = false,
)

@Immutable
data class AvistaZConfigurationUi(
    val baseUrl: String = "",
    val username: String = "",
    val passwordConfigured: Boolean = false,
    val pidConfigured: Boolean = false,
    val configured: Boolean = false,
    val runtimeSupported: Boolean = true,
)

@Immutable
data class NexusPhpConfigurationUi(
    val siteId: String = "",
    val displayName: String = "",
    val baseUrl: String = "",
    val cookieConfigured: Boolean = false,
    val passkeyConfigured: Boolean = false,
    val configured: Boolean = false,
    val runtimeSupported: Boolean = false,
)

@Immutable
data class SettingsConfigurationUiState(
    val canEdit: Boolean = false,
    val isSaving: Boolean = false,
    val nextFind: NextFindConfigurationUi = NextFindConfigurationUi(),
    val tmdb: TmdbConfigurationUi = TmdbConfigurationUi(),
    val outboundProxy: OutboundProxyConfigurationUi = OutboundProxyConfigurationUi(),
    val qbittorrent: QbittorrentConfigurationUi = QbittorrentConfigurationUi(),
    val avistaZ: AvistaZConfigurationUi = AvistaZConfigurationUi(),
    val nexusPhp: NexusPhpConfigurationUi = NexusPhpConfigurationUi(),
    val activePtArchitecture: PtArchitectureUi = PtArchitectureUi.AvistaZ,
)

/**
 * Configuration secrets are intentionally callback arguments rather than fields in
 * [SettingsConfigurationUiState]. The host must submit them immediately, must not log them, and
 * must discard them after the request completes. A null secret means "keep the stored value".
 */
@Immutable
data class SettingsConfigurationCallbacks(
    val onSaveNextFind: (
        baseUrl: String,
        username: String,
        replacementPassword: String?,
    ) -> Unit = { _, _, _ -> },
    val onSaveTmdb: (replacementToken: String) -> Unit = {},
    val onSaveOutboundProxy: (
        url: String,
        username: String,
        replacementPassword: String?,
    ) -> Unit = { _, _, _ -> },
    val onSaveQbittorrent: (
        baseUrl: String,
        username: String,
        replacementPassword: String?,
        savePath: String,
        category: String,
        allowInsecureHttp: Boolean,
    ) -> Unit = { _, _, _, _, _, _ -> },
    val onSaveAvistaZ: (
        baseUrl: String,
        username: String,
        replacementPassword: String?,
        replacementPid: String?,
    ) -> Unit = { _, _, _, _ -> },
    val onSaveNexusPhp: (
        siteId: String,
        displayName: String,
        baseUrl: String,
        replacementCookie: String?,
        replacementPasskey: String?,
    ) -> Unit = { _, _, _, _, _ -> },
)
