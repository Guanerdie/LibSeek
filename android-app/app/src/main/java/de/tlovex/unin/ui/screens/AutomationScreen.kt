package de.tlovex.unin.ui.screens

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.PlayArrow
import androidx.compose.material.icons.outlined.Refresh
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Slider
import androidx.compose.material3.Switch
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
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import de.tlovex.unin.ui.AutomationPolicyUi
import de.tlovex.unin.ui.AutomationRunState
import de.tlovex.unin.ui.AutomationRunUi
import de.tlovex.unin.ui.UninCallbacks
import de.tlovex.unin.ui.UninUiState
import de.tlovex.unin.ui.components.GradientPrimaryButton
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
import kotlin.math.roundToInt

@Composable
fun AutomationScreen(
    state: UninUiState,
    callbacks: UninCallbacks,
    contentPadding: androidx.compose.foundation.layout.PaddingValues,
    modifier: Modifier = Modifier,
) {
    val policy = state.automationPolicy
    var liveConfirmation by remember { mutableStateOf<LiveAutomationConfirmation?>(null) }
    var page by remember { mutableStateOf(0) }
    val pageSize = 8
    val totalItems = state.automationRuns.size
    val pages = pageCount(totalItems, pageSize)
    val visibleRuns = pageSlice(state.automationRuns, page, pageSize)
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
                title = "自动化",
                subtitle = "按质量门槛筛选候选；建议先通过 Dry-run 核对结果",
                action = {
                    GradientPrimaryButton(
                        text = if (policy.dryRun) "立即预演" else "立即运行",
                        onClick = {
                            if (policy.dryRun) {
                                callbacks.onRunAutomation()
                            } else {
                                liveConfirmation = LiveAutomationConfirmation.Run
                            }
                        },
                        enabled = policy.enabled && !state.automationRunOutcomeUnknown,
                        loading = state.isAutomationRunInProgress,
                        icon = Icons.Outlined.PlayArrow,
                    )
                },
            )
        }
        state.automationStats?.let { stats ->
            item {
                NeumorphicCard(Modifier.fillMaxWidth()) {
                    Column(
                        Modifier.padding(20.dp),
                        verticalArrangement = Arrangement.spacedBy(10.dp),
                    ) {
                        Text(
                            "最近 ${stats.windowDays} 天",
                            style = MaterialTheme.typography.titleLarge,
                        )
                        PolicySummaryRow("库存补齐", stats.libraryCoverageText)
                        PolicySummaryRow("搜索命中", stats.searchSuccessText)
                        PolicySummaryRow("下载任务", stats.downloadHealthText)
                        PolicySummaryRow("站点响应", stats.siteLatencyText)
                    }
                }
            }
        }
        if (state.automationRunOutcomeUnknown) {
            item {
                NeumorphicCard(Modifier.fillMaxWidth()) {
                    Column(Modifier.padding(20.dp)) {
                        Text("上次运行结果尚未确认", style = MaterialTheme.typography.titleMedium)
                        Spacer(Modifier.height(6.dp))
                        Text(
                            "客户端未收到服务端的明确结果。为避免重复提交，立即运行和失败任务重试已暂时锁定。",
                            style = MaterialTheme.typography.bodyMedium,
                            color = UninWarning,
                        )
                        Spacer(Modifier.height(8.dp))
                        TextButton(
                            onClick = callbacks.onRefreshAutomation,
                            enabled = !state.isBusy,
                            modifier = Modifier.height(48.dp),
                        ) {
                            androidx.compose.material3.Icon(Icons.Outlined.Refresh, null)
                            Spacer(Modifier.padding(horizontal = 3.dp))
                            Text("检查运行状态")
                        }
                    }
                }
            }
        }
        item {
            NeumorphicCard(Modifier.fillMaxWidth()) {
                Column(Modifier.padding(20.dp)) {
                    ToggleSetting(
                        title = "启用自动化",
                        description = "允许已保存的策略按计划运行",
                        checked = policy.enabled,
                        onCheckedChange = callbacks.onAutomationEnabledChanged,
                    )
                    Spacer(Modifier.height(10.dp))
                    ToggleSetting(
                        title = "Dry-run 安全预演",
                        description = "仅展示将要执行的操作，不提交下载",
                        checked = policy.dryRun,
                        onCheckedChange = callbacks.onDryRunChanged,
                    )
                }
            }
        }
        item {
            NeumorphicCard(Modifier.fillMaxWidth()) {
                Column(Modifier.padding(20.dp), verticalArrangement = Arrangement.spacedBy(16.dp)) {
                    Text("筛选条件", style = MaterialTheme.typography.titleLarge)
                    PolicySlider("最低匹配评分", "${policy.minimumScore}%", policy.minimumScore.toFloat(), 50f..100f, 49) {
                        callbacks.onMinimumScoreChanged(it.roundToInt())
                    }
                    PolicySlider("最低做种数", "${policy.minimumSeeders} 人", policy.minimumSeeders.toFloat(), 0f..20f, 19) {
                        callbacks.onMinimumSeedersChanged(it.roundToInt())
                    }
                    PolicySlider(
                        label = "单资源最大体积",
                        valueLabel = policy.maximumSizeGb?.let { "$it GB" } ?: "不限",
                        value = (policy.maximumSizeGb ?: 200).toFloat(),
                        range = 10f..200f,
                        steps = 18,
                    ) {
                        callbacks.onMaximumSizeChanged((it / 10).roundToInt() * 10)
                    }
                    PolicySlider("每日提交上限", "${policy.dailyLimit} 项", policy.dailyLimit.toFloat(), 1f..20f, 18) {
                        callbacks.onDailyLimitChanged(it.roundToInt())
                    }
                    Text("完整策略范围", style = MaterialTheme.typography.titleMedium)
                    PolicySummaryRow("搜索站点", policy.siteNames)
                    PolicySummaryRow("媒体类型", policy.mediaTypesText)
                    PolicySummaryRow("选择范围", policy.scopeText)
                    PolicySummaryRow("地区", policy.regionsText)
                    PolicySummaryRow("自动识别", if (policy.autoIdentify) "开启" else "关闭")
                    PolicySummaryRow(
                        "带风险候选",
                        if (policy.allowWarnings) "允许" else "拒绝",
                        if (policy.allowWarnings) UninDanger else MaterialTheme.colorScheme.onSurface,
                    )
                    PolicySummaryRow("执行周期", "每 ${policy.intervalMinutes} 分钟")
                    PolicySummaryRow(
                        "失败重试",
                        "最多 ${policy.maxAttempts} 次 · 首次等待 ${policy.retryDelayMinutes} 分钟",
                    )
                    PolicySummaryRow(
                        "每日体积上限",
                        policy.dailyDownloadSizeGb?.let { "$it GB" } ?: "不限",
                    )
                    PolicySummaryRow("搜不到时的冷却", policy.cooldownText)
                    PolicySummaryRow("综艺", policy.varietyText)
                    PolicySummaryRow("画质权重", policy.qualityWeightsText)
                    PolicySummaryRow("做种数下限", "${policy.seederFloor} 人")
                    if (policy.enabled && !policy.dryRun) {
                        Text(
                            "实时自动化会在服务端允许 qB 写入时自动提交下载，请确认预算和筛选条件后保存。",
                            style = MaterialTheme.typography.bodyMedium,
                            color = UninWarning,
                        )
                    }
                    GradientPrimaryButton(
                        text = "保存策略",
                        onClick = {
                            if (policy.enabled && !policy.dryRun) {
                                liveConfirmation = LiveAutomationConfirmation.Save
                            } else {
                                callbacks.onSaveAutomationPolicy()
                            }
                        },
                        modifier = Modifier.fillMaxWidth(),
                        loading = state.isBusy,
                    )
                }
            }
        }
        item { SectionHeading("最近运行", subtitle = "运行记录和决策摘要") }
        if (state.automationRuns.isEmpty()) {
            item {
                NeumorphicCard(Modifier.fillMaxWidth()) {
                    Text("暂无自动化运行记录", modifier = Modifier.padding(24.dp), color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
            }
        } else {
            items(visibleRuns, key = { it.id }) { run ->
                AutomationRunCard(
                    run = run,
                    retryEnabled = !state.isBusy &&
                        !state.isAutomationRunInProgress &&
                        !state.automationRunOutcomeUnknown,
                    onRetry = {
                        if (policy.dryRun) {
                            callbacks.onRetryAutomation(run)
                        } else {
                            liveConfirmation = LiveAutomationConfirmation.Retry(run)
                        }
                    },
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

    liveConfirmation?.let { action ->
        AlertDialog(
            onDismissRequest = { liveConfirmation = null },
            title = {
                Text(
                    when (action) {
                        LiveAutomationConfirmation.Save -> "启用实时自动下载？"
                        LiveAutomationConfirmation.Run -> "立即执行实时自动化？"
                        is LiveAutomationConfirmation.Retry -> "将失败任务放回实时队列？"
                    },
                )
            },
            text = {
                Text(
                    livePolicyConfirmationText(
                        policy = policy,
                        retryTitle = (action as? LiveAutomationConfirmation.Retry)?.run?.title,
                    ),
                )
            },
            confirmButton = {
                TextButton(
                    onClick = {
                        liveConfirmation = null
                        when (action) {
                            LiveAutomationConfirmation.Save -> callbacks.onSaveAutomationPolicy()
                            LiveAutomationConfirmation.Run -> callbacks.onRunAutomation()
                            is LiveAutomationConfirmation.Retry -> callbacks.onRetryAutomation(action.run)
                        }
                    },
                ) {
                    Text(
                        when (action) {
                            LiveAutomationConfirmation.Save -> "确认保存"
                            LiveAutomationConfirmation.Run -> "确认运行"
                            is LiveAutomationConfirmation.Retry -> "确认重试"
                        },
                    )
                }
            },
            dismissButton = {
                TextButton(onClick = { liveConfirmation = null }) {
                    Text("取消")
                }
            },
        )
    }
}

private sealed interface LiveAutomationConfirmation {
    data object Save : LiveAutomationConfirmation
    data object Run : LiveAutomationConfirmation
    data class Retry(val run: AutomationRunUi) : LiveAutomationConfirmation
}

private fun livePolicyConfirmationText(
    policy: AutomationPolicyUi,
    retryTitle: String? = null,
): String = buildString {
    appendLine("当前已关闭 Dry-run，满足策略的候选可能被提交到 qBittorrent。")
    if (retryTitle != null) {
        appendLine("失败任务：《$retryTitle》")
    }
    appendLine()
    appendLine("站点：${policy.siteNames}")
    appendLine("媒体：${policy.mediaTypesText}")
    appendLine("自动识别：${if (policy.autoIdentify) "开启" else "关闭"}")
    appendLine("带风险候选：${if (policy.allowWarnings) "允许" else "拒绝"}")
    appendLine("单资源上限：${policy.maximumSizeGb?.let { "$it GB" } ?: "不限"}")
    appendLine(
        "每日预算：${policy.dailyLimit} 项 / " +
            (policy.dailyDownloadSizeGb?.let { "$it GB" } ?: "体积不限"),
    )
    append("继续后仍受服务端 qB 写入开关限制。")
}

@Composable
private fun ToggleSetting(title: String, description: String, checked: Boolean, onCheckedChange: (Boolean) -> Unit) {
    Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
        Column(Modifier.weight(1f)) {
            Text(title, style = MaterialTheme.typography.titleMedium)
            Text(description, style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
        Switch(checked = checked, onCheckedChange = onCheckedChange)
    }
}

@Composable
private fun PolicySlider(
    label: String,
    valueLabel: String,
    value: Float,
    range: ClosedFloatingPointRange<Float>,
    steps: Int,
    onValueChange: (Float) -> Unit,
) {
    Column {
        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
            Text(label, style = MaterialTheme.typography.bodyLarge)
            Text(valueLabel, style = MaterialTheme.typography.labelLarge, color = UninBlue)
        }
        Slider(value = value.coerceIn(range), onValueChange = onValueChange, valueRange = range, steps = steps)
    }
}

@Composable
private fun PolicySummaryRow(
    label: String,
    value: String,
    valueColor: Color = MaterialTheme.colorScheme.onSurface,
) {
    Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
        Text(
            label,
            modifier = Modifier.weight(0.38f),
            style = MaterialTheme.typography.bodyMedium,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
        Text(
            value,
            modifier = Modifier.weight(0.62f),
            style = MaterialTheme.typography.bodyMedium,
            color = valueColor,
            textAlign = TextAlign.End,
        )
    }
}

@Composable
private fun AutomationRunCard(
    run: AutomationRunUi,
    retryEnabled: Boolean,
    onRetry: () -> Unit,
) {
    val (label, color) = when (run.state) {
        AutomationRunState.Success -> "成功" to UninSuccess
        AutomationRunState.DryRun -> "预演" to UninViolet
        AutomationRunState.Running -> "运行中" to UninBlue
        AutomationRunState.Waiting -> "等待重试" to UninWarning
        AutomationRunState.Superseded -> "已接替" to MaterialTheme.colorScheme.onSurfaceVariant
        AutomationRunState.Failed -> "失败" to UninDanger
    }
    NeumorphicCard(Modifier.fillMaxWidth()) {
        Row(Modifier.padding(18.dp), verticalAlignment = Alignment.CenterVertically) {
            Column(Modifier.weight(1f)) {
                Text(run.title, style = MaterialTheme.typography.titleMedium)
                Text(run.detail, style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
                Text(run.timeText, style = MaterialTheme.typography.labelMedium, color = UninWarning)
            }
            Column(horizontalAlignment = Alignment.End) {
                StatusPill(label, color)
                if (run.canRetry) {
                    TextButton(
                        onClick = onRetry,
                        enabled = retryEnabled,
                        modifier = Modifier.height(48.dp),
                    ) {
                        Text("重新执行")
                    }
                }
            }
        }
    }
}
