package de.tlovex.unin.data.security

import okhttp3.Cookie
import okhttp3.CookieJar
import okhttp3.HttpUrl
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Before
import org.junit.Test

class CsrfInterceptorTest {
    private lateinit var server: MockWebServer

    @Before
    fun setUp() {
        server = MockWebServer().apply { start() }
    }

    @After
    fun tearDown() {
        server.shutdown()
    }

    @Test
    fun `mutation request receives csrf cookie value as header`() {
        server.enqueue(MockResponse().setResponseCode(204))
        val baseUrl = server.url("/api/")
        val cookie = Cookie.Builder()
            .name("unin_csrf")
            .value("session-csrf")
            .hostOnlyDomain(baseUrl.host)
            .path("/api")
            .build()
        val client = client(baseUrl.host, listOf(cookie))

        client.newCall(
            Request.Builder()
                .url(server.url("/api/library/sync"))
                .post(ByteArray(0).toRequestBody())
                .build(),
        ).execute().close()

        assertEquals("session-csrf", server.takeRequest().getHeader("X-CSRF-Token"))
    }

    @Test
    fun `safe request does not receive csrf header`() {
        server.enqueue(MockResponse().setResponseCode(200).setBody("{}"))
        val baseUrl = server.url("/api/")
        val cookie = Cookie.Builder()
            .name("unin_csrf")
            .value("session-csrf")
            .hostOnlyDomain(baseUrl.host)
            .path("/api")
            .build()
        val client = client(baseUrl.host, listOf(cookie))

        client.newCall(Request.Builder().url(server.url("/api/auth/me")).build())
            .execute()
            .close()

        assertNull(server.takeRequest().getHeader("X-CSRF-Token"))
    }

    private fun client(host: String, cookies: List<Cookie>): OkHttpClient {
        val cookieJar = object : CookieJar {
            override fun saveFromResponse(url: HttpUrl, cookies: List<Cookie>) = Unit

            override fun loadForRequest(url: HttpUrl): List<Cookie> = cookies
        }
        return OkHttpClient.Builder()
            .addInterceptor(CsrfInterceptor(cookieJar, host))
            .build()
    }
}
