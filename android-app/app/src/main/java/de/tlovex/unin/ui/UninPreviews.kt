package de.tlovex.unin.ui

import androidx.compose.runtime.Composable
import androidx.compose.ui.tooling.preview.Preview

@Preview(name = "UNIN Phone", widthDp = 390, heightDp = 844, showBackground = true)
@Composable
private fun PhonePreview() {
    UninApp(state = fakeUninState(), callbacks = UninCallbacks())
}

@Preview(name = "UNIN Tablet", widthDp = 1000, heightDp = 700, showBackground = true)
@Composable
private fun TabletPreview() {
    UninApp(state = fakeUninState(), callbacks = UninCallbacks())
}

@Preview(name = "UNIN Login", widthDp = 390, heightDp = 844, showBackground = true)
@Composable
private fun LoginPreview() {
    UninApp(
        state = UninUiState(username = "admin", serverLabel = "unin.tlovex.de"),
        callbacks = UninCallbacks(),
    )
}

private fun fakeUninState() = UninUiState(
    isAuthenticated = true,
    accountDisplayName = "UNIN 管理员",
    currentDestination = UninDestination.Missing,
    lastSyncedText = "刚刚同步",
    missingMedia = listOf(
        MissingMediaUi(
            id = "m-1",
            title = "沙丘：预言",
            originalTitle = "Dune: Prophecy",
            year = 2024,
            kind = MediaKind.Series,
            missingDescription = "缺少 S01E05–S01E06",
            tmdbConfirmed = true,
            candidateCount = 6,
        ),
        MissingMediaUi(
            id = "m-2",
            title = "机器人之梦",
            originalTitle = "Robot Dreams",
            year = 2023,
            kind = MediaKind.Movie,
            missingDescription = "缺少 1080p 正片",
            tmdbConfirmed = false,
        ),
        MissingMediaUi(
            id = "m-3",
            title = "幕府将军",
            originalTitle = "Shōgun",
            year = 2024,
            kind = MediaKind.Series,
            missingDescription = "缺少 S01E09",
            tmdbConfirmed = true,
            candidateCount = 3,
        ),
    ),
    candidates = listOf(
        ResourceCandidateUi(
            id = "r-1",
            title = "Dune.Prophecy.S01.2160p.WEB-DL.DDP5.1.H.265",
            siteName = "M-Team",
            releaseGroup = "UNIN",
            resolution = "2160p",
            source = "WEB-DL",
            codec = "H.265",
            sizeText = "38.4 GB",
            seeders = 24,
            matchScore = 96,
            freeLeech = true,
            coversMissing = "S01E01–S01E06",
        ),
    ),
    downloads = listOf(
        DownloadItemUi("d-1", "沙丘：预言 S01", "M-Team · 38.4 GB", .64f, DownloadState.Downloading, "18.2 MB/s", "剩余 12 分钟"),
        DownloadItemUi("d-2", "机器人之梦", "HDHome · 11.2 GB", 1f, DownloadState.Seeding, ratioText = "Ratio 1.42"),
    ),
    automationPolicy = AutomationPolicyUi(enabled = true, dryRun = true),
    automationRuns = listOf(
        AutomationRunUi("a-1", "缺失影视日常扫描", "发现 3 项，2 项满足策略", "今天 08:30", AutomationRunState.DryRun),
        AutomationRunUi("a-2", "下载状态同步", "更新 4 个 qBittorrent 任务", "昨天 23:10", AutomationRunState.Success),
    ),
    connections = listOf(
        ConnectionUi("api", "UNIN API", "unin.tlovex.de", ConnectionState.Connected),
        ConnectionUi("nextfind", "NextFind", "缺失影视数据源", ConnectionState.Connected),
        ConnectionUi("tmdb", "TMDB", "影视身份与元数据", ConnectionState.Connected),
        ConnectionUi("pt", "PT 站点", "候选资源检索", ConnectionState.Checking),
        ConnectionUi("qb", "qBittorrent", "下载与做种管理", ConnectionState.Connected),
    ),
)
