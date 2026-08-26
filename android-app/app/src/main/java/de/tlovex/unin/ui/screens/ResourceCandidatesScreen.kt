package de.tlovex.unin.ui.screens

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.lazy.grid.GridCells
import androidx.compose.foundation.lazy.grid.GridItemSpan
import androidx.compose.foundation.lazy.grid.LazyVerticalGrid
import androidx.compose.foundation.lazy.grid.items
import androidx.compose.foundation.lazy.grid.rememberLazyGridState
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.outlined.ArrowBack
import androidx.compose.material.icons.outlined.Download
import androidx.compose.material.icons.outlined.Groups
import androidx.compose.material.icons.outlined.Storage
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
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
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import de.tlovex.unin.ui.ResourceCandidateUi
import de.tlovex.unin.ui.UninCallbacks
import de.tlovex.unin.ui.UninDestination
import de.tlovex.unin.ui.UninUiState
import de.tlovex.unin.ui.components.GradientPrimaryButton
import de.tlovex.unin.ui.components.NeumorphicCard
import de.tlovex.unin.ui.components.PaginationBar
import de.tlovex.unin.ui.components.SectionHeading
import de.tlovex.unin.ui.components.StatusPill
import de.tlovex.unin.ui.components.pageCount
import de.tlovex.unin.ui.components.pageSlice
import de.tlovex.unin.ui.theme.UninCyan
import de.tlovex.unin.ui.theme.UninDanger
import de.tlovex.unin.ui.theme.UninSuccess
import de.tlovex.unin.ui.theme.UninViolet
import de.tlovex.unin.ui.theme.UninWarning

@Composable
fun ResourceCandidatesScreen(
    state: UninUiState,
    callbacks: UninCallbacks,
    contentPadding: androidx.compose.foundation.layout.PaddingValues,
    modifier: Modifier = Modifier,
) {
    var candidateAwaitingConfirmation by remember { mutableStateOf<ResourceCandidateUi?>(null) }
    var page by remember { mutableStateOf(0) }
    val pageSize = 12
    val totalItems = state.candidates.size
    val pages = pageCount(totalItems, pageSize)
    val visibleCandidates = pageSlice(state.candidates, page, pageSize)
    val gridState = rememberLazyGridState()
    LaunchedEffect(state.selectedMedia?.id, totalItems) {
        page = page.coerceIn(0, (pages - 1).coerceAtLeast(0))
    }
    LaunchedEffect(page) {
        gridState.animateScrollToItem(0)
    }
    val submissionUnknownForSelected = state.selectedMedia?.id
        ?.let { it in state.downloadSubmissionUnknownMediaIds }
        ?: false

    LazyVerticalGrid(
        columns = GridCells.Adaptive(330.dp),
        state = gridState,
        modifier = modifier,
        contentPadding = contentPadding,
        horizontalArrangement = Arrangement.spacedBy(16.dp),
        verticalArrangement = Arrangement.spacedBy(16.dp),
    ) {
        item(span = { GridItemSpan(maxLineSpan) }) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                IconButton(
                    onClick = { callbacks.onDestinationSelected(UninDestination.Missing) },
                    modifier = Modifier.size(48.dp),
                ) {
                    Icon(Icons.AutoMirrored.Outlined.ArrowBack, contentDescription = "返回缺失影视")
                }
                Spacer(Modifier.size(8.dp))
                SectionHeading(
                    title = state.selectedMedia?.title ?: "候选资源",
                    subtitle = state.selectedMedia?.missingDescription ?: "比较匹配程度、做种与风险后再提交",
                )
            }
        }
        if (submissionUnknownForSelected || state.selectedMedia?.downloadActive == true) {
            item(span = { GridItemSpan(maxLineSpan) }) {
                NeumorphicCard(Modifier.fillMaxWidth()) {
                    Column(Modifier.padding(20.dp)) {
                        Text(
                            if (submissionUnknownForSelected) {
                                "上次提交结果尚未确认"
                            } else {
                                "该影视已有下载任务"
                            },
                            style = MaterialTheme.typography.titleMedium,
                        )
                        Spacer(Modifier.height(6.dp))
                        Text(
                            if (submissionUnknownForSelected) {
                                "为避免同一影视重复提交资源，候选按钮已锁定。请前往下载页同步 qBittorrent 状态。"
                            } else {
                                "下载完成或失败前不能再为同一影视提交其他候选资源。请前往下载页查看状态。"
                            },
                            style = MaterialTheme.typography.bodyMedium,
                            color = UninWarning,
                        )
                    }
                }
            }
        }
        if (state.candidates.isEmpty()) {
            item(span = { GridItemSpan(maxLineSpan) }) {
                NeumorphicCard(Modifier.fillMaxWidth()) {
                    Column(Modifier.padding(28.dp), horizontalAlignment = Alignment.CenterHorizontally) {
                        Text("尚未发现候选资源", style = MaterialTheme.typography.titleLarge)
                        Text("返回后重新搜索，或检查 PT 站点连接状态", color = MaterialTheme.colorScheme.onSurfaceVariant)
                    }
                }
            }
        } else {
            items(visibleCandidates, key = { it.id }) { candidate ->
                CandidateCard(
                    candidate = candidate,
                    onDownload = {
                        if (
                            state.submittingCandidateId != null ||
                            submissionUnknownForSelected ||
                            state.selectedMedia?.downloadActive == true
                        ) return@CandidateCard
                        if (candidate.riskMessage == null) {
                            callbacks.onCandidateDownload(candidate)
                        } else {
                            candidateAwaitingConfirmation = candidate
                        }
                    },
                    enabled = state.submittingCandidateId == null &&
                        !submissionUnknownForSelected &&
                        state.selectedMedia?.downloadActive != true,
                    loading = state.submittingCandidateId == candidate.id,
                    modifier = Modifier.fillMaxWidth(),
                )
            }
            item(span = { GridItemSpan(maxLineSpan) }) {
                PaginationBar(
                    currentPage = page,
                    totalItems = totalItems,
                    pageSize = pageSize,
                    onPageChange = { page = it },
                )
            }
        }
    }

    candidateAwaitingConfirmation?.let { candidate ->
        AlertDialog(
            onDismissRequest = { candidateAwaitingConfirmation = null },
            title = { Text("确认提交带风险的资源？") },
            text = {
                Text(
                    "《${candidate.title}》包含风险提示：\n\n${candidate.riskMessage}\n\n继续后会向 qBittorrent 提交下载任务。",
                )
            },
            confirmButton = {
                TextButton(
                    enabled = state.submittingCandidateId == null &&
                        !submissionUnknownForSelected &&
                        state.selectedMedia?.downloadActive != true,
                    onClick = {
                        candidateAwaitingConfirmation = null
                        callbacks.onCandidateDownload(candidate)
                    },
                ) {
                    Text("确认提交")
                }
            },
            dismissButton = {
                TextButton(onClick = { candidateAwaitingConfirmation = null }) {
                    Text("取消")
                }
            },
        )
    }
}

@Composable
private fun CandidateCard(
    candidate: ResourceCandidateUi,
    onDownload: () -> Unit,
    enabled: Boolean,
    loading: Boolean,
    modifier: Modifier = Modifier,
) {
    NeumorphicCard(modifier) {
        Column(Modifier.padding(18.dp)) {
            Row(verticalAlignment = Alignment.Top) {
                Column(Modifier.weight(1f)) {
                    Text(candidate.title, style = MaterialTheme.typography.titleMedium, maxLines = 3)
                    Spacer(Modifier.height(4.dp))
                    Text("${candidate.siteName} · ${candidate.releaseGroup}", style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
                StatusPill("匹配 ${candidate.matchScore}%", if (candidate.matchScore >= 90) UninSuccess else UninWarning)
            }
            Spacer(Modifier.height(14.dp))
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                StatusPill(candidate.resolution, UninViolet)
                StatusPill(candidate.source, UninCyan)
                if (candidate.freeLeech) StatusPill("FREE", UninSuccess)
            }
            Spacer(Modifier.height(14.dp))
            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(18.dp)) {
                CandidateFact(Icons.Outlined.Storage, "${candidate.sizeText} · ${candidate.codec}")
                CandidateFact(Icons.Outlined.Groups, "${candidate.seeders} 做种")
            }
            Spacer(Modifier.height(10.dp))
            Text("覆盖：${candidate.coversMissing}", style = MaterialTheme.typography.bodyMedium, fontWeight = FontWeight.Medium)
            if (candidate.riskMessage != null) {
                Spacer(Modifier.height(8.dp))
                Text(candidate.riskMessage, style = MaterialTheme.typography.bodyMedium, color = UninDanger)
            }
            Spacer(Modifier.height(16.dp))
            GradientPrimaryButton(
                text = if (loading) "正在提交" else "提交到 qBittorrent",
                onClick = onDownload,
                modifier = Modifier.fillMaxWidth(),
                enabled = enabled,
                loading = loading,
                icon = Icons.Outlined.Download,
            )
        }
    }
}

@Composable
private fun CandidateFact(icon: androidx.compose.ui.graphics.vector.ImageVector, text: String) {
    Row(verticalAlignment = Alignment.CenterVertically) {
        Icon(icon, contentDescription = null, modifier = Modifier.size(18.dp), tint = MaterialTheme.colorScheme.onSurfaceVariant)
        Spacer(Modifier.size(6.dp))
        Text(text, style = MaterialTheme.typography.labelMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
    }
}
