package de.tlovex.unin.data.remote

import okio.Buffer
import retrofit2.HttpException

internal fun HttpException.peekErrorBody(maxBytes: Long): String? {
    if (maxBytes <= 0) return null
    return runCatching {
        val source = response()?.errorBody()?.source() ?: return null
        val peek = source.peek()
        val copy = Buffer()
        while (copy.size < maxBytes) {
            val bytesRead = peek.read(copy, maxBytes - copy.size)
            if (bytesRead == -1L) break
        }
        copy.readUtf8().takeIf(String::isNotBlank)
    }.getOrNull()
}
