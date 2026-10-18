package de.tlovex.unin.ui.components

import android.graphics.BitmapFactory
import android.util.LruCache
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.Movie
import androidx.compose.material.icons.outlined.Tv
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.produceState
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.ImageBitmap
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.unit.dp
import de.tlovex.unin.ui.theme.uninPalette
import java.util.concurrent.TimeUnit
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import okhttp3.OkHttpClient
import okhttp3.Request

/**
 * Posters straight from TMDB's image CDN.
 *
 * A plain client with no cookie jar: nothing of the UNIN session goes to a
 * third party. Decoded posters are kept in memory for the life of the process.
 */
private object PosterLoader {
    private val client = OkHttpClient.Builder()
        .callTimeout(15, TimeUnit.SECONDS)
        .build()

    // About 12 MB of decoded posters, a few screens' worth at w342.
    private val cache = object : LruCache<String, ImageBitmap>(12 * 1024 * 1024) {
        override fun sizeOf(key: String, value: ImageBitmap): Int = value.width * value.height * 4
    }

    fun cached(url: String): ImageBitmap? = cache.get(url)

    suspend fun load(url: String): ImageBitmap? = cache.get(url) ?: withContext(Dispatchers.IO) {
        runCatching {
            client.newCall(Request.Builder().url(url).build()).execute().use { response ->
                if (!response.isSuccessful) return@use null
                response.body?.byteStream()?.use(BitmapFactory::decodeStream)?.asImageBitmap()
            }
        }.getOrNull()?.also { cache.put(url, it) }
    }
}

/** A 2:3 poster, or a quiet placeholder while there is none. */
@Composable
fun PosterImage(
    url: String?,
    isMovie: Boolean,
    title: String,
    modifier: Modifier = Modifier,
) {
    val shape = RoundedCornerShape(10.dp)
    val poster by produceState(initialValue = url?.let(PosterLoader::cached), url) {
        if (url != null && value == null) value = PosterLoader.load(url)
    }
    Box(
        modifier
            .clip(shape)
            .background(uninPalette.surfaceSunken),
        contentAlignment = Alignment.Center,
    ) {
        val image = poster
        if (image != null) {
            Image(
                bitmap = image,
                contentDescription = title,
                contentScale = ContentScale.Crop,
                modifier = Modifier.fillMaxSize(),
            )
        } else {
            Column(horizontalAlignment = Alignment.CenterHorizontally) {
                Icon(
                    if (isMovie) Icons.Outlined.Movie else Icons.Outlined.Tv,
                    contentDescription = null,
                    tint = uninPalette.inkMuted,
                    modifier = Modifier.size(26.dp),
                )
                Spacer(Modifier.height(4.dp))
                Text(
                    title.take(1),
                    style = MaterialTheme.typography.titleLarge,
                    color = uninPalette.inkMuted,
                )
            }
        }
    }
}
