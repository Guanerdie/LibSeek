package de.tlovex.unin.ui.screens

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
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
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.CheckCircle
import androidx.compose.material.icons.outlined.CloudSync
import androidx.compose.material.icons.outlined.Movie
import androidx.compose.material.icons.outlined.Search
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import de.tlovex.unin.ui.MediaKind
import de.tlovex.unin.ui.MissingMediaUi
import de.tlovex.unin.ui.UninCallbacks
import de.tlovex.unin.ui.UninUiState
import de.tlovex.unin.ui.components.GradientPrimaryButton
import de.tlovex.unin.ui.components.MetricCard
import de.tlovex.unin.ui.components.NeumorphicCard
import de.tlovex.unin.ui.components.SectionHeading
import de.tlovex.unin.ui.components.StatusPill
import de.tlovex.unin.ui.theme.UninBlue
import de.tlovex.unin.ui.theme.UninCyan
import de.tlovex.unin.ui.theme.UninSuccess
import de.tlovex.unin.ui.theme.UninViolet
import de.tlovex.unin.ui.theme.UninWarning

@Composable
fun MissingMediaScreen(
    state: UninUiState,
    callbacks: UninCallbacks,
    contentPadding: androidx.compose.foundation.layout.PaddingValues,
    modifier: Modifier = Modifier,
) {
    val movieCount = state.missingMedia.count { it.kind == MediaKind.Movie }
    val seriesCount = state.missingMedia.count { it.kind == MediaKind.Series }
    val syncStatus = when {
        state.isLibrarySyncing -> "正在同步 NextFind"
        state.isLibraryLoading -> "正在读取最近项目"
        else -> state.lastSyncedText
    }
    LazyVerticalGrid(
        columns = GridCells.Adaptive(290.dp),
        modifier = modifier,
        contentPadding = contentPadding,
        horizontalArrangement = Arrangement.spacedBy(16.dp),
        verticalArrangement = Arrangement.spacedBy(16.dp),
    ) {
        item(span = { GridItemSpan(maxLineSpan) }) {
            SectionHeading(
                title = "缺失影视",
                subtitle = "来自 NextFind · $syncStatus",
                action = if (state.canSyncLibrary) {
                    {
                        GradientPrimaryButton(
                            text = "同步",
                            onClick = callbacks.onSyncMissing,
                            loading = state.isLibrarySyncing,
                            icon = Icons.Outlined.CloudSync,
                        )
                    }
                } else {
                    null
                },
            )
        }
        item { MetricCard("当前显示", state.missingMedia.size.toString(), UninBlue, Modifier.fillMaxWidth()) }
        item { MetricCard("电影", movieCount.toString(), UninCyan, Modifier.fillMaxWidth()) }
        item { MetricCard("剧集", seriesCount.toString(), UninViolet, Modifier.fillMaxWidth()) }
        if (state.missingMedia.isEmpty()) {
            item(span = { GridItemSpan(maxLineSpan) }) {
                NeumorphicCard(Modifier.fillMaxWidth()) {
                    Column(
                        Modifier.padding(32.dp),
                        horizontalAlignment = Alignment.CenterHorizontally,
                    ) {
                        Icon(
                            if (state.isLibraryLoading || state.isLibrarySyncing) {
                                Icons.Outlined.CloudSync
                            } else {
                                Icons.Outlined.CheckCircle
                            },
                            null,
                            tint = UninSuccess,
                            modifier = Modifier.size(44.dp),
                        )
                        Spacer(Modifier.height(12.dp))
                        Text(
                            if (state.isLibraryLoading || state.isLibrarySyncing) {
                                "正在更新缺失项目"
                            } else {
                                "当前没有缺失项目"
                            },
                            style = MaterialTheme.typography.titleLarge,
                        )
                        Text(
                            when {
                                state.isLibraryLoading || state.isLibrarySyncing ->
                                    "正在获取最新媒体状态，请稍候"
                                state.canSyncLibrary ->
                                    "点击同步以检查 NextFind 的最新状态"
                                else -> "当前账号拥有只读权限"
                            },
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                    }
                }
            }
        } else {
            items(state.missingMedia, key = { it.id }) { media ->
                MissingMediaCard(media, callbacks, Modifier.fillMaxWidth())
            }
        }
    }
}

@Composable
private fun MissingMediaCard(media: MissingMediaUi, callbacks: UninCallbacks, modifier: Modifier = Modifier) {
    var showTmdbDialog by remember(media.id) { mutableStateOf(false) }
    var tmdbIdText by remember(media.id) { mutableStateOf("") }

    NeumorphicCard(modifier.clickable { callbacks.onMediaSelected(media) }) {
        Column(Modifier.padding(16.dp)) {
            Row(horizontalArrangement = Arrangement.spacedBy(14.dp)) {
                Box(
                    Modifier
                        .size(width = 88.dp, height = 124.dp)
                        .clip(RoundedCornerShape(16.dp))
                        .background(Brush.linearGradient(listOf(UninBlue.copy(.75f), UninCyan.copy(.7f), UninViolet.copy(.8f)))),
                    contentAlignment = Alignment.Center,
                ) {
                    Icon(Icons.Outlined.Movie, contentDescription = null, tint = Color.White, modifier = Modifier.size(34.dp))
                }
                Column(Modifier.weight(1f)) {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Text(
                            media.title,
                            style = MaterialTheme.typography.titleMedium,
                            modifier = Modifier.weight(1f),
                            maxLines = 2,
                            overflow = TextOverflow.Ellipsis,
                        )
                        if (media.year != null) Text(media.year.toString(), style = MaterialTheme.typography.labelMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
                    }
                    if (media.originalTitle.isNotBlank()) {
                        Text(media.originalTitle, style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant, maxLines = 1)
                    }
                    Spacer(Modifier.height(10.dp))
                    StatusPill(if (media.kind == MediaKind.Movie) "电影" else "剧集", if (media.kind == MediaKind.Movie) UninCyan else UninViolet)
                    Spacer(Modifier.height(10.dp))
                    Text(media.missingDescription, style = MaterialTheme.typography.bodyMedium, fontWeight = FontWeight.Medium)
                    if (media.candidateCount > 0) {
                        Text("已发现 ${media.candidateCount} 个候选", style = MaterialTheme.typography.labelMedium, color = UninSuccess)
                    }
                }
            }
            Spacer(Modifier.height(12.dp))
            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.End) {
                if (!media.tmdbConfirmed) {
                    TextButton(onClick = { showTmdbDialog = true }, modifier = Modifier.height(48.dp)) {
                        Text("识别 TMDB")
                    }
                } else if (media.downloadActive) {
                    StatusPill("下载进行中", UninWarning)
                } else {
                    StatusPill("TMDB 已确认", UninSuccess)
                    Spacer(Modifier.size(8.dp))
                    GradientPrimaryButton(
                        text = "查找资源",
                        onClick = { callbacks.onSearchResources(media) },
                        icon = Icons.Outlined.Search,
                    )
                }
            }
        }
    }

    if (showTmdbDialog) {
        val tmdbId = tmdbIdText.toIntOrNull()?.takeIf { it > 0 }
        AlertDialog(
            onDismissRequest = { showTmdbDialog = false },
            title = { Text("确认 TMDB 身份") },
            text = {
                Column {
                    Text("可先让 UNIN 自动识别；遇到同名或多结果时，请输入 TMDB 页面中的数字 ID。")
                    Spacer(Modifier.height(14.dp))
                    OutlinedTextField(
                        value = tmdbIdText,
                        onValueChange = { value ->
                            tmdbIdText = value.filter(Char::isDigit).take(10)
                        },
                        modifier = Modifier.fillMaxWidth(),
                        label = { Text("TMDB ID（可选）") },
                        placeholder = { Text("例如 12345") },
                        singleLine = true,
                        isError = tmdbIdText.isNotBlank() && tmdbId == null,
                        keyboardOptions = KeyboardOptions(
                            keyboardType = KeyboardType.Number,
                            imeAction = ImeAction.Done,
                        ),
                    )
                }
            },
            confirmButton = {
                Row {
                    TextButton(
                        onClick = {
                            showTmdbDialog = false
                            callbacks.onConfirmTmdb(media)
                        },
                    ) {
                        Text("自动识别")
                    }
                    TextButton(
                        onClick = {
                            val selectedId = tmdbId ?: return@TextButton
                            showTmdbDialog = false
                            callbacks.onConfirmTmdbWithId(media, selectedId)
                        },
                        enabled = tmdbId != null,
                    ) {
                        Text("按 ID 确认")
                    }
                }
            },
            dismissButton = {
                TextButton(onClick = { showTmdbDialog = false }) {
                    Text("取消")
                }
            },
        )
    }
}
