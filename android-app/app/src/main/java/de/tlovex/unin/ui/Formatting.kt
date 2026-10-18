package de.tlovex.unin.ui

import java.time.Instant
import java.time.LocalDateTime
import java.time.OffsetDateTime
import java.time.ZoneId
import java.time.ZoneOffset
import java.time.format.DateTimeFormatter
import java.util.Locale

internal val SHANGHAI_ZONE: ZoneId = ZoneId.of("Asia/Shanghai")
private val SHORT_TIME: DateTimeFormatter = DateTimeFormatter.ofPattern("MM-dd HH:mm")
private val FULL_TIME: DateTimeFormatter = DateTimeFormatter.ofPattern("yyyy-MM-dd HH:mm")

/**
 * A server timestamp as an instant.
 *
 * The server stores UTC and, from SQLite, returns it without an offset
 * ("2026-10-12T08:41:00"). Reading such a value as local time put every
 * time in the app eight hours early, so a missing offset means UTC.
 */
internal fun parseServerInstant(value: String): Instant? =
    runCatching { Instant.parse(value) }.getOrNull()
        ?: runCatching { OffsetDateTime.parse(value).toInstant() }.getOrNull()
        ?: runCatching { LocalDateTime.parse(value).toInstant(ZoneOffset.UTC) }.getOrNull()

/** "10-12 16:41" in Shanghai time; the raw text if it cannot be read. */
internal fun formatServerTime(value: String, withYear: Boolean = false): String {
    val instant = parseServerInstant(value) ?: return value.take(16).replace('T', ' ')
    val formatter = if (withYear) FULL_TIME else SHORT_TIME
    return formatter.format(instant.atZone(SHANGHAI_ZONE))
}

internal fun formatBytes(bytes: Long): String {
    if (bytes < 1024) return "$bytes B"
    val units = arrayOf("KB", "MB", "GB", "TB")
    var value = bytes.toDouble()
    var unit = -1
    do {
        value /= 1024.0
        unit += 1
    } while (value >= 1024 && unit < units.lastIndex)
    return String.format(Locale.US, if (value >= 10) "%.1f %s" else "%.2f %s", value, units[unit])
}

private val POSTER_FILE = Regex("""^[\w-]+\.(jpe?g|png|webp)$""", RegexOption.IGNORE_CASE)

/**
 * The TMDB address of a poster.
 *
 * NextFind reports posters as its own proxy path (`/api/tmdb/image/<file>`),
 * TMDB as `/<file>`; only the file name is the same in both.
 */
internal fun tmdbPosterUrl(posterPath: String?): String? {
    val file = posterPath?.substringBefore('?')?.substringBefore('#')?.substringAfterLast('/')
    if (file.isNullOrEmpty() || !POSTER_FILE.matches(file)) return null
    return "https://image.tmdb.org/t/p/w342/$file"
}

/** 3.0 → "3", 2.46 → "2.5". */
internal fun formatDays(days: Double): String =
    if (days % 1.0 == 0.0) days.toLong().toString() else String.format(Locale.US, "%.1f", days)
