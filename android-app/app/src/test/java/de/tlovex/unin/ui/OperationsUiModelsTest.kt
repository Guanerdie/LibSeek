package de.tlovex.unin.ui

import de.tlovex.unin.data.model.ActivityCategory
import de.tlovex.unin.data.model.ActivityDto
import de.tlovex.unin.data.model.CleanupPreviewDto
import de.tlovex.unin.data.model.CleanupPreviewEntryDto
import de.tlovex.unin.data.model.DownloadCleanupState
import de.tlovex.unin.data.model.ImportBatchDto
import de.tlovex.unin.data.model.ImportBatchItemDto
import de.tlovex.unin.data.model.ImportBatchState
import de.tlovex.unin.data.model.ImportLineStatus
import de.tlovex.unin.data.model.ImportOutcome
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class OperationsUiModelsTest {
    @Test
    fun `a server time without an offset is UTC, shown in Shanghai time`() {
        // 08:41 UTC is 16:41 in Shanghai; reading it as local time showed 08:41.
        assertEquals("10-12 16:41", formatServerTime("2026-10-12T08:41:00"))
        assertEquals("10-12 16:41", formatServerTime("2026-10-12T08:41:00.123456"))
        assertEquals("10-12 16:41", formatServerTime("2026-10-12T08:41:00Z"))
        assertEquals("2026-10-12 16:41", formatServerTime("2026-10-12T16:41:00+08:00", withYear = true))
    }

    @Test
    fun `the preview is split the way the cleanup page reads it`() {
        val preview = CleanupPreviewDto(
            enabled = true,
            dryRun = false,
            deleteAuthorized = true,
            reclaimableBytes = 0,
            heldReclaimableBytes = 0,
            items = listOf(
                entry("marked", DownloadCleanupState.MARKED, deletesAt = "2026-10-12T00:00:00"),
                entry("queued", DownloadCleanupState.NONE),
                entry("waiting", DownloadCleanupState.NONE, blocked = "尚未确认入库"),
                entry("big", DownloadCleanupState.HELD, size = 9_000, hold = "BACKLOG"),
                entry("small", DownloadCleanupState.HELD, size = 1_000, hold = "BACKLOG"),
                entry("mine", DownloadCleanupState.HELD, size = 5_000, hold = "MANUAL"),
                entry("stuck", DownloadCleanupState.HELD, blocked = "做种不足"),
            ),
        )

        val lists = preview.toLists()

        assertEquals(listOf("marked"), lists.marked.map { it.downloadId })
        assertEquals("10-12 08:00", lists.marked.single().deletesAtText)
        assertEquals(listOf("queued"), lists.queued.map { it.downloadId })
        assertEquals(listOf("waiting"), lists.waiting.map { it.downloadId })
        // Smallest first, so the cheapest to confirm come first.
        assertEquals(listOf("small", "mine", "big"), lists.heldReady.map { it.downloadId })
        assertEquals(listOf("stuck"), lists.heldBlocked.map { it.downloadId })
        // A hold the operator placed is never offered in bulk.
        assertEquals(listOf("small", "big"), lists.heldBacklogReady.map { it.downloadId })
        assertEquals("手动保留", lists.heldReady[1].holdLabel)
        assertEquals(listOf("marked", "queued"), lists.pending.map { it.downloadId })
    }

    @Test
    fun `the cleanup page says when saving would start deleting`() {
        val off = CleanupSettingsUi(enabled = false, dryRun = true)
        val cleanup = CleanupUi(saved = off, form = off.copy(enabled = true, dryRun = false), deleteAuthorized = true)

        assertTrue(cleanup.isDirty)
        assertTrue(cleanup.armsDeletion)
        assertEquals("未启用", cleanup.statusTitle)

        val running = cleanup.copy(saved = cleanup.form)
        assertFalse(running.isDirty)
        assertFalse(running.armsDeletion)
        assertEquals("正在运行", running.statusTitle)
        // Without the server's permission nothing is deleted, whatever the form says.
        assertEquals("未授权删除", running.copy(deleteAuthorized = false).statusTitle)
        assertTrue(running.copy(form = running.form.copy(minSeedingDays = 7)).riskyDays)
    }

    @Test
    fun `cleanup settings are sent within the server's bounds`() {
        val dto = CleanupSettingsUi(afterDays = 0, graceDays = 99, dailyLimit = 1_000).toDto()

        assertEquals(1, dto.afterDays)
        assertEquals(30, dto.graceDays)
        assertEquals(500, dto.dailyLimit)
    }

    @Test
    fun `an activity row says who, how and why`() {
        val entry = ActivityDto(
            id = "a1",
            createdAt = "2026-10-07T03:02:00",
            event = "DOWNLOAD_CLEANUP_DELETED",
            category = ActivityCategory.CLEANUP,
            message = "已删除种子和文件",
            actor = "system:cleanup",
            trigger = "AUTO",
            reason = null,
            details = mapOf(
                "size_bytes" to 2_147_483_648.0,
                "seeding_days" to 12.0,
                "files_deleted" to true,
                "completed_at" to "2026-09-20T00:00:00",
            ),
            mediaId = "m1",
            mediaTitle = "说客",
        ).toUi()

        assertEquals("删除种子和文件", entry.title)
        assertEquals("空间清理", entry.actorText)
        assertEquals("自动", entry.triggerText)
        assertEquals("系统按既定流程执行", entry.reasonText)
        assertEquals("2026-10-07 11:02", entry.timeText)
        assertEquals(
            listOf(
                "体积" to "2.00 GB",
                "已做种" to "12 天",
                "下载完成时间" to "2026-09-20 08:00",
                "文件" to "已连同文件删除",
            ),
            entry.details,
        )
    }

    @Test
    fun `a policy change lists each field from and to`() {
        val rows = detailRows(
            mapOf("changes" to mapOf("cleanup_grace_days" to mapOf("from" to 0.0, "to" to 2.0))),
        )

        assertEquals(listOf("标记后观察天数" to "0 → 2"), rows)
    }

    @Test
    fun `an old row without an actor says so`() {
        assertEquals("未记录", actorLabel(null))
        assertEquals("owner", actorLabel("owner"))
    }

    @Test
    fun `an import downloads matched lines, picked lines and ticked library titles once each`() {
        val missing = ImportChoiceUi("m1", "红宝石戒指", inLibrary = false)
        val first = ImportChoiceUi("m2", "同名（2001 · 电影）", inLibrary = false)
        val second = ImportChoiceUi("m3", "同名（2020 · 电视剧）", inLibrary = false)
        val owned = ImportChoiceUi("m4", "已入库的剧", inLibrary = true)
        var bulk = ImportUi(
            lines = listOf(
                ImportLineUi("红宝石戒指", ImportLineStatus.MATCHED, media = missing),
                ImportLineUi("同名", ImportLineStatus.AMBIGUOUS, choices = listOf(first, second)),
                ImportLineUi("已入库的剧", ImportLineStatus.NOT_MISSING, media = owned),
                ImportLineUi("不存在", ImportLineStatus.NOT_FOUND),
                ImportLineUi("红宝石戒指 2013", ImportLineStatus.MATCHED, media = missing),
            ),
        )

        // Nothing is picked or ticked on the operator's behalf.
        assertEquals(listOf("m1"), bulk.chosen.map { it.mediaId })
        assertEquals("1 部将下载，1 部需要你选择，1 部库中已有、未勾选，1 部不会下载", bulk.countsText)
        assertEquals("需要你选择", bulk.lines!![1].statusText)

        bulk = bulk.copy(
            lines = bulk.lines.mapIndexed { index, line ->
                when (index) {
                    1 -> line.copy(pickedId = "m3")
                    2 -> line.copy(again = true)
                    else -> line
                }
            },
        )

        assertEquals(listOf("m1", "m3", "m4"), bulk.chosen.map { it.mediaId })
        assertEquals(1, bulk.againCount)
        assertEquals("3 部将下载（其中 1 部库中已有），1 部不会下载", bulk.countsText)
    }

    @Test
    fun `a finished batch is summed up by outcome`() {
        val batch = ImportBatchDto(
            id = "b1",
            state = ImportBatchState.FINISHED,
            startedBy = "owner",
            startedAt = "2026-10-07T02:00:00",
            finishedAt = null,
            cancelRequested = null,
            items = listOf(
                item("m1", ImportOutcome.DOWNLOADED, selected = "Ruby.Ring.S01.1080p"),
                item("m2", ImportOutcome.NO_CANDIDATE, message = "做种数不足"),
                item("m3", ImportOutcome.ALREADY_PRESENT),
            ),
        ).toUi()

        assertFalse(batch.isRunning)
        assertEquals("已提交下载 1 部，种子已存在 1 部，没有合格资源 1 部", batch.summary)
        assertEquals("Ruby.Ring.S01.1080p", batch.items[0].note)
        assertEquals("做种数不足", batch.items[1].note)
        assertEquals("10-07 10:00", batch.startedAtText)
    }

    @Test
    fun `a running batch reports progress`() {
        val batch = ImportBatchDto(
            id = "b1",
            state = ImportBatchState.RUNNING,
            startedBy = null,
            startedAt = null,
            finishedAt = null,
            cancelRequested = false,
            items = listOf(item("m1", ImportOutcome.DOWNLOADED), item("m2", ImportOutcome.RUNNING)),
        ).toUi()

        assertEquals("已处理 1 / 2 部", batch.summary)
        assertNull(batch.startedAtText)
    }

    private fun entry(
        id: String,
        state: DownloadCleanupState,
        size: Long = 1_000,
        blocked: String? = null,
        hold: String? = null,
        deletesAt: String? = null,
    ) = CleanupPreviewEntryDto(
        downloadId = id,
        mediaTitle = id,
        name = "$id.mkv",
        sizeBytes = size,
        completedAt = null,
        seedingDays = 12.0,
        requiredSeedingDays = 10,
        cleanupState = state,
        deletesAt = deletesAt,
        blockedReason = blocked,
        holdReason = hold,
    )

    private fun item(
        id: String,
        outcome: ImportOutcome,
        selected: String? = null,
        message: String? = null,
    ) = ImportBatchItemDto(
        mediaId = id,
        title = id,
        outcome = outcome,
        message = message,
        selectedTitle = selected,
        downloadId = null,
        inLibrary = null,
    )
}
