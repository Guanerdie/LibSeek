package de.tlovex.unin.ui.screens

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.DownloadDone
import androidx.compose.material.icons.outlined.Refresh
import androidx.compose.material3.Icon
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import de.tlovex.unin.ui.DownloadItemUi
import de.tlovex.unin.ui.DownloadState
import de.tlovex.unin.ui.UninCallbacks
import de.tlovex.unin.ui.UninUiState
import de.tlovex.unin.ui.components.NeumorphicCard
import de.tlovex.unin.ui.components.PaginationBar
import de.tlovex.unin.ui.components.SectionHeading
import de.tlovex.unin.ui.components.StatusPill
import de.tlovex.unin.ui.components.pageCount
import de.tlovex.unin.ui.components.pageSlice
import de.tlovex.unin.ui.theme.UninBlue
import de.tlovex.unin.ui.theme.UninDanger
import de.tlovex.unin.ui.theme.UninSuccess
import de.tlovex.unin.ui.theme.UninViolet
import de.tlovex.unin.ui.theme.UninWarning

@Composable
fun DownloadsScreen(
    state: UninUiState,
    callbacks: UninCallbacks,
    contentPadding: androidx.compose.foundation.layout.PaddingValues,
    modifier: Modifier = Modifier,
) {
    var page by remember { mutableStateOf(0) }
    val pageSize = 8
    val totalItems = state.downloads.size
    val pages = pageCount(totalItems, pageSize)
    val visibleDownloads = pageSlice(state.downloads, page, pageSize)
    val listState = rememberLazyListState()
    LaunchedEffect(totalItems) {
        page = page.coerceIn(0, (pages - 1).coerceAtLeast(0))
    }
    LaunchedEffect(page) {
        listState.animateScrollToItem(0)
    }
    LazyColumn(
        state = listState,
        modifier = modifier,
        contentPadding = contentPadding,
        verticalArrangement = Arrangement.spacedBy(16.dp),
    ) {
        item {
            SectionHeading(
                title = "下载任务",
                subtitle = "qBittorrent 最近同步状态 · ${state.downloads.count { it.state == DownloadState.Downloading }} 项下载中",
                action = {
                    TextButton(
                        onClick = callbacks.onRefreshDownloads,
                        enabled = !state.isBusy,
                        modifier = Modifier.height(48.dp),
                    ) {
                        Icon(Icons.Outlined.Refresh, contentDescription = null)
                        Spacer(Modifier.size(4.dp))
                        Text("同步状态")
                    }
                },
            )
        }
        if (state.downloadSubmissionUnknownMediaIds.isNotEmpty()) {
            item {
                NeumorphicCard(Modifier.fillMaxWidth()) {
                    Column(Modifier.padding(20.dp)) {
                        Text("资源提交结果待确认", style = MaterialTheme.typography.titleMedium)
                        Spacer(Modifier.height(6.dp))
                        Text(
                            "服务端可能仍在完成上次提交。请使用“同步状态”确认 qBittorrent 结果；确认前不会允许再次提交候选资源。",
                            style = MaterialTheme.typography.bodyMedium,
                            color = UninWarning,
                        )
                    }
                }
            }
        }
        if (state.downloads.isEmpty()) {
            item {
                NeumorphicCard(Modifier.fillMaxWidth()) {
                    Column(Modifier.padding(32.dp), horizontalAlignment = Alignment.CenterHorizontally) {
                        Icon(Icons.Outlined.DownloadDone, null, tint = UninSuccess)
                        Spacer(Modifier.height(10.dp))
                        Text("暂无下载任务", style = MaterialTheme.typography.titleLarge)
                        Text("从候选资源页提交的任务会显示在这里", color = MaterialTheme.colorScheme.onSurfaceVariant)
                    }
                }
            }
        } else {
            items(visibleDownloads, key = { it.id }) { item ->
                DownloadCard(
                    item = item,
                    callbacks = callbacks,
                    actionsEnabled = !state.isBusy,
                    modifier = Modifier.fillMaxWidth(),
                )
            }
            item {
                PaginationBar(
                    currentPage = page,
                    totalItems = totalItems,
                    pageSize = pageSize,
                    onPageChange = { page = it },
                )
            }
        }
    }
}

@Composable
private fun DownloadCard(
    item: DownloadItemUi,
    callbacks: UninCallbacks,
    actionsEnabled: Boolean,
    modifier: Modifier = Modifier,
) {
    val statusColor = when (item.state) {
        DownloadState.Queued -> UninWarning
        DownloadState.Downloading -> UninBlue
        DownloadState.Seeding -> UninViolet
        DownloadState.Completed -> UninSuccess
        DownloadState.OutcomeUnknown -> UninWarning
        DownloadState.Failed -> UninDanger
    }
    val statusText = when (item.state) {
        DownloadState.Queued -> "排队中"
        DownloadState.Downloading -> "下载中"
        DownloadState.Seeding -> "做种中"
        DownloadState.Completed -> "已完成"
        DownloadState.OutcomeUnknown -> "结果未知"
        DownloadState.Failed -> "失败"
    }
    NeumorphicCard(modifier) {
        Column(Modifier.padding(18.dp)) {
            Row(verticalAlignment = Alignment.Top) {
                Column(Modifier.weight(1f)) {
                    Text(item.title, style = MaterialTheme.typography.titleMedium)
                    Text(item.subtitle, style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
                StatusPill(statusText, statusColor)
            }
            Spacer(Modifier.height(16.dp))
            LinearProgressIndicator(
                progress = { item.progress.coerceIn(0f, 1f) },
                modifier = Modifier.fillMaxWidth().height(8.dp),
                color = statusColor,
                trackColor = MaterialTheme.colorScheme.surfaceVariant,
            )
            Spacer(Modifier.height(8.dp))
            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
                Text("${(item.progress.coerceIn(0f, 1f) * 100).toInt()}%", style = MaterialTheme.typography.labelLarge)
                Text(
                    listOf(item.speedText, item.etaText, item.ratioText).filter { it.isNotBlank() }.joinToString(" · "),
                    style = MaterialTheme.typography.labelMedium,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
            if (item.errorMessage != null) {
                Spacer(Modifier.height(10.dp))
                Text(
                    item.errorMessage,
                    style = MaterialTheme.typography.bodyMedium,
                    color = if (item.state == DownloadState.OutcomeUnknown) UninWarning else UninDanger,
                )
            }
            if (item.state == DownloadState.Failed) {
                Spacer(Modifier.height(8.dp))
                Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.End) {
                    TextButton(
                        onClick = { callbacks.onRetryDownload(item) },
                        enabled = actionsEnabled,
                        modifier = Modifier.height(48.dp),
                    ) {
                        Icon(Icons.Outlined.Refresh, null)
                        Spacer(Modifier.size(4.dp))
                        Text("重试")
                    }
                }
            }
            if (item.state == DownloadState.OutcomeUnknown) {
                Spacer(Modifier.height(8.dp))
                Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.End) {
                    TextButton(
                        onClick = callbacks.onRefreshDownloads,
                        enabled = actionsEnabled,
                        modifier = Modifier.height(48.dp),
                    ) {
                        Icon(Icons.Outlined.Refresh, null)
                        Spacer(Modifier.size(4.dp))
                        Text("同步确认")
                    }
                }
            }
        }
    }
}
