package de.tlovex.unin

import de.tlovex.unin.data.model.MediaState
import de.tlovex.unin.data.model.MediaSummaryDto
import de.tlovex.unin.data.model.MediaType
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class LibraryWindowTest {
    @Test
    fun `queries every non-complete workflow state`() {
        assertEquals(7, LibraryWindow.states.size)
        assertFalse(MediaState.COMPLETE in LibraryWindow.states)
        assertTrue(
            LibraryWindow.states.containsAll(MediaState.values().filterNot { it == MediaState.COMPLETE }),
        )
    }

    @Test
    fun `requests enough pages for two hundred items in any one state`() {
        assertEquals(emptyList<Int>(), LibraryWindow.additionalPages(0))
        assertEquals(emptyList<Int>(), LibraryWindow.additionalPages(100))
        assertEquals(listOf(2), LibraryWindow.additionalPages(101))
        assertEquals(listOf(2), LibraryWindow.additionalPages(500))
    }

    @Test
    fun `reads only the newest window of a longer history`() {
        assertEquals(emptyList<Int>(), recentWindowPages(total = 0, pageSize = 100, maxItems = 200))
        assertEquals(emptyList<Int>(), recentWindowPages(total = 100, pageSize = 100, maxItems = 200))
        assertEquals(listOf(2), recentWindowPages(total = 150, pageSize = 100, maxItems = 200))
        // Fourteen thousand jobs still cost two requests, not a hundred and forty.
        assertEquals(listOf(2), recentWindowPages(total = 14_000, pageSize = 100, maxItems = 200))
        assertEquals(listOf(2, 3), recentWindowPages(total = 14_000, pageSize = 30, maxItems = 90))
    }

    @Test
    fun `keeps the latest unique items inside the mobile display limit`() {
        val items = (0..LibraryWindow.maxItems + 5).map { index ->
            media(
                id = "media-$index",
                updatedAt = "2026-08-25T00:00:00.${index.toString().padStart(3, '0')}Z",
            )
        }
        val staleDuplicate = media(
            id = "media-${LibraryWindow.maxItems + 5}",
            updatedAt = "2025-01-01T00:00:00Z",
        )

        val combined = LibraryWindow.combine(listOf(listOf(staleDuplicate), items))

        assertEquals(LibraryWindow.maxItems, combined.size)
        assertEquals("media-${LibraryWindow.maxItems + 5}", combined.first().id)
        assertEquals(combined.size, combined.map(MediaSummaryDto::id).distinct().size)
        assertFalse(combined.contains(staleDuplicate))
    }

    private fun media(id: String, updatedAt: String) = MediaSummaryDto(
        id = id,
        source = "nextfind",
        sourceItemId = id,
        mediaType = MediaType.MOVIE,
        tmdbId = null,
        title = id,
        originalTitle = null,
        countryCodes = emptyList(),
        originalLanguage = null,
        regions = emptyList(),
        year = null,
        posterPath = null,
        state = MediaState.READY,
        attentionReason = null,
        discoveredAt = updatedAt,
        updatedAt = updatedAt,
    )
}
