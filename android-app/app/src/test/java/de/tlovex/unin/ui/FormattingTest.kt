package de.tlovex.unin.ui

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class FormattingTest {
    @Test
    fun `a poster address is built from either form of poster path`() {
        val expected = "https://image.tmdb.org/t/p/w342/7AjIupf0lxNKNq0p8z36jv5ZpiJ.jpg"
        // NextFind's own proxy path, as the library actually stores it.
        assertEquals(expected, tmdbPosterUrl("/api/tmdb/image/7AjIupf0lxNKNq0p8z36jv5ZpiJ.jpg"))
        assertEquals(expected, tmdbPosterUrl("/7AjIupf0lxNKNq0p8z36jv5ZpiJ.jpg"))
        assertEquals(expected, tmdbPosterUrl("/api/tmdb/image/7AjIupf0lxNKNq0p8z36jv5ZpiJ.jpg?v=2"))
    }

    @Test
    fun `no usable poster gives the placeholder`() {
        assertNull(tmdbPosterUrl(null))
        assertNull(tmdbPosterUrl(""))
        assertNull(tmdbPosterUrl("/api/tmdb/image/"))
        assertNull(tmdbPosterUrl("/api/tmdb/image/../../etc/passwd"))
    }
}
