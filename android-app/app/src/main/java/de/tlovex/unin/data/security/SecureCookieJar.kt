package de.tlovex.unin.data.security

import android.content.Context
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.util.Base64
import com.google.gson.Gson
import okhttp3.Cookie
import okhttp3.CookieJar
import okhttp3.HttpUrl
import java.nio.ByteBuffer
import java.nio.charset.StandardCharsets
import java.security.KeyStore
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec

class SecureCookieJar(
    context: Context,
    expectedHost: String,
    private val gson: Gson = Gson(),
) : CookieJar {
    private val expectedHost = expectedHost.lowercase()
    private val preferences = context.applicationContext.getSharedPreferences(
        PREFERENCES_NAME,
        Context.MODE_PRIVATE,
    )
    private val keyAlias = "$KEY_ALIAS_PREFIX:${this.expectedHost}"
    private var cookies: MutableList<PersistedCookie> = readCookies().toMutableList()

    init {
        removeExpiredCookies(System.currentTimeMillis())
    }

    @Synchronized
    override fun saveFromResponse(url: HttpUrl, responseCookies: List<Cookie>) {
        if (!isExpectedOrigin(url)) return
        val now = System.currentTimeMillis()
        var changed = removeExpiredCookies(now, persist = false)

        responseCookies.forEach { cookie ->
            if (cookie.domain != expectedHost) return@forEach
            val persisted = PersistedCookie.from(cookie)
            val removed = cookies.removeAll { it.sameIdentityAs(persisted) }
            changed = changed || removed
            if (cookie.expiresAt > now) {
                cookies += persisted
                changed = true
            }
        }
        if (changed) persistCookies()
    }

    @Synchronized
    override fun loadForRequest(url: HttpUrl): List<Cookie> {
        if (!isExpectedOrigin(url)) return emptyList()
        removeExpiredCookies(System.currentTimeMillis())
        return cookies.mapNotNull(PersistedCookie::toCookie).filter { it.matches(url) }
    }

    @Synchronized
    fun clear() {
        cookies.clear()
        preferences.edit().remove(COOKIES_KEY).apply()
    }

    private fun isExpectedOrigin(url: HttpUrl): Boolean =
        url.isHttps && url.host == expectedHost

    private fun removeExpiredCookies(now: Long, persist: Boolean = true): Boolean {
        val changed = cookies.removeAll { it.expiresAt <= now }
        if (changed && persist) persistCookies()
        return changed
    }

    private fun readCookies(): List<PersistedCookie> {
        val encrypted = preferences.getString(COOKIES_KEY, null) ?: return emptyList()
        return runCatching {
            val bytes = Base64.decode(encrypted, Base64.NO_WRAP)
            require(bytes.size > IV_SIZE_BYTES)
            val buffer = ByteBuffer.wrap(bytes)
            val iv = ByteArray(IV_SIZE_BYTES).also { buffer.get(it) }
            val ciphertext = ByteArray(buffer.remaining()).also { buffer.get(it) }
            val cipher = Cipher.getInstance(TRANSFORMATION)
            cipher.init(Cipher.DECRYPT_MODE, getOrCreateKey(), GCMParameterSpec(TAG_BITS, iv))
            cipher.updateAAD(expectedHost.toByteArray(StandardCharsets.UTF_8))
            val plaintext = cipher.doFinal(ciphertext).toString(StandardCharsets.UTF_8)
            gson.fromJson(plaintext, PersistedCookies::class.java).cookies
        }.getOrElse {
            preferences.edit().remove(COOKIES_KEY).apply()
            emptyList()
        }
    }

    private fun persistCookies() {
        if (cookies.isEmpty()) {
            preferences.edit().remove(COOKIES_KEY).apply()
            return
        }
        val plaintext = gson.toJson(PersistedCookies(cookies))
            .toByteArray(StandardCharsets.UTF_8)
        val cipher = Cipher.getInstance(TRANSFORMATION)
        cipher.init(Cipher.ENCRYPT_MODE, getOrCreateKey())
        cipher.updateAAD(expectedHost.toByteArray(StandardCharsets.UTF_8))
        val ciphertext = cipher.doFinal(plaintext)
        val stored = ByteBuffer.allocate(cipher.iv.size + ciphertext.size)
            .put(cipher.iv)
            .put(ciphertext)
            .array()
        preferences.edit()
            .putString(COOKIES_KEY, Base64.encodeToString(stored, Base64.NO_WRAP))
            .apply()
    }

    private fun getOrCreateKey(): SecretKey {
        val keyStore = KeyStore.getInstance(ANDROID_KEY_STORE).apply { load(null) }
        (keyStore.getKey(keyAlias, null) as? SecretKey)?.let { return it }

        return KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, ANDROID_KEY_STORE).run {
            init(
                KeyGenParameterSpec.Builder(
                    keyAlias,
                    KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT,
                )
                    .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
                    .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
                    .setKeySize(256)
                    .build(),
            )
            generateKey()
        }
    }

    private data class PersistedCookies(
        val cookies: List<PersistedCookie> = emptyList(),
    )

    private data class PersistedCookie(
        val name: String,
        val value: String,
        val expiresAt: Long,
        val domain: String,
        val path: String,
        val secure: Boolean,
        val httpOnly: Boolean,
        val hostOnly: Boolean,
    ) {
        fun sameIdentityAs(other: PersistedCookie): Boolean =
            name == other.name && domain == other.domain && path == other.path

        fun toCookie(): Cookie? = runCatching {
            Cookie.Builder()
                .name(name)
                .value(value)
                .expiresAt(expiresAt)
                .path(path)
                .apply {
                    if (this@PersistedCookie.hostOnly) {
                        hostOnlyDomain(this@PersistedCookie.domain)
                    } else {
                        domain(this@PersistedCookie.domain)
                    }
                    if (this@PersistedCookie.secure) secure()
                    if (this@PersistedCookie.httpOnly) httpOnly()
                }
                .build()
        }.getOrNull()

        companion object {
            fun from(cookie: Cookie): PersistedCookie = PersistedCookie(
                name = cookie.name,
                value = cookie.value,
                expiresAt = cookie.expiresAt,
                domain = cookie.domain,
                path = cookie.path,
                secure = cookie.secure,
                httpOnly = cookie.httpOnly,
                hostOnly = cookie.hostOnly,
            )
        }
    }

    private companion object {
        const val ANDROID_KEY_STORE = "AndroidKeyStore"
        const val TRANSFORMATION = "AES/GCM/NoPadding"
        const val TAG_BITS = 128
        const val IV_SIZE_BYTES = 12
        const val PREFERENCES_NAME = "unin_secure_http_session"
        const val COOKIES_KEY = "cookies"
        const val KEY_ALIAS_PREFIX = "unin.cookie.v1"
    }
}
