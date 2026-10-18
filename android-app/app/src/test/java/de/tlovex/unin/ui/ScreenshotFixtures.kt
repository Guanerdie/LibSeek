package de.tlovex.unin.ui

import de.tlovex.unin.data.model.ActivityCategory
import de.tlovex.unin.data.model.ImportBatchState
import de.tlovex.unin.data.model.ImportLineStatus
import de.tlovex.unin.data.model.ImportOutcome

/** Realistic screen states for the screenshot tests. */
internal object ScreenshotFixtures {
    private val missing = listOf(
        MissingMediaUi(
            id = "m-1",
            title = "说客",
            originalTitle = "로비스트",
            year = 2007,
            kind = MediaKind.Series,
            missingDescription = "缺少 S01E01–S01E24",
            tmdbConfirmed = true,
            candidateCount = 6,
        ),
        MissingMediaUi(
            id = "m-2",
            title = "机器人之梦",
            originalTitle = "Robot Dreams",
            year = 2023,
            kind = MediaKind.Movie,
            missingDescription = "缺少正片",
            tmdbConfirmed = false,
        ),
        MissingMediaUi(
            id = "m-3",
            title = "黄金新娘",
            originalTitle = "황금신부",
            year = 2007,
            kind = MediaKind.Series,
            missingDescription = "缺少 S01E09",
            tmdbConfirmed = true,
            candidateCount = 3,
            downloadActive = true,
        ),
    )

    private val downloads = listOf(
        DownloadItemUi(
            "d-1", "Sing.My.Crush.S01.1080p.WEB-DL.AAC2.0.H.264", "更新于 10-18 09:20", .64f,
            DownloadState.Downloading, "↓ 18.2 MB/s", ratioText = "分享率 0.12",
            cleanupText = null, canChangeHold = true,
        ),
        DownloadItemUi(
            "d-2", "Blue.Complex.2026.1080p.WEB-DL.H.264", "更新于 10-17 22:10", 1f,
            DownloadState.Seeding, ratioText = "分享率 1.42",
            cleanupText = "保留，不会自动清理", held = true, canChangeHold = true,
        ),
        DownloadItemUi(
            "d-3", "Color.Rush.S02.1080p.WEB-DL", "更新于 10-16 08:00", 1f,
            DownloadState.Seeding, ratioText = "分享率 2.10",
            cleanupText = "已标记待清理（10-18 03:02）",
        ),
    )

    private val cleanupItems = listOf(
        CleanupItemUi("c-1", "说客", "The.Lobbyist.E01-E24.1080p", 12_884_901_888, "已做种 40 天（需 10 天）", "10-19 03:02"),
        CleanupItemUi("c-2", "你开车让我疯狂", "You.Drive.Me.Crazy.S01.1080p", 4_294_967_296, "已做种 21 天（需 10 天）"),
        CleanupItemUi("c-3", "夏日少年", "Summer.Guys.S01.1080p", 2_147_483_648, "已做种 3 天（需 10 天）", blockedReason = "做种未满 10 天"),
        CleanupItemUi("c-4", "合租之家", "Share.House.S01.1080p", 1_073_741_824, "已做种 33 天（需 10 天）", holdReason = HoldReason.Backlog),
        CleanupItemUi("c-5", "幕府将军", "Shogun.S01.2160p", 53_687_091_200, "已做种 60 天（需 10 天）", holdReason = HoldReason.Manual),
    )

    private val activity = listOf(
        ActivityEntryUi(
            id = "a-1", title = "删除种子和文件", message = "已删除 Blue.Complex 和 11.5 GB 文件",
            category = ActivityCategory.CLEANUP, actorText = "空间清理", triggerText = "自动", isAutomatic = true,
            reasonText = "已入库并做种满 10 天，观察期结束", timeText = "2026-10-18 03:02", mediaTitle = "蓝色情结",
            details = listOf("体积" to "11.5 GB", "已做种" to "40 天", "文件" to "已连同文件删除"),
        ),
        ActivityEntryUi(
            id = "a-2", title = "提交下载", message = "Sing.My.Crush.S01.1080p 已提交到 qBittorrent",
            category = ActivityCategory.DOWNLOAD, actorText = "admin", triggerText = "手动", isAutomatic = false,
            reasonText = "批量导入", timeText = "2026-10-18 09:12", mediaTitle = "唱给我心动的你",
        ),
        ActivityEntryUi(
            id = "a-3", title = "修改自动化策略", message = "修改了 2 项设置",
            category = ActivityCategory.SETTINGS, actorText = "admin", triggerText = "手动", isAutomatic = false,
            reasonText = "用户手动操作", timeText = "2026-10-17 21:40",
            details = listOf("标记后观察天数" to "0 → 2"),
        ),
    )

    fun base(destination: UninDestination) = UninUiState(
        isAuthenticated = true,
        canSyncLibrary = true,
        canOperate = true,
        accountDisplayName = "admin · 管理员",
        currentDestination = destination,
        lastSyncedText = "10-18 09:00 同步",
        missingMedia = missing,
        selectedMedia = missing.first(),
        candidates = listOf(
            ResourceCandidateUi(
                id = "r-1",
                title = "The.Lobbyist.2007.S01.1080p.WEB-DL.AAC2.0.H.264-UNIN",
                siteName = "AvistaZ",
                releaseGroup = "UNIN",
                resolution = "1080p",
                source = "WEB-DL",
                codec = "H.264",
                sizeText = "38.4 GB",
                seeders = 24,
                matchScore = 92,
                freeLeech = true,
                coversMissing = "S01E01–S01E24",
            ),
            ResourceCandidateUi(
                id = "r-2",
                title = "The.Lobbyist.2007.S01.720p.HDTV.x264",
                siteName = "AvistaZ",
                resolution = "720p",
                source = "HDTV",
                codec = "x264",
                sizeText = "18.0 GB",
                seeders = 3,
                matchScore = 64,
                coversMissing = "S01E01–S01E24",
                riskMessage = "做种数偏少",
            ),
        ),
        downloads = downloads,
        automationPolicy = AutomationPolicyUi(
            enabled = true,
            dryRun = false,
            minimumScore = 60,
            dailyLimit = 20,
            yearsText = "2000–2010 年",
            regionsText = "韩国、日本",
            qualityWeightsText = "画质 44 · 体积 24 · 做种 13 · 片源 12 · 促销 3",
        ),
        automationRuns = listOf(
            AutomationRunUi("j-1", "说客", "已提交 The.Lobbyist.S01.1080p", "10-18 08:30", AutomationRunState.Success),
            AutomationRunUi("j-2", "黄金新娘", "没有合格资源：做种数不足（3 个）", "10-18 08:30", AutomationRunState.Waiting, canRetry = true),
            AutomationRunUi("j-3", "机器人之梦", "搜索失败：站点超时", "10-17 20:30", AutomationRunState.Failed, canRetry = true),
        ),
        automationStats = AutomationStatsUi(
            libraryCoverageText = "46,583 部缺失 · 本周入库 59 部",
            searchSuccessText = "搜索成功率 82%",
            downloadHealthText = "下载失败 1 次",
            siteLatencyText = "AvistaZ 平均 1.8 秒",
        ),
        cleanup = CleanupUi(
            isLoaded = true,
            saved = CleanupSettingsUi(enabled = true, dryRun = false, graceDays = 2, dailyLimit = 20),
            form = CleanupSettingsUi(enabled = true, dryRun = false, graceDays = 2, dailyLimit = 20),
            deleteAuthorized = true,
            lists = CleanupListsUi(
                marked = listOf(cleanupItems[0]),
                queued = listOf(cleanupItems[1]),
                waiting = listOf(cleanupItems[2]),
                heldReady = listOf(cleanupItems[3], cleanupItems[4]),
            ),
            recent = activity.take(1),
            selectedIds = setOf("c-4"),
        ),
        activity = ActivityUi(entries = activity, total = 3, isLoaded = true),
        bulkImport = ImportUi(
            lines = listOf(
                ImportLineUi("说客", ImportLineStatus.MATCHED, media = ImportChoiceUi("m-1", "说客（2007 · 电视剧）", false)),
                ImportLineUi(
                    "同名",
                    ImportLineStatus.AMBIGUOUS,
                    choices = listOf(
                        ImportChoiceUi("m-7", "同名（2001 · 电影）", false),
                        ImportChoiceUi("m-8", "同名（2020 · 电视剧）", false),
                    ),
                ),
                ImportLineUi(
                    "妇产科",
                    ImportLineStatus.NOT_MISSING,
                    media = ImportChoiceUi("m-9", "妇产科（2010 · 电视剧）", true),
                    note = "库中已有。仍可下载，但这份下载不会被自动清理",
                ),
                ImportLineUi("不存在的片", ImportLineStatus.NOT_FOUND),
            ),
            batch = ImportBatchUi(
                state = ImportBatchState.RUNNING,
                startedBy = "admin",
                startedAtText = "10-18 09:10",
                cancelRequested = false,
                items = listOf(
                    ImportItemUi("m-1", "说客", ImportOutcome.DOWNLOADED, "The.Lobbyist.S01.1080p", false),
                    ImportItemUi("m-3", "黄金新娘", ImportOutcome.RUNNING, null, false),
                    ImportItemUi("m-2", "机器人之梦", ImportOutcome.PENDING, null, false),
                ),
            ),
        ),
        connections = listOf(
            ConnectionUi("unin", "UNIN API", "unin.tlovex.de", ConnectionState.Connected),
            ConnectionUi("nextfind", "NextFind", "缺失影视数据源", ConnectionState.Connected),
            ConnectionUi("tmdb", "TMDB", "影视身份与元数据", ConnectionState.Configured),
            ConnectionUi("pt_site", "AvistaZ", "候选资源检索", ConnectionState.Checking),
            ConnectionUi("qbittorrent", "qBittorrent", "下载与做种管理", ConnectionState.Disconnected),
        ),
    )

    fun login() = UninUiState(username = "admin", serverLabel = "unin.tlovex.de")
}
