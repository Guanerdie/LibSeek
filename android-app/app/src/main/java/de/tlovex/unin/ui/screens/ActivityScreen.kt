package de.tlovex.unin.ui.screens

import androidx.compose.foundation.clickable
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
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
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.rememberScrollState
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.Refresh
import androidx.compose.material3.FilterChip
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import de.tlovex.unin.data.model.ActivityCategory
import de.tlovex.unin.ui.ActivityEntryUi
import de.tlovex.unin.ui.UninCallbacks
import de.tlovex.unin.ui.UninUiState
import de.tlovex.unin.ui.categoryLabel
import de.tlovex.unin.ui.components.NeumorphicCard
import de.tlovex.unin.ui.components.SectionHeading
import de.tlovex.unin.ui.components.StatusPill
import de.tlovex.unin.ui.theme.UninBlue
import de.tlovex.unin.ui.theme.UninDanger
import de.tlovex.unin.ui.theme.UninSuccess
import de.tlovex.unin.ui.theme.UninViolet
import de.tlovex.unin.ui.theme.UninWarning

private val CATEGORY_FILTERS: List<ActivityCategory?> = listOf(null) + ActivityCategory.entries

@Composable
fun ActivityScreen(
    state: UninUiState,
    callbacks: UninCallbacks,
    contentPadding: PaddingValues,
    modifier: Modifier = Modifier,
) {
    val activity = state.activity
    val listState = rememberLazyListState()
    LaunchedEffect(activity.page, activity.category) { listState.scrollToItem(0) }

    LazyColumn(
        state = listState,
        modifier = modifier,
        contentPadding = contentPadding,
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        item {
            SectionHeading(
                title = "操作记录",
                subtitle = "下载、删种、入库确认和策略修改：什么时候、谁、手动还是自动、为什么。",
                action = {
                    TextButton(
                        onClick = callbacks.onRefreshActivity,
                        enabled = !activity.isLoading,
                        modifier = Modifier.height(48.dp),
                    ) {
                        Icon(Icons.Outlined.Refresh, contentDescription = null)
                        Spacer(Modifier.size(4.dp))
                        Text("刷新")
                    }
                },
            )
        }
        item {
            Row(
                Modifier.horizontalScroll(rememberScrollState()),
                horizontalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                CATEGORY_FILTERS.forEach { category ->
                    FilterChip(
                        selected = activity.category == category,
                        onClick = { callbacks.onActivityCategorySelected(category) },
                        label = { Text(categoryLabel(category)) },
                    )
                }
            }
        }
        if (activity.entries.isEmpty()) {
            item {
                NeumorphicCard(Modifier.fillMaxWidth()) {
                    Text(
                        when {
                            activity.isLoading -> "正在读取…"
                            activity.isLoaded -> "没有符合条件的记录。"
                            else -> "还没有读取到记录，点「刷新」重试。"
                        },
                        Modifier.padding(24.dp),
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
            }
        }
        items(activity.entries, key = { it.id }) { entry -> ActivityCard(entry) }
        if (activity.total > 0) {
            item {
                NeumorphicCard(Modifier.fillMaxWidth()) {
                    Row(Modifier.padding(horizontal = 8.dp), verticalAlignment = Alignment.CenterVertically) {
                        TextButton(
                            onClick = { callbacks.onActivityPageSelected(activity.page - 1) },
                            enabled = activity.page > 1 && !activity.isLoading,
                        ) { Text("上一页") }
                        Text(
                            "第 ${activity.page} / ${activity.pageCount} 页 · 共 ${activity.total} 条",
                            Modifier.weight(1f),
                            style = MaterialTheme.typography.bodySmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                        TextButton(
                            onClick = { callbacks.onActivityPageSelected(activity.page + 1) },
                            enabled = activity.page < activity.pageCount && !activity.isLoading,
                        ) { Text("下一页") }
                    }
                }
            }
        }
    }
}

@Composable
private fun ActivityCard(entry: ActivityEntryUi) {
    var expanded by rememberSaveable(entry.id) { mutableStateOf(false) }
    val categoryColor = when (entry.category) {
        ActivityCategory.CLEANUP -> UninDanger
        ActivityCategory.DOWNLOAD -> UninBlue
        ActivityCategory.LIBRARY -> UninSuccess
        ActivityCategory.SETTINGS -> UninWarning
        else -> UninViolet
    }
    NeumorphicCard(
        Modifier
            .fillMaxWidth()
            .clickable(enabled = entry.details.isNotEmpty()) { expanded = !expanded },
    ) {
        Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text(entry.title, style = MaterialTheme.typography.titleMedium, modifier = Modifier.weight(1f))
                StatusPill(categoryLabel(entry.category), categoryColor)
            }
            Text(
                "${entry.timeText} · ${entry.actorText} · ${entry.triggerText}",
                style = MaterialTheme.typography.labelMedium,
                color = if (entry.isAutomatic) UninViolet else MaterialTheme.colorScheme.onSurfaceVariant,
            )
            entry.mediaTitle?.let { Text(it, style = MaterialTheme.typography.titleSmall) }
            Text(entry.message, style = MaterialTheme.typography.bodyMedium)
            Text(
                "原因：${entry.reasonText}",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            if (entry.details.isNotEmpty()) {
                if (expanded) {
                    Column(Modifier.padding(top = 4.dp), verticalArrangement = Arrangement.spacedBy(2.dp)) {
                        entry.details.forEach { (label, value) ->
                            Row {
                                Text(
                                    label,
                                    Modifier.width(96.dp),
                                    style = MaterialTheme.typography.bodySmall,
                                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                                )
                                Text(value, style = MaterialTheme.typography.bodySmall)
                            }
                        }
                    }
                }
                Text(
                    if (expanded) "收起详情" else "查看详情（${entry.details.size} 项）",
                    style = MaterialTheme.typography.labelMedium,
                    color = UninBlue,
                )
            }
        }
    }
}
