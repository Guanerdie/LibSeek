package de.tlovex.unin.data.remote

import com.google.gson.Gson
import de.tlovex.unin.data.model.AuthRole
import de.tlovex.unin.data.model.CredentialsDto
import de.tlovex.unin.data.model.LoginRequestDto
import de.tlovex.unin.data.model.PasswordChangeRequestDto
import kotlinx.coroutines.runBlocking
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import retrofit2.Retrofit
import retrofit2.converter.gson.GsonConverterFactory

/**
 * The wire contract for signing in and for changing the password.
 *
 * Both fields are named by the server, so a rename here fails silently: the
 * server would fall back to its default (an eight-hour session) or reject the
 * body. These tests pin the names.
 */
class AuthApiTest {
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
    fun `asking to be remembered is sent to the server`() = runBlocking {
        server.enqueue(jsonResponse(LOGIN_JSON))

        api.login(LoginRequestDto("owner", "secret", remember = true))

        val request = server.takeRequest()
        assertEquals("POST", request.method)
        assertEquals("/api/auth/login", request.path)
        assertTrue(request.body.readUtf8().contains("\"remember\":true"))
    }

    @Test
    fun `a plain sign in still names the flag`() = runBlocking {
        // Omitting it would also mean "short session", but sending it keeps the
        // request self-describing rather than relying on the server default.
        server.enqueue(jsonResponse(LOGIN_JSON))

        api.login(LoginRequestDto("owner", "secret"))

        assertTrue(server.takeRequest().body.readUtf8().contains("\"remember\":false"))
    }

    @Test
    fun `setup never sends the remember flag`() = runBlocking {
        // POST /api/auth/setup rejects fields it does not know, so the login
        // body must stay a separate type.
        server.enqueue(jsonResponse(LOGIN_JSON))

        api.setup(CredentialsDto("owner", "secret"))

        val body = server.takeRequest().body.readUtf8()
        assertFalse(body.contains("remember"))
    }

    @Test
    fun `changing the password sends both passwords in the server's spelling`() = runBlocking {
        server.enqueue(jsonResponse(LOGIN_JSON))

        api.changePassword(PasswordChangeRequestDto("old-secret", "new-secret"))

        val request = server.takeRequest()
        assertEquals("POST", request.method)
        assertEquals("/api/auth/password", request.path)
        val body = request.body.readUtf8()
        assertTrue(body.contains("\"current_password\":\"old-secret\""))
        assertTrue(body.contains("\"new_password\":\"new-secret\""))
    }

    @Test
    fun `changing the password returns the reissued session`() = runBlocking {
        // Rotating the signing key invalidates this device's cookie too, so the
        // server hands back a fresh CSRF token with the response.
        server.enqueue(jsonResponse(LOGIN_JSON))

        val reissued = api.changePassword(PasswordChangeRequestDto("old-secret", "new-secret"))

        assertEquals("owner", reissued.username)
        assertEquals(AuthRole.ADMIN, reissued.role)
        assertEquals("csrf-token-2", reissued.csrfToken)
    }

    private fun jsonResponse(body: String, status: Int = 200) = MockResponse()
        .setResponseCode(status)
        .setHeader("Content-Type", "application/json")
        .setBody(body)

    private companion object {
        val LOGIN_JSON = """
            {
              "username": "owner",
              "role": "admin",
              "csrf_token": "csrf-token-2"
            }
        """.trimIndent()
    }
}
