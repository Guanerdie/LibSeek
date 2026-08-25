package de.tlovex.unin.data.remote

import com.google.gson.Gson
import de.tlovex.unin.data.model.MediaState
import kotlinx.coroutines.runBlocking
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Before
import org.junit.Test
import retrofit2.HttpException
import retrofit2.Retrofit
import retrofit2.converter.gson.GsonConverterFactory

class HttpErrorBodyTest {
    private lateinit var server: MockWebServer
    private lateinit var api: UninApi

    @Before
    fun setUp() {
        server = MockWebServer().apply { start() }
        api = Retrofit.Builder()
            .baseUrl(server.url("/"))
            .addConverterFactory(GsonConverterFactory.create(Gson()))
            .build()
            .create(UninApi::class.java)
    }

    @After
    fun tearDown() {
        server.shutdown()
    }

    @Test
    fun `retrofit error body can be inspected repeatedly without consuming it`() = runBlocking {
        val body = """{"error_code":"AUTH_SESSION_EXPIRED","message":"登录会话已过期"}"""
        server.enqueue(
            MockResponse()
                .setResponseCode(401)
                .setHeader("Content-Type", "application/json")
                .setBody(body),
        )

        val error = try {
            api.library()
            throw AssertionError("Expected an HTTP error")
        } catch (error: HttpException) {
            error
        }

        assertEquals(body, error.peekErrorBody(16_384))
        assertEquals(body, error.peekErrorBody(16_384))
        assertEquals(body, error.response()?.errorBody()?.string())
    }

    @Test
    fun `peek respects the byte limit without consuming the error body`() = runBlocking {
        val body = "0123456789abcdef"
        server.enqueue(
            MockResponse()
                .setResponseCode(500)
                .setHeader("Content-Type", "text/plain")
                .setBody(body),
        )

        val error = try {
            api.library()
            throw AssertionError("Expected an HTTP error")
        } catch (error: HttpException) {
            error
        }

        assertEquals("01234567", error.peekErrorBody(8))
        assertEquals(body, error.response()?.errorBody()?.string())
    }

    @Test
    fun `library state filter is sent as the backend enum value`() = runBlocking {
        server.enqueue(
            MockResponse()
                .setResponseCode(200)
                .setHeader("Content-Type", "application/json")
                .setBody(
                    """
                    {
                      "items": [],
                      "total": 0,
                      "page": 1,
                      "page_size": 100,
                      "filter_options": {
                        "media_types": [],
                        "regions": [],
                        "states": [],
                        "years": []
                      }
                    }
                    """.trimIndent(),
                ),
        )

        api.library(state = MediaState.READY, page = 1, pageSize = 100)

        val requestUrl = server.takeRequest().requestUrl
        assertEquals("READY", requestUrl?.queryParameter("state"))
        assertEquals("100", requestUrl?.queryParameter("page_size"))
    }
}
