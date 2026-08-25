package de.tlovex.unin.data.remote

import com.google.gson.Gson
import de.tlovex.unin.data.model.AutomationRunStateDto
import kotlinx.coroutines.runBlocking
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Before
import org.junit.Test
import retrofit2.Retrofit
import retrofit2.converter.gson.GsonConverterFactory

class AutomationApiTest {
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
    fun `run endpoints use asynchronous run resource contract`() = runBlocking {
        server.enqueue(jsonResponse(PENDING_RUN_JSON, 202))
        server.enqueue(jsonResponse(RUNNING_RUN_JSON))
        server.enqueue(jsonResponse(SUCCEEDED_RUN_JSON))

        val created = api.runAutomation()
        val latest = api.latestAutomationRun().body()
        val completed = api.automationRun(created.id)

        assertEquals(AutomationRunStateDto.PENDING, created.state)
        assertEquals(AutomationRunStateDto.RUNNING, latest?.state)
        assertEquals(AutomationRunStateDto.SUCCEEDED, completed.state)
        assertEquals(6, completed.succeeded)

        val createRequest = server.takeRequest()
        val latestRequest = server.takeRequest()
        val statusRequest = server.takeRequest()
        assertEquals("POST", createRequest.method)
        assertEquals("/api/automation/runs", createRequest.path)
        assertEquals("/api/automation/runs/latest", latestRequest.path)
        assertEquals("/api/automation/runs/run-42", statusRequest.path)
    }

    @Test
    fun `latest run accepts an empty history response`() = runBlocking {
        server.enqueue(jsonResponse("null"))

        assertNull(api.latestAutomationRun().body())
        assertEquals("/api/automation/runs/latest", server.takeRequest().path)
    }

    private fun jsonResponse(body: String, status: Int = 200) = MockResponse()
        .setResponseCode(status)
        .setHeader("Content-Type", "application/json")
        .setBody(body)

    private companion object {
        val PENDING_RUN_JSON = runJson("PENDING", created = 0, succeeded = 0, deferred = 0)
        val RUNNING_RUN_JSON = runJson("RUNNING", created = 8, succeeded = 3, deferred = 5)
        val SUCCEEDED_RUN_JSON = runJson("SUCCEEDED", created = 8, succeeded = 6, deferred = 2)

        fun runJson(state: String, created: Int, succeeded: Int, deferred: Int): String =
            """
            {
              "id": "run-42",
              "trigger": "manual",
              "state": "$state",
              "created": $created,
              "succeeded": $succeeded,
              "failed": 0,
              "deferred": $deferred,
              "error_message": null,
              "created_at": "2026-08-25T01:02:03Z",
              "started_at": null,
              "finished_at": null
            }
            """.trimIndent()
    }
}
