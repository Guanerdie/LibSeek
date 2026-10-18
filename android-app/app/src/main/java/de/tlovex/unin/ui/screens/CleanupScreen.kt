package de.tlovex.unin.ui.screens

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.IntrinsicSize
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.ExpandLess
import androidx.compose.material.icons.outlined.ExpandMore
import androidx.compose.material.icons.outlined.Refresh
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Checkbox
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import de.tlovex.unin.data.model.ActivityCategory
import de.tlovex.unin.ui.CleanupItemUi
import de.tlovex.unin.ui.CleanupSettingsUi
import de.tlovex.unin.ui.CleanupUi
import de.tlovex.unin.ui.UninCallbacks
import de.tlovex.unin.ui.UninDestination
import de.tlovex.unin.ui.UninUiState
import de.tlovex.unin.ui.components.GradientPrimaryButton
import de.tlovex.unin.ui.components.NeumorphicCard
import de.tlovex.unin.ui.components.SectionHeading
import de.tlovex.unin.ui.components.StatusPill
import de.tlovex.unin.ui.theme.UninBlue
import de.tlovex.unin.ui.theme.UninDanger
import de.tlovex.unin.ui.theme.UninSuccess
import de.tlovex.unin.ui.theme.UninWarning
import de.tlovex.unin.ui.totalSize

@Composable
fun CleanupScreen(
    state: UninUiState,
    callbacks: UninCallbacks,
    contentPadding: PaddingValues,
    modifier: Modifier = Modifier,
) {
    val cleanup = state.cleanup
    val lists = cleanup.lists
    val canOperate = state.canOperate
    val busy = cleanup.changingId != null
    var confirmSave by remember { mutableStateOf(false) }
    var confirmRelease by remember { mutableStateOf(false) }
    var showBlocked by rememberSaveable { mutableStateOf(false) }

    LazyColumn(
        modifier = modifier,
        contentPadding = contentPadding,
        verticalArrangement = Arrangement.spacedBy(14.dp),
    ) {
        item { DownloadsTabs(UninDestination.Cleanup, callbacks.onDestinationSelected) }
        item {
            SectionHeading(
                title = "空间清理",
                subtitle = "资源入库后，下载目录里那一份就多余了。这里决定哪些种子连同文件一起删除。",
                action = {
                    TextButton(
                        onClick = callbacks.onRefreshCleanup,
                        enabled = !cleanup.isLoading,
                        modifier = Modifier.height(48.dp),
                    ) {
                        Icon(Icons.Outlined.Refresh, contentDescription = null)
                        Spacer(Modifier.size(4.dp))
                        Text("刷新")
                    }
                },
            )
        }
        if (!cleanup.isLoaded) {
            item {
                NeumorphicCard(Modifier.fillMaxWidth()) {
                    Text(
                        if (cleanup.isLoading) "正在读取清理设置…" else "还没有读取到清理设置，点「刷新」重试。",
                        Modifier.padding(24.dp),
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
            }
            return@LazyColumn
        }

        item { CleanupSummary(cleanup) }
        cleanup.previewError?.let { message ->
            item { Text(message, color = UninWarning, style = MaterialTheme.typography.bodyMedium) }
        }
        item {
            CleanupSettingsCard(
                cleanup = cleanup,
                enabled = canOperate && !cleanup.isSaving,
                onChange = callbacks.onCleanupSettingsChanged,
                onSave = {
                    if (cleanup.armsDeletion || cleanup.armsBacklog) confirmSave = true else callbacks.onSaveCleanupSettings()
                },
            )
        }

        // ------------------------------------------------- 待删除
        item {
            ListHeading(
                title = "待删除",
                detail = "已放行的种子。先打上 unin-cleanup 标签，观察期过后删除种子和文件；在 qBittorrent 里摘掉标签可以保住它。",
            )
        }
        if (lists.marked.isEmpty() && lists.queued.isEmpty() && lists.waiting.isEmpty()) {
            item { EmptyLine("没有已放行的种子。从下面「保留中」里放行，它们才会进入这里。") }
        }
        items(lists.marked, key = { "marked-${it.downloadId}" }) { item ->
            CleanupRow(
                item,
                detail = "${item.name} · ${item.sizeText}",
                status = (item.deletesAtText?.let { "已标记 · $it 后删除" } ?: "已标记") to UninDanger,
            )
        }
        items(lists.queued, key = { "queued-${it.downloadId}" }) { item ->
            CleanupRow(
                item,
                detail = "${item.name} · ${item.sizeText} · ${item.seedingText}",
                status = (if (cleanup.deleting) "下一轮标记" else "启用后标记") to UninBlue,
            ) {
                TextButton(
                    onClick = { callbacks.onSetCleanupHold(item.downloadId, true) },
                    enabled = canOperate && !busy,
                ) { Text("保留") }
            }
        }
        items(lists.waiting, key = { "waiting-${it.downloadId}" }) { item ->
            CleanupRow(item, detail = "${item.name} · 暂不满足：${item.blockedReason}") {
                TextButton(
                    onClick = { callbacks.onSetCleanupHold(item.downloadId, true) },
                    enabled = canOperate && !busy,
                ) { Text("保留") }
            }
        }

        // ------------------------------------------------- 保留中
        item {
            ListHeading(
                title = "保留中",
                detail = "不会被自动清理。下面 ${lists.heldReady.size} 项已满足全部条件，放行后就会被删除；放行前请先确认媒体库里那一份完好。",
            )
        }
        if (cleanup.saved.releaseBacklog && lists.heldBacklogReady.isNotEmpty()) {
            item {
                Text(
                    "「自动放行存量」已开启：下面没有标注「手动保留」的种子会在下一轮自动进入待删除，不需要逐个放行。",
                    color = UninWarning,
                    style = MaterialTheme.typography.bodyMedium,
                )
            }
        }
        if (lists.heldReady.isEmpty()) {
            item { EmptyLine("没有可以放行的种子。") }
        } else {
            item {
                BulkReleaseBar(
                    cleanup = cleanup,
                    enabled = canOperate && !busy,
                    onSelectAll = { all ->
                        callbacks.onCleanupSelectionChanged(
                            if (all) lists.heldBacklogReady.mapTo(mutableSetOf()) { it.downloadId } else emptySet(),
                        )
                    },
                    onRelease = { confirmRelease = true },
                )
            }
        }
        items(lists.heldReady, key = { "held-${it.downloadId}" }) { item ->
            val selected = item.downloadId in cleanup.selectedIds
            CleanupRow(
                item = item,
                detail = "${item.name} · ${item.sizeText} · ${item.seedingText}",
                leading = if (item.isBacklog) {
                    {
                        Checkbox(
                            checked = selected,
                            onCheckedChange = { checked ->
                                callbacks.onCleanupSelectionChanged(
                                    if (checked) cleanup.selectedIds + item.downloadId else cleanup.selectedIds - item.downloadId,
                                )
                            },
                            enabled = canOperate && !busy,
                        )
                    }
                } else {
                    null
                },
            ) {
                TextButton(
                    onClick = { callbacks.onSetCleanupHold(item.downloadId, false) },
                    enabled = canOperate && !busy,
                ) { Text(if (cleanup.changingId == item.downloadId) "放行中…" else "放行") }
            }
        }
        if (lists.heldBlocked.isNotEmpty()) {
            item {
                Row(
                    Modifier
                        .fillMaxWidth()
                        .clickable { showBlocked = !showBlocked }
                        .padding(vertical = 8.dp),
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    Text(
                        "另有 ${lists.heldBlocked.size} 项即使放行也不会删除",
                        Modifier.weight(1f),
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                    Icon(if (showBlocked) Icons.Outlined.ExpandLess else Icons.Outlined.ExpandMore, contentDescription = null)
                }
            }
            if (showBlocked) {
                items(lists.heldBlocked, key = { "blocked-${it.downloadId}" }) { item ->
                    CleanupRow(item, detail = "${item.name} · ${item.blockedReason}")
                }
            }
        }

        // ------------------------------------------------- records
        item {
            SectionHeading(
                title = "最近的清理记录",
                action = {
                    TextButton(onClick = {
                        callbacks.onDestinationSelected(UninDestination.Activity)
                        callbacks.onActivityCategorySelected(ActivityCategory.CLEANUP)
                    }) { Text("全部记录") }
                },
            )
        }
        if (cleanup.recent.isEmpty()) {
            item { EmptyLine("还没有清理记录。") }
        }
        items(cleanup.recent, key = { "recent-${it.id}" }) { entry ->
            NeumorphicCard(Modifier.fillMaxWidth()) {
                Column(Modifier.padding(14.dp)) {
                    Text(entry.title, style = MaterialTheme.typography.titleSmall)
                    Text(entry.message, style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
                    Text(
                        "${entry.actorText} · ${entry.timeText}",
                        style = MaterialTheme.typography.labelMedium,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
            }
        }
    }

    if (confirmSave) {
        AlertDialog(
            onDismissRequest = { confirmSave = false },
            title = { Text("确认保存清理设置") },
            text = { Text(saveWarning(cleanup)) },
            confirmButton = {
                TextButton(onClick = {
                    confirmSave = false
                    callbacks.onSaveCleanupSettings()
                }) { Text("确认保存", color = UninDanger) }
            },
            dismissButton = { TextButton(onClick = { confirmSave = false }) { Text("取消") } },
        )
    }
    if (confirmRelease) {
        val selected = cleanup.selectedItems
        AlertDialog(
            onDismissRequest = { confirmRelease = false },
            title = { Text("放行 ${selected.size} 项？") },
            text = {
                Text(
                    "共 ${totalSize(selected)}。放行后它们会在下一轮被打上标签，观察 ${cleanup.saved.graceDays} 天后连同文件一起删除，删除不可恢复。请先确认媒体库里那一份完好。",
                )
            },
            confirmButton = {
                TextButton(onClick = {
                    confirmRelease = false
                    callbacks.onReleaseSelected()
                }) { Text("确认放行", color = UninDanger) }
            },
            dismissButton = { TextButton(onClick = { confirmRelease = false }) { Text("取消") } },
        )
    }
}

private fun saveWarning(cleanup: CleanupUi): String = buildList {
    if (cleanup.armsDeletion) {
        add("保存后将开始真正删除：「待删除」里的 ${cleanup.lists.pending.size} 项会在下一轮被标记，观察期后连同文件删除，删除不可恢复。")
    }
    if (cleanup.armsBacklog) {
        val backlog = cleanup.lists.heldBacklogReady
        add(
            "「保留中」里 ${backlog.size} 项满足条件的存量（${totalSize(backlog)}）会在下一轮自动放行，观察 ${cleanup.form.graceDays} 天后删除，每天最多删 ${cleanup.form.dailyLimit} 个；以后其余存量满足条件时也会自动处理。手动保留和摘过标签的不受影响。",
        )
    }
}.joinToString("\n\n")

@Composable
private fun CleanupSummary(cleanup: CleanupUi) {
    val lists = cleanup.lists
    Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
        Row(Modifier.height(IntrinsicSize.Min), horizontalArrangement = Arrangement.spacedBy(10.dp)) {
            SummaryTile(
                "当前状态",
                cleanup.statusTitle,
                cleanup.statusDetail,
                if (cleanup.deleting) UninWarning else MaterialTheme.colorScheme.onSurface,
                Modifier.weight(1f).fillMaxHeight(),
            )
            SummaryTile(
                "待删除",
                "${lists.pending.size} 项",
                totalSize(lists.pending),
                MaterialTheme.colorScheme.onSurface,
                Modifier.weight(1f).fillMaxHeight(),
            )
        }
        Row(Modifier.height(IntrinsicSize.Min), horizontalArrangement = Arrangement.spacedBy(10.dp)) {
            SummaryTile(
                "保留中，可放行",
                "${lists.heldReady.size} 项",
                totalSize(lists.heldReady),
                MaterialTheme.colorScheme.onSurface,
                Modifier.weight(1f).fillMaxHeight(),
            )
            SummaryTile(
                "删除前观察",
                "${cleanup.saved.graceDays} 天",
                "打上标签后等这么久才删",
                MaterialTheme.colorScheme.onSurface,
                Modifier.weight(1f).fillMaxHeight(),
            )
        }
    }
}

@Composable
private fun SummaryTile(
    label: String,
    value: String,
    detail: String,
    accent: androidx.compose.ui.graphics.Color,
    modifier: Modifier = Modifier,
) {
    NeumorphicCard(modifier) {
        Column(Modifier.padding(14.dp)) {
            Text(label, style = MaterialTheme.typography.labelMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
            Spacer(Modifier.height(4.dp))
            Text(value, style = MaterialTheme.typography.titleLarge, color = accent, fontWeight = FontWeight.SemiBold)
            Text(
                detail,
                style = MaterialTheme.typography.labelMedium,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                maxLines = 2,
                overflow = TextOverflow.Ellipsis,
            )
        }
    }
}

@Composable
private fun CleanupSettingsCard(
    cleanup: CleanupUi,
    enabled: Boolean,
    onChange: (CleanupSettingsUi) -> Unit,
    onSave: () -> Unit,
) {
    val form = cleanup.form
    NeumorphicCard(Modifier.fillMaxWidth()) {
        Column(Modifier.padding(18.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
            Text("设置", style = MaterialTheme.typography.titleMedium)
            Text(
                "删除不可逆。真正删除需要同时满足：启用、关闭演练模式、服务端开启 ENABLE_QB_DELETE" +
                    if (cleanup.deleteAuthorized == false) "（当前未开启）。" else "。",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            SwitchLine("启用空间清理", form.enabled, enabled) { onChange(form.copy(enabled = it)) }
            SwitchLine("演练模式（只记录，不删除）", form.dryRun, enabled) { onChange(form.copy(dryRun = it)) }
            SwitchLine("必须已确认入库", form.requireLibraryConfirmed, enabled) {
                onChange(form.copy(requireLibraryConfirmed = it))
            }
            SwitchLine("自动放行存量（满足条件即清理）", form.releaseBacklog, enabled) {
                onChange(form.copy(releaseBacklog = it))
            }
            HorizontalDivider()
            Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                NumberField("保留天数", form.afterDays, enabled, Modifier.weight(1f)) { onChange(form.copy(afterDays = it)) }
                NumberField("最短做种天数", form.minSeedingDays, enabled, Modifier.weight(1f)) {
                    onChange(form.copy(minSeedingDays = it))
                }
            }
            Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                NumberField("标记后观察天数", form.graceDays, enabled, Modifier.weight(1f)) { onChange(form.copy(graceDays = it)) }
                NumberField("每日最多删除", form.dailyLimit, enabled, Modifier.weight(1f)) { onChange(form.copy(dailyLimit = it)) }
            }
            if (cleanup.riskyDays) {
                Text(
                    "AvistaZ 要求做种满 7 天，而客户端统计的做种时长通常比站点认可的更长。低于 8 天有被记 H&R 的风险，建议保持 10 天。",
                    color = UninWarning,
                    style = MaterialTheme.typography.bodySmall,
                )
            }
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text(
                    if (cleanup.isDirty) "有未保存的修改，点「保存设置」后才生效" else "当前显示的就是正在生效的设置",
                    Modifier.weight(1f),
                    style = MaterialTheme.typography.bodySmall,
                    color = if (cleanup.isDirty) UninWarning else MaterialTheme.colorScheme.onSurfaceVariant,
                )
                Spacer(Modifier.width(10.dp))
                GradientPrimaryButton(
                    text = "保存设置",
                    onClick = onSave,
                    enabled = enabled && cleanup.isDirty,
                    loading = cleanup.isSaving,
                )
            }
        }
    }
}

@Composable
private fun SwitchLine(label: String, checked: Boolean, enabled: Boolean, onChange: (Boolean) -> Unit) {
    Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
        Text(label, Modifier.weight(1f), style = MaterialTheme.typography.bodyMedium)
        Switch(checked = checked, onCheckedChange = onChange, enabled = enabled)
    }
}

/** A whole-number field that only reports a value once it is one. */
@Composable
internal fun NumberField(
    label: String,
    value: Int,
    enabled: Boolean,
    modifier: Modifier = Modifier,
    onValue: (Int) -> Unit,
) {
    var text by remember(value) { mutableStateOf(value.toString()) }
    OutlinedTextField(
        value = text,
        onValueChange = { input ->
            val digits = input.filter(Char::isDigit).take(3)
            text = digits
            digits.toIntOrNull()?.let(onValue)
        },
        label = { Text(label, maxLines = 1, overflow = TextOverflow.Ellipsis) },
        enabled = enabled,
        singleLine = true,
        keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number),
        modifier = modifier,
    )
}

@Composable
private fun ListHeading(title: String, detail: String) {
    Column(Modifier.padding(top = 8.dp)) {
        Text(title, style = MaterialTheme.typography.titleLarge)
        Text(detail, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
    }
}

@Composable
private fun EmptyLine(text: String) {
    Text(text, style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
}

@Composable
private fun BulkReleaseBar(
    cleanup: CleanupUi,
    enabled: Boolean,
    onSelectAll: (Boolean) -> Unit,
    onRelease: () -> Unit,
) {
    val offered = cleanup.lists.heldBacklogReady
    val selected = cleanup.selectedItems
    val allSelected = offered.isNotEmpty() && selected.size == offered.size
    NeumorphicCard(Modifier.fillMaxWidth()) {
        Row(Modifier.padding(horizontal = 8.dp, vertical = 6.dp), verticalAlignment = Alignment.CenterVertically) {
            Checkbox(
                checked = allSelected,
                onCheckedChange = { onSelectAll(!allSelected) },
                enabled = enabled && offered.isNotEmpty(),
            )
            Column(Modifier.weight(1f)) {
                Text("全选（${offered.size} 项）", style = MaterialTheme.typography.bodyMedium)
                Text(
                    "已选 ${selected.size} 项 · ${totalSize(selected)}",
                    style = MaterialTheme.typography.labelMedium,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
            OutlinedButton(onClick = onRelease, enabled = enabled && selected.isNotEmpty()) {
                Text(if (cleanup.changingId != null && selected.isNotEmpty()) "放行中…" else "放行所选")
            }
        }
    }
}

@Composable
private fun CleanupRow(
    item: CleanupItemUi,
    detail: String,
    status: Pair<String, androidx.compose.ui.graphics.Color>? = null,
    leading: (@Composable () -> Unit)? = null,
    trailing: (@Composable () -> Unit)? = null,
) {
    NeumorphicCard(Modifier.fillMaxWidth()) {
        Row(
            Modifier.padding(start = if (leading == null) 14.dp else 4.dp, end = 8.dp, top = 10.dp, bottom = 10.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            leading?.invoke()
            Column(Modifier.weight(1f)) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Text(
                        item.mediaTitle,
                        style = MaterialTheme.typography.titleSmall,
                        maxLines = 1,
                        overflow = TextOverflow.Ellipsis,
                        modifier = Modifier.weight(1f, fill = false),
                    )
                    item.holdLabel?.let {
                        Spacer(Modifier.width(6.dp))
                        StatusPill(it, UninWarning)
                    }
                }
                Text(
                    detail,
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    maxLines = 3,
                    overflow = TextOverflow.Ellipsis,
                )
                status?.let { (text, color) ->
                    Spacer(Modifier.height(6.dp))
                    StatusPill(text, color)
                }
            }
            trailing?.let {
                Spacer(Modifier.width(6.dp))
                it()
            }
        }
    }
}
