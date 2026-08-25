package de.tlovex.unin

import de.tlovex.unin.data.model.MediaState
import de.tlovex.unin.data.model.MediaSummaryDto
import java.time.Instant
import java.time.OffsetDateTime

internal object LibraryWindow {
    const val pageSize = 100
    const val maxItems = 200

    val states = listOf(
        MediaState.MISSING,
        MediaState.IDENTIFYING,
        MediaState.READY,
        MediaState.SEARCHING,
        MediaState.CANDIDATES,
        MediaState.DOWNLOADING,
        MediaState.NEEDS_ATTENTION,
    )

    fun additionalPages(total: Int): List<Int> {
        val cappedTotal = total.coerceIn(0, maxItems)
        val pageCount = (cappedTotal + pageSize - 1) / pageSize
        return if (pageCount <= 1) emptyList() else (2..pageCount).toList()
    }

    fun combine(pages: List<List<MediaSummaryDto>>): List<MediaSummaryDto> = pages
        .asSequence()
        .flatten()
        .sortedByDescending { it.updatedInstant() }
        .distinctBy(MediaSummaryDto::id)
        .take(maxItems)
        .toList()

    private fun MediaSummaryDto.updatedInstant(): Instant =
        runCatching { Instant.parse(updatedAt) }.getOrElse {
            runCatching { OffsetDateTime.parse(updatedAt).toInstant() }.getOrDefault(Instant.MIN)
        }
}
