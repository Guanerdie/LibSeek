package de.tlovex.unin.ui

import androidx.compose.runtime.Immutable
import de.tlovex.unin.data.model.ActivityCategory
import de.tlovex.unin.data.model.ActivityDto
import de.tlovex.unin.data.model.AutomationPolicyDto
import de.tlovex.unin.data.model.CleanupPolicyUpdateDto
import de.tlovex.unin.data.model.CleanupPreviewDto
import de.tlovex.unin.data.model.CleanupPreviewEntryDto
import de.tlovex.unin.data.model.DownloadCleanupState
import de.tlovex.unin.data.model.ImportBatchDto
import de.tlovex.unin.data.model.ImportBatchItemDto
import de.tlovex.unin.data.model.ImportBatchState
import de.tlovex.unin.data.model.ImportLineStatus
import de.tlovex.unin.data.model.ImportMatchLineDto
import de.tlovex.unin.data.model.ImportMediaChoiceDto
import de.tlovex.unin.data.model.ImportOutcome
import de.tlovex.unin.data.model.MediaType
import java.util.Locale

/*
 * Space reclaim, the activity record and bulk import, worded for the screen.
 * Everything here is plain data and pure functions so the rules -- what is
 * about to be deleted, what a batch would download -- can be tested without
 * Android.
 */

// ---------------------------------------------------------------- cleanup

@Immutable
data class CleanupSettingsUi(
    val enabled: Boolean = false,
    val dryRun: Boolean = true,
    val afterDays: Int = 10,
    val minSeedingDays: Int = 10,
    val graceDays: Int = 2,
    val requireLibraryConfirmed: Boolean = true,
    val dailyLimit: Int = 20,
    val releaseBacklog: Boolean = false,
) {
    fun toDto() = CleanupPolicyUpdateDto(
        enabled = enabled,
        dryRun = dryRun,
        afterDays = afterDays.coerceIn(1, 365),
        minSeedingDays = minSeedingDays.coerceIn(1, 365),
        graceDays = graceDays.coerceIn(0, 30),
        requireLibraryConfirmed = requireLibraryConfirmed,
        dailyLimit = dailyLimit.coerceIn(1, 500),
        releaseBacklog = releaseBacklog,
    )
}

/** The cleanup half of the policy; a server without a field means its default. */
internal fun AutomationPolicyDto.toCleanupSettings(): CleanupSettingsUi {
    val defaults = CleanupSettingsUi()
    return CleanupSettingsUi(
        enabled = cleanupEnabled ?: defaults.enabled,
        dryRun = cleanupDryRun ?: defaults.dryRun,
        afterDays = cleanupAfterDays ?: defaults.afterDays,
        minSeedingDays = cleanupMinSeedingDays ?: defaults.minSeedingDays,
        graceDays = cleanupGraceDays ?: defaults.graceDays,
        requireLibraryConfirmed = cleanupRequireLibraryConfirmed ?: defaults.requireLibraryConfirmed,
        dailyLimit = cleanupDailyLimit ?: defaults.dailyLimit,
        releaseBacklog = cleanupReleaseBacklog ?: defaults.releaseBacklog,
    )
}

enum class HoldReason { Backlog, Manual, TagRemoved }

@Immutable
data class CleanupItemUi(
    val downloadId: String,
    val mediaTitle: String,
    val name: String,
    val sizeBytes: Long,
    val seedingText: String,
    val deletesAtText: String? = null,
    val blockedReason: String? = null,
    val holdReason: HoldReason? = null,
) {
    val sizeText: String get() = formatBytes(sizeBytes)

    // A hold the operator placed is theirs; only the backlog is offered in bulk.
    val isBacklog: Boolean get() = holdReason == null || holdReason == HoldReason.Backlog

    val holdLabel: String?
        get() = when (holdReason) {
            HoldReason.Manual -> "手动保留"
            HoldReason.TagRemoved -> "摘标签保留"
            else -> null
        }
}

internal fun CleanupPreviewEntryDto.toUi() = CleanupItemUi(
    downloadId = downloadId,
    mediaTitle = mediaTitle,
    name = name,
    sizeBytes = sizeBytes,
    seedingText = "已做种 ${formatDays(seedingDays)} 天（需 $requiredSeedingDays 天）",
    deletesAtText = deletesAt?.let { formatServerTime(it) },
    blockedReason = blockedReason,
    holdReason = when (holdReason) {
        "MANUAL" -> HoldReason.Manual
        "TAG_REMOVED" -> HoldReason.TagRemoved
        "BACKLOG" -> HoldReason.Backlog
        else -> null
    },
)

/** The preview split the way the cleanup screen reads it. */
@Immutable
data class CleanupListsUi(
    // Tagged; deleted when the grace period ends.
    val marked: List<CleanupItemUi> = emptyList(),
    // Released and meeting every condition: the next cycle tags these.
    val queued: List<CleanupItemUi> = emptyList(),
    // Released but not yet meeting the conditions.
    val waiting: List<CleanupItemUi> = emptyList(),
    // Held, and would be deleted once released. Smallest first.
    val heldReady: List<CleanupItemUi> = emptyList(),
    // Held, and would not be deleted even if released.
    val heldBlocked: List<CleanupItemUi> = emptyList(),
) {
    val pending: List<CleanupItemUi> get() = marked + queued
    val heldBacklogReady: List<CleanupItemUi> get() = heldReady.filter { it.isBacklog }
}

internal fun CleanupPreviewDto.toLists(): CleanupListsUi {
    fun select(state: DownloadCleanupState, ready: Boolean) = items.filter {
        it.cleanupState == state && (it.blockedReason == null) == ready
    }
    return CleanupListsUi(
        marked = items.filter { it.cleanupState == DownloadCleanupState.MARKED }.map { it.toUi() },
        queued = select(DownloadCleanupState.NONE, ready = true).map { it.toUi() },
        waiting = select(DownloadCleanupState.NONE, ready = false).map { it.toUi() },
        heldReady = select(DownloadCleanupState.HELD, ready = true)
            .sortedBy { it.sizeBytes }
            .map { it.toUi() },
        heldBlocked = select(DownloadCleanupState.HELD, ready = false).map { it.toUi() },
    )
}

internal fun totalSize(items: List<CleanupItemUi>): String = formatBytes(items.sumOf { it.sizeBytes })

@Immutable
data class CleanupUi(
    val isLoading: Boolean = false,
    val isLoaded: Boolean = false,
    // What the server is acting on, as opposed to what is being edited.
    val saved: CleanupSettingsUi = CleanupSettingsUi(),
    val form: CleanupSettingsUi = CleanupSettingsUi(),
    // Null until the preview has been read.
    val deleteAuthorized: Boolean? = null,
    val lists: CleanupListsUi = CleanupListsUi(),
    val previewError: String? = null,
    val recent: List<ActivityEntryUi> = emptyList(),
    val selectedIds: Set<String> = emptySet(),
    // The download whose hold is being changed, or "bulk" while releasing several.
    val changingId: String? = null,
    val isSaving: Boolean = false,
) {
    /** Real deletion is on: enabled, not a rehearsal, and allowed by the server. */
    val deleting: Boolean get() = saved.enabled && !saved.dryRun && deleteAuthorized != false

    val statusTitle: String
        get() = when {
            !saved.enabled -> "未启用"
            deleting -> "正在运行"
            saved.dryRun -> "演练中"
            else -> "未授权删除"
        }

    val statusDetail: String
        get() = when {
            !saved.enabled -> "不会标记，也不会删除"
            deleting -> "符合条件的种子会被删除"
            saved.dryRun -> "只记录，不标记也不删除"
            else -> "服务端未开启 ENABLE_QB_DELETE"
        }

    val isDirty: Boolean get() = form != saved

    // Saving this form would start real deletion where there was none.
    val armsDeletion: Boolean get() = form.enabled && !form.dryRun && !deleting

    // AvistaZ asks for seven days, and a client counts more seeding time than
    // the tracker credits.
    val riskyDays: Boolean get() = form.afterDays < 8 || form.minSeedingDays < 8

    // Saving would let the backlog in where it was being kept out.
    val armsBacklog: Boolean get() = form.releaseBacklog && !saved.releaseBacklog

    val selectedItems: List<CleanupItemUi>
        get() = lists.heldBacklogReady.filter { it.downloadId in selectedIds }
}

// --------------------------------------------------------------- activity

private val EVENT_LABELS = mapOf(
    "DOWNLOAD_QUEUED" to "选择资源",
    "DOWNLOAD_RETRYING" to "重新提交下载",
    "DOWNLOAD_SUBMITTED" to "提交下载",
    "DOWNLOAD_FAILED" to "下载提交失败",
    "DOWNLOAD_RECORD_RETIRED" to "旧下载记录作废",
    "DOWNLOAD_CLEANUP_MARKED" to "标记待清理",
    "DOWNLOAD_CLEANUP_DELETED" to "删除种子和文件",
    "DOWNLOAD_CLEANUP_HELD" to "设为保留",
    "DOWNLOAD_CLEANUP_RELEASED" to "允许自动清理",
    "DOWNLOAD_CLEANUP_UNMARKED" to "取消清理标记",
    "DOWNLOAD_CLEANUP_SKIPPED" to "本轮清理已跳过",
    "DOWNLOAD_CLEANUP_PREVIEW" to "清理演练",
    "LIBRARY_CONFIRMED" to "确认入库",
    "LIBRARY_CONFIRMATION_REVOKED" to "撤销入库确认",
    "NEXTFIND_SYNCED" to "同步缺失列表",
    "AUTOMATION_SYNC_FAILED" to "缺失列表同步失败",
    "TMDB_IDENTIFIED" to "确认影视信息",
    "SEARCH_CREATED" to "开始搜索",
    "SEARCH_COMPLETED" to "搜索完成",
    "AUTOMATION_POLICY_UPDATED" to "修改自动化策略",
)

private val SYSTEM_ACTORS = mapOf(
    "system" to "系统",
    "system:scheduler" to "定时调度",
    "system:cleanup" to "空间清理",
)

private val DETAIL_LABELS = linkedMapOf(
    "name" to "种子名称",
    "site_id" to "站点",
    "size_bytes" to "体积",
    "score" to "综合评分",
    "seeding_days" to "已做种",
    "required_seeding_days" to "要求做种",
    "completed_at" to "下载完成时间",
    "library_confirmed_at" to "入库确认时间",
    "marked_at" to "标记时间",
    "grace_days" to "观察期",
    "files_deleted" to "文件",
    "info_hash" to "Info hash",
    "category" to "qB 分类",
    "error_code" to "错误代码",
    "candidate_count" to "候选数量",
    "created" to "新增影视",
    "updated" to "更新影视",
    "warnings" to "列表读取警告",
    "newly_confirmed" to "新确认入库",
    "confirmation_revoked" to "撤销入库确认",
    "id_collisions" to "电影与剧集同号",
    "rejected_confirmations" to "未采信的入库数量",
    "confirmation_limit" to "单次确认上限",
    "markable" to "可标记",
    "deletable" to "可删除",
    "would_reclaim_bytes" to "预计释放",
    "run_trigger" to "运行方式",
    "run_id" to "自动化运行",
    "job_id" to "自动化任务",
    "download_id" to "下载记录",
    "candidate_id" to "候选资源",
)

private val POLICY_FIELD_LABELS = mapOf(
    "enabled" to "启用自动化",
    "dry_run" to "仅试运行",
    "cleanup_enabled" to "启用空间清理",
    "cleanup_dry_run" to "清理演练模式",
    "cleanup_after_days" to "保留天数",
    "cleanup_min_seeding_days" to "最短做种天数",
    "cleanup_grace_days" to "标记后观察天数",
    "cleanup_require_library_confirmed" to "必须已确认入库",
    "cleanup_daily_limit" to "每日最多清理",
    "cleanup_release_backlog" to "自动放行存量",
    "daily_download_limit" to "每日自动下载数量",
    "minimum_score" to "最低评分",
    "scope_mode" to "自动化范围",
    "regions" to "地区",
    "years" to "年份",
    "media_types" to "影视类型",
)

internal fun eventLabel(event: String): String = EVENT_LABELS[event] ?: event

internal fun actorLabel(actor: String?): String =
    if (actor == null) "未记录" else SYSTEM_ACTORS[actor] ?: actor

internal fun categoryLabel(category: ActivityCategory?): String = when (category) {
    ActivityCategory.DOWNLOAD -> "下载"
    ActivityCategory.CLEANUP -> "删种"
    ActivityCategory.LIBRARY -> "入库"
    ActivityCategory.SEARCH -> "搜索"
    ActivityCategory.SETTINGS -> "设置"
    null -> "全部"
}

@Immutable
data class ActivityEntryUi(
    val id: String,
    val title: String,
    val message: String,
    val category: ActivityCategory?,
    val actorText: String,
    // 手动 / 自动 / 未记录
    val triggerText: String,
    val isAutomatic: Boolean,
    val reasonText: String,
    val timeText: String,
    val mediaTitle: String? = null,
    val details: List<Pair<String, String>> = emptyList(),
)

internal fun ActivityDto.toUi() = ActivityEntryUi(
    id = id,
    title = eventLabel(event),
    message = message,
    category = category,
    actorText = actorLabel(actor),
    triggerText = when (trigger) {
        "MANUAL" -> "手动"
        "AUTO" -> "自动"
        else -> "未记录"
    },
    isAutomatic = trigger == "AUTO",
    // A person's own action needs no justification.
    reasonText = reason?.takeIf { it.isNotBlank() } ?: when (trigger) {
        "MANUAL" -> "用户手动操作"
        "AUTO" -> "系统按既定流程执行"
        else -> "这条记录产生时还没有记录原因"
    },
    timeText = formatServerTime(createdAt, withYear = true),
    mediaTitle = mediaTitle,
    details = detailRows(details.orEmpty()),
)

/** The details object as labelled rows, most useful first. */
internal fun detailRows(details: Map<String, Any?>): List<Pair<String, String>> {
    val rows = mutableListOf<Pair<String, String>>()
    (details["changes"] as? Map<*, *>)?.forEach { (field, change) ->
        val values = change as? Map<*, *>
        rows += (POLICY_FIELD_LABELS[field] ?: field.toString()) to
            "${plainValue(values?.get("from"))} → ${plainValue(values?.get("to"))}"
    }
    val known = DETAIL_LABELS.keys.filter { it in details }
    val unknown = details.keys.filter { it != "changes" && it !in DETAIL_LABELS }
    (known + unknown).forEach { key ->
        rows += (DETAIL_LABELS[key] ?: key) to detailValue(key, details[key])
    }
    return rows
}

private fun plainValue(value: Any?): String = when (value) {
    null -> "—"
    is Boolean -> if (value) "是" else "否"
    is List<*> -> if (value.isEmpty()) "（空）" else value.joinToString("、") { plainValue(it) }
    // Gson reads every JSON number as a Double.
    is Double -> if (value % 1.0 == 0.0) value.toLong().toString() else value.toString()
    else -> value.toString()
}

private fun detailValue(key: String, value: Any?): String {
    if (value == null) return "—"
    if (value is Number) {
        val number = value.toDouble()
        return when {
            key.endsWith("_bytes") -> formatBytes(number.toLong())
            key in setOf("seeding_days", "required_seeding_days", "grace_days") ->
                "${formatDays(number)} 天"
            key == "score" -> String.format(Locale.US, "%.2f", number)
            else -> plainValue(number)
        }
    }
    if (value is String) {
        if (key.endsWith("_at")) return formatServerTime(value, withYear = true)
        if (key == "run_trigger") return if (value == "scheduled") "定时运行" else "手动启动"
    }
    if (key == "files_deleted") return if (value == true) "已连同文件删除" else "未删除文件"
    return plainValue(value)
}

@Immutable
data class ActivityUi(
    val entries: List<ActivityEntryUi> = emptyList(),
    val category: ActivityCategory? = null,
    // Pages are 1-based, as the server counts them.
    val page: Int = 1,
    val pageSize: Int = 30,
    val total: Int = 0,
    val isLoading: Boolean = false,
    val isLoaded: Boolean = false,
) {
    val pageCount: Int get() = if (total <= 0) 1 else (total + pageSize - 1) / pageSize
}

// ----------------------------------------------------------------- import

@Immutable
data class ImportChoiceUi(
    val mediaId: String,
    val label: String,
    val inLibrary: Boolean,
)

internal fun ImportMediaChoiceDto.toUi(): ImportChoiceUi {
    val kind = if (mediaType == MediaType.TV) "电视剧" else "电影"
    return ImportChoiceUi(
        mediaId = mediaId,
        label = "$title（${year ?: "年份未知"} · $kind）",
        inLibrary = inLibrary == true,
    )
}

@Immutable
data class ImportLineUi(
    val raw: String,
    val status: ImportLineStatus,
    val media: ImportChoiceUi? = null,
    val choices: List<ImportChoiceUi> = emptyList(),
    val note: String? = null,
    // The operator's pick on an unclear line. Nothing is picked for them.
    val pickedId: String? = null,
    // Download it anyway although it is in the library already.
    val again: Boolean = false,
) {
    val statusText: String
        get() = when (status) {
            ImportLineStatus.MATCHED -> "将下载"
            ImportLineStatus.AMBIGUOUS -> if (chosen == null) "需要你选择" else "将下载"
            ImportLineStatus.NOT_MISSING -> if (again) "将下载" else "库中已有"
            ImportLineStatus.DOWNLOADING -> "已在下载"
            ImportLineStatus.NOT_FOUND -> "没有找到"
            ImportLineStatus.DUPLICATE -> "重复"
        }

    /** What this line would download, if anything. */
    val chosen: ImportChoiceUi?
        get() = when (status) {
            ImportLineStatus.MATCHED -> media
            ImportLineStatus.NOT_MISSING -> media.takeIf { again }
            ImportLineStatus.AMBIGUOUS -> choices.firstOrNull { it.mediaId == pickedId }
            else -> null
        }
}

internal fun ImportMatchLineDto.toUi() = ImportLineUi(
    raw = raw,
    status = status,
    media = media?.toUi(),
    choices = choices.orEmpty().map { it.toUi() },
    note = note,
)

/** The titles a batch would download, each once, in the order pasted. */
internal fun List<ImportLineUi>.chosenMedia(): List<ImportChoiceUi> =
    mapNotNull { it.chosen }.distinctBy { it.mediaId }

@Immutable
data class ImportItemUi(
    val mediaId: String,
    val title: String,
    val outcome: ImportOutcome,
    val note: String?,
    val inLibrary: Boolean,
) {
    val outcomeText: String
        get() = when (outcome) {
            ImportOutcome.PENDING -> "等待中"
            ImportOutcome.RUNNING -> "搜索中…"
            ImportOutcome.DOWNLOADED -> "已提交下载"
            ImportOutcome.ALREADY_PRESENT -> "种子已存在"
            ImportOutcome.NO_CANDIDATE -> "没有合格资源"
            ImportOutcome.FAILED -> "失败"
            ImportOutcome.CANCELLED -> "已取消"
        }

    val isFinished: Boolean
        get() = outcome != ImportOutcome.PENDING && outcome != ImportOutcome.RUNNING
}

internal fun ImportBatchItemDto.toUi() = ImportItemUi(
    mediaId = mediaId,
    title = title,
    outcome = outcome,
    note = if (outcome == ImportOutcome.DOWNLOADED) selectedTitle else message,
    inLibrary = inLibrary == true,
)

@Immutable
data class ImportBatchUi(
    val state: ImportBatchState,
    val startedBy: String?,
    val startedAtText: String?,
    val cancelRequested: Boolean,
    val items: List<ImportItemUi>,
) {
    val isRunning: Boolean get() = state == ImportBatchState.RUNNING

    val summary: String
        get() {
            val done = items.count { it.isFinished }
            if (isRunning) return "已处理 $done / ${items.size} 部"
            val parts = listOfNotNull(
                count(ImportOutcome.DOWNLOADED)?.let { "已提交下载 $it 部" },
                count(ImportOutcome.ALREADY_PRESENT)?.let { "种子已存在 $it 部" },
                count(ImportOutcome.NO_CANDIDATE)?.let { "没有合格资源 $it 部" },
                count(ImportOutcome.FAILED)?.let { "失败 $it 部" },
                count(ImportOutcome.CANCELLED)?.let { "已取消 $it 部" },
            )
            return parts.joinToString("，").ifEmpty { "没有处理任何影视" }
        }

    private fun count(outcome: ImportOutcome): Int? =
        items.count { it.outcome == outcome }.takeIf { it > 0 }
}

internal fun ImportBatchDto.toUi() = ImportBatchUi(
    state = state,
    startedBy = startedBy,
    startedAtText = startedAt?.let { formatServerTime(it) },
    cancelRequested = cancelRequested == true,
    items = items.orEmpty().map { it.toUi() },
)

@Immutable
data class ImportUi(
    val isMatching: Boolean = false,
    val isStarting: Boolean = false,
    val lines: List<ImportLineUi>? = null,
    val truncated: Boolean = false,
    // The current or last batch; null when there has not been one.
    val batch: ImportBatchUi? = null,
    val error: String? = null,
    // Bumped when a batch starts so the screen can clear the pasted list.
    val startedCount: Int = 0,
) {
    val chosen: List<ImportChoiceUi> get() = lines.orEmpty().chosenMedia()
    val againCount: Int get() = chosen.count { it.inLibrary }
    val undecidedCount: Int
        get() = lines.orEmpty().count { it.status == ImportLineStatus.AMBIGUOUS && it.chosen == null }
    val inLibraryLines: List<ImportLineUi>
        get() = lines.orEmpty().filter { it.status == ImportLineStatus.NOT_MISSING && it.media != null }
    val skippedCount: Int
        get() = lines.orEmpty().count {
            it.status != ImportLineStatus.MATCHED &&
                it.status != ImportLineStatus.AMBIGUOUS &&
                it.status != ImportLineStatus.NOT_MISSING
        }

    val countsText: String
        get() {
            val ready = chosen.size
            val notTicked = inLibraryLines.count { !it.again }
            return listOfNotNull(
                if (againCount > 0) "$ready 部将下载（其中 $againCount 部库中已有）" else "$ready 部将下载",
                undecidedCount.takeIf { it > 0 }?.let { "$it 部需要你选择" },
                notTicked.takeIf { it > 0 }?.let { "$it 部库中已有、未勾选" },
                skippedCount.takeIf { it > 0 }?.let { "$it 部不会下载" },
            ).joinToString("，")
        }
}
