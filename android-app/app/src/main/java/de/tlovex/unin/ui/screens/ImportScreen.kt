package de.tlovex.unin.ui.screens

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.compose.foundation.lazy.items
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.outlined.ArrowBack
import androidx.compose.material.icons.automirrored.outlined.PlaylistAdd
import androidx.compose.material3.Checkbox
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.RadioButton
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.unit.dp
import de.tlovex.unin.data.model.ImportLineStatus
import de.tlovex.unin.data.model.ImportOutcome
import de.tlovex.unin.ui.ImportBatchUi
import de.tlovex.unin.ui.ImportItemUi
import de.tlovex.unin.ui.ImportLineUi
import de.tlovex.unin.ui.ImportUi
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

@Composable
fun ImportScreen(
    state: UninUiState,
    callbacks: UninCallbacks,
    contentPadding: PaddingValues,
    modifier: Modifier = Modifier,
) {
    val bulk = state.bulkImport
    var text by rememberSaveable { mutableStateOf("") }
    // A batch has started from this list: the pasted text has done its job.
    LaunchedEffect(bulk.startedCount) { if (bulk.startedCount > 0) text = "" }
    val lines = bulk.lines

    LazyColumn(
        modifier = modifier,
        contentPadding = contentPadding,
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        item {
            Row(verticalAlignment = Alignment.CenterVertically) {
                IconButton(
                    onClick = { callbacks.onDestinationSelected(UninDestination.Missing) },
                    modifier = Modifier.size(48.dp),
                ) {
                    Icon(Icons.AutoMirrored.Outlined.ArrowBack, contentDescription = "返回缺失影视")
                }
                Spacer(Modifier.size(8.dp))
                SectionHeading(
                    title = "批量导入",
                    subtitle = "贴一份急需入库的清单，逐部搜索并自动下载评分最高的资源。",
                )
            }
        }
        bulk.batch?.let { batch ->
            item { BatchCard(batch, state.canOperate, callbacks.onImportCancel) }
            items(batch.items, key = { "batch-${it.mediaId}" }) { item -> BatchItemRow(item) }
        }

        if (lines == null) {
            item {
                NeumorphicCard(Modifier.fillMaxWidth()) {
                    Column(Modifier.padding(18.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
                        Text("清单", style = MaterialTheme.typography.titleMedium)
                        Text(
                            "每行一部：片名、片名加年份、TMDB 编号或 TMDB 链接都可以，一次最多 200 行。" +
                                "不占自动化的每日额度；最低评分、做种数等选种标准照常生效。",
                            style = MaterialTheme.typography.bodySmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                        OutlinedTextField(
                            value = text,
                            onValueChange = { text = it },
                            placeholder = { Text("红宝石戒指\n黄金新娘 2007\n12938") },
                            modifier = Modifier.fillMaxWidth().heightIn(min = 160.dp),
                            enabled = state.canOperate,
                        )
                        GradientPrimaryButton(
                            text = "匹配清单",
                            onClick = { callbacks.onImportMatch(text) },
                            enabled = state.canOperate && text.isNotBlank(),
                            loading = bulk.isMatching,
                            icon = Icons.AutoMirrored.Outlined.PlaylistAdd,
                            modifier = Modifier.fillMaxWidth(),
                        )
                    }
                }
            }
        } else {
            item { MatchSummary(bulk, callbacks) }
            itemsIndexed(lines, key = { index, _ -> "line-$index" }) { index, line ->
                MatchLineCard(line, enabled = state.canOperate, onPick = { callbacks.onImportPick(index, it) }) {
                    callbacks.onImportAgain(index, it)
                }
            }
            item {
                Row(horizontalArrangement = Arrangement.spacedBy(10.dp), verticalAlignment = Alignment.CenterVertically) {
                    OutlinedButton(onClick = callbacks.onImportDiscard) { Text("重新填写") }
                    GradientPrimaryButton(
                        text = "开始下载 ${bulk.chosen.size} 部",
                        onClick = callbacks.onImportStart,
                        enabled = state.canOperate && bulk.chosen.isNotEmpty() && bulk.batch?.isRunning != true,
                        loading = bulk.isStarting,
                        modifier = Modifier.weight(1f),
                    )
                }
            }
            if (bulk.batch?.isRunning == true) {
                item {
                    Text("上一批还在下载，结束后才能开始新的一批。", color = UninWarning, style = MaterialTheme.typography.bodySmall)
                }
            }
        }
    }
}

@Composable
private fun BatchCard(batch: ImportBatchUi, canOperate: Boolean, onCancel: () -> Unit) {
    NeumorphicCard(Modifier.fillMaxWidth()) {
        Column(Modifier.padding(18.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text(
                    if (batch.isRunning) "正在下载这一批" else "上一批的结果",
                    style = MaterialTheme.typography.titleMedium,
                    modifier = Modifier.weight(1f),
                )
                if (batch.isRunning && canOperate) {
                    OutlinedButton(onClick = onCancel, enabled = !batch.cancelRequested) {
                        Text(if (batch.cancelRequested) "处理完当前这部后停止" else "停止")
                    }
                }
            }
            Text(
                listOfNotNull(batch.startedBy, batch.startedAtText?.let { "$it 开始" }).joinToString(" · "),
                style = MaterialTheme.typography.labelMedium,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            Text(batch.summary, style = MaterialTheme.typography.bodyMedium)
            if (batch.isRunning && batch.items.isNotEmpty()) {
                LinearProgressIndicator(
                    progress = { batch.items.count { it.isFinished }.toFloat() / batch.items.size },
                    modifier = Modifier.fillMaxWidth(),
                )
            }
        }
    }
}

@Composable
private fun BatchItemRow(item: ImportItemUi) {
    val color = when (item.outcome) {
        ImportOutcome.DOWNLOADED -> UninSuccess
        ImportOutcome.RUNNING -> UninBlue
        ImportOutcome.FAILED -> UninDanger
        ImportOutcome.NO_CANDIDATE, ImportOutcome.ALREADY_PRESENT -> UninWarning
        else -> MaterialTheme.colorScheme.onSurfaceVariant
    }
    NeumorphicCard(Modifier.fillMaxWidth()) {
        Column(Modifier.padding(horizontal = 14.dp, vertical = 10.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text(
                    item.title + if (item.inLibrary) "（库中已有）" else "",
                    style = MaterialTheme.typography.titleSmall,
                    modifier = Modifier.weight(1f),
                )
                StatusPill(item.outcomeText, color)
            }
            item.note?.let {
                Text(it, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
        }
    }
}

@Composable
private fun MatchSummary(bulk: ImportUi, callbacks: UninCallbacks) {
    val inLibrary = bulk.inLibraryLines
    NeumorphicCard(Modifier.fillMaxWidth()) {
        Column(Modifier.padding(18.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
            Text("匹配结果", style = MaterialTheme.typography.titleMedium)
            Text(bulk.countsText, style = MaterialTheme.typography.bodyMedium)
            if (bulk.truncated) {
                Text("清单超过 200 行，后面的没有处理。", color = UninWarning, style = MaterialTheme.typography.bodySmall)
            }
            if (inLibrary.isNotEmpty()) {
                val all = inLibrary.all { it.again }
                Row(
                    Modifier.fillMaxWidth().clickable { callbacks.onImportAgainAll(!all) },
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    Checkbox(checked = all, onCheckedChange = { callbacks.onImportAgainAll(it) })
                    Text("库中已有的 ${inLibrary.size} 部也下载", style = MaterialTheme.typography.bodyMedium)
                }
                Text(
                    "库中已有的影视再下一份不会被空间清理自动删除，需要你自己处理。",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
        }
    }
}

@Composable
private fun MatchLineCard(
    line: ImportLineUi,
    enabled: Boolean,
    onPick: (String?) -> Unit,
    onAgain: (Boolean) -> Unit,
) {
    val color: Color = when {
        line.chosen != null -> UninSuccess
        line.status == ImportLineStatus.AMBIGUOUS -> UninWarning
        line.status == ImportLineStatus.NOT_MISSING -> UninBlue
        else -> MaterialTheme.colorScheme.onSurfaceVariant
    }
    NeumorphicCard(Modifier.fillMaxWidth()) {
        Column(Modifier.padding(horizontal = 14.dp, vertical = 10.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text(line.raw, style = MaterialTheme.typography.titleSmall, modifier = Modifier.weight(1f))
                Spacer(Modifier.width(6.dp))
                StatusPill(line.statusText, color)
            }
            when (line.status) {
                ImportLineStatus.AMBIGUOUS -> line.choices.forEach { choice ->
                    Row(
                        Modifier.fillMaxWidth().clickable(enabled = enabled) {
                            onPick(if (line.pickedId == choice.mediaId) null else choice.mediaId)
                        },
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        RadioButton(
                            selected = line.pickedId == choice.mediaId,
                            onClick = { onPick(choice.mediaId) },
                            enabled = enabled,
                        )
                        Text(
                            choice.label + if (choice.inLibrary) " · 库中已有" else "",
                            style = MaterialTheme.typography.bodyMedium,
                        )
                    }
                }
                ImportLineStatus.NOT_MISSING -> {
                    line.media?.let { Text(it.label, style = MaterialTheme.typography.bodyMedium) }
                    if (line.media != null) {
                        Row(
                            Modifier.fillMaxWidth().clickable(enabled = enabled) { onAgain(!line.again) },
                            verticalAlignment = Alignment.CenterVertically,
                        ) {
                            Checkbox(checked = line.again, onCheckedChange = onAgain, enabled = enabled)
                            Text("仍然下载（不会被自动清理）", style = MaterialTheme.typography.bodyMedium)
                        }
                    }
                }
                else -> line.media?.let { Text(it.label, style = MaterialTheme.typography.bodyMedium) }
            }
            line.note?.let {
                Text(it, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
        }
    }
}
