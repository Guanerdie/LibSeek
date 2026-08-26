package de.tlovex.unin.ui.components

import org.junit.Assert.assertEquals
import org.junit.Test

class PaginationTest {
    @Test
    fun `page count rounds up and handles empty lists`() {
        assertEquals(0, pageCount(0, 8))
        assertEquals(1, pageCount(1, 8))
        assertEquals(1, pageCount(8, 8))
        assertEquals(2, pageCount(9, 8))
    }

    @Test
    fun `page slice returns only the requested window`() {
        val items = (1..20).toList()

        assertEquals((1..8).toList(), pageSlice(items, page = 0, pageSize = 8))
        assertEquals((9..16).toList(), pageSlice(items, page = 1, pageSize = 8))
        assertEquals((17..20).toList(), pageSlice(items, page = 2, pageSize = 8))
        assertEquals(emptyList<Int>(), pageSlice(items, page = 9, pageSize = 8))
    }
}
