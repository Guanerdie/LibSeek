package de.tlovex.unin.ui.components

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp

internal fun pageCount(totalItems: Int, pageSize: Int): Int {
    require(pageSize > 0) { "pageSize must be positive" }
    return if (totalItems <= 0) 0 else (totalItems + pageSize - 1) / pageSize
}

internal fun <T> pageSlice(items: List<T>, page: Int, pageSize: Int): List<T> {
    require(pageSize > 0) { "pageSize must be positive" }
    if (items.isEmpty()) return emptyList()
    val safePage = page.coerceAtLeast(0)
    val start = (safePage * pageSize).coerceAtMost(items.size)
    val end = (start + pageSize).coerceAtMost(items.size)
    return items.subList(start, end)
}

@Composable
internal fun PaginationBar(
    currentPage: Int,
    totalItems: Int,
    pageSize: Int,
    modifier: Modifier = Modifier,
    onPageChange: (Int) -> Unit,
) {
    val pages = pageCount(totalItems, pageSize)
    if (pages <= 1) return

    val safePage = currentPage.coerceIn(0, pages - 1)
    val firstItem = safePage * pageSize + 1
    val lastItem = minOf((safePage + 1) * pageSize, totalItems)
    NeumorphicCard(modifier.fillMaxWidth()) {
        Column(Modifier.padding(horizontal = 16.dp, vertical = 10.dp)) {
            Text("第 ${safePage + 1} / $pages 页 · 显示 $firstItem-$lastItem，共 $totalItems 项")
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
            ) {
                TextButton(
                    onClick = { onPageChange((safePage - 1).coerceAtLeast(0)) },
                    enabled = safePage > 0,
                    modifier = Modifier.height(44.dp),
                ) {
                    Text("上一页")
                }
                TextButton(
                    onClick = { onPageChange((safePage + 1).coerceAtMost(pages - 1)) },
                    enabled = safePage < pages - 1,
                    modifier = Modifier.height(44.dp),
                ) {
                    Text("下一页")
                }
            }
        }
    }
}
