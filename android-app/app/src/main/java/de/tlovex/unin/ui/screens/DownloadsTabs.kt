package de.tlovex.unin.ui.screens

import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.material3.SegmentedButton
import androidx.compose.material3.SegmentedButtonDefaults
import androidx.compose.material3.SingleChoiceSegmentedButtonRow
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import de.tlovex.unin.ui.UninDestination

/** 「下载任务 | 空间清理」: the two halves of the downloads page. */
@Composable
internal fun DownloadsTabs(
    selected: UninDestination,
    onSelected: (UninDestination) -> Unit,
    modifier: Modifier = Modifier,
) {
    val tabs = listOf(UninDestination.Downloads to "下载任务", UninDestination.Cleanup to "空间清理")
    SingleChoiceSegmentedButtonRow(modifier.fillMaxWidth()) {
        tabs.forEachIndexed { index, (destination, label) ->
            SegmentedButton(
                selected = selected == destination,
                onClick = { if (selected != destination) onSelected(destination) },
                shape = SegmentedButtonDefaults.itemShape(index, tabs.size),
            ) {
                Text(label)
            }
        }
    }
}
