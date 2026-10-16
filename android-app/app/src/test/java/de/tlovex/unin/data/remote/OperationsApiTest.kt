package de.tlovex.unin.data.remote

import com.google.gson.Gson
import de.tlovex.unin.data.model.ActivityCategory
import de.tlovex.unin.data.model.CleanupHoldBulkUpdateDto
import de.tlovex.unin.data.model.CleanupHoldUpdateDto
import de.tlovex.unin.data.model.CleanupPolicyUpdateDto
import de.tlovex.unin.data.model.DownloadCleanupState
import de.tlovex.unin.data.model.ImportLineStatus
import de.tlovex.unin.data.model.ImportStartRequestDto
import kotlinx.coroutines.runBlocking
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import com.google.gson.JsonParser
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import retrofit2.Retrofit
import retrofit2.converter.gson.GsonConverterFactory

class OperationsApiTest {
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
    fun `saving cleanup settings sends only the cleanup fields`() = runBlocking {
        server.enqueue(json(POLICY_JSON))

        val policy = api.updateCleanupPolicy(
            CleanupPolicyUpdateDto(
                enabled = true,
                dryRun = false,
                afterDays = 10,
                minSeedingDays = 10,
                graceDays = 2,
                requireLibraryConfirmed = true,
                dailyLimit = 20,
                releaseBacklog = false,
            ),
        )

        val request = server.takeRequest()
        assertEquals("PUT", request.method)
        assertEquals("/api/automation/policy", request.path)
        val body = JsonParser.parseString(request.body.readUtf8()).asJsonObject
        // The server applies what a request carries; nothing else may be in it.
        assertEquals(8, body.size())
        assertTrue(body.keySet().all { it.startsWith("cleanup_") })
        assertEquals(2, body.get("cleanup_grace_days").asInt)
        assertEquals(true, policy.cleanupEnabled)
        assertEquals(listOf(2010, 2009), policy.years)
    }

    @Test
    fun `the preview and holds use the cleanup endpoints`() = runBlocking {
        server.enqueue(json(PREVIEW_JSON))
        server.enqueue(json(DOWNLOAD_JSON))
        server.enqueue(json("""{"changed": 2, "skipped": [{"download_id": "d3", "reason": "已删除"}]}"""))

        val preview = api.cleanupPreview()
        val download = api.setCleanupHold("d1", CleanupHoldUpdateDto(held = true))
        val bulk = api.setCleanupHolds(CleanupHoldBulkUpdateDto(listOf("d1", "d2", "d3"), held = false))

        assertFalse(preview.deleteAuthorized)
        assertEquals(DownloadCleanupState.HELD, preview.items.single().cleanupState)
        assertEquals("BACKLOG", preview.items.single().holdReason)
        assertEquals(DownloadCleanupState.HELD, download.cleanupState)
        assertEquals(2, bulk.changed)
        assertEquals("已删除", bulk.skipped!!.single().reason)

        assertEquals("/api/downloads/cleanup-preview", server.takeRequest().path)
        val hold = server.takeRequest()
        assertEquals("PUT", hold.method)
        assertEquals("/api/downloads/d1/cleanup-hold", hold.path)
        assertEquals("""{"held":true}""", hold.body.readUtf8())
        val release = server.takeRequest()
        assertEquals("/api/downloads/cleanup-hold", release.path)
        assertEquals("""{"download_ids":["d1","d2","d3"],"held":false}""", release.body.readUtf8())
    }

    @Test
    fun `activity is filtered by its lower-case category`() = runBlocking {
        server.enqueue(json(ACTIVITY_JSON))

        val page = api.activity(category = ActivityCategory.CLEANUP, page = 2, pageSize = 30)

        assertEquals("/api/activity?category=cleanup&page=2&page_size=30", server.takeRequest().path)
        val entry = page.items.single()
        assertEquals(ActivityCategory.CLEANUP, entry.category)
        assertNull(entry.actor)
        assertEquals(12.0, entry.details!!["seeding_days"])
    }

    @Test
    fun `bulk import matches, starts and follows a batch`() = runBlocking {
        server.enqueue(json(MATCH_JSON))
        server.enqueue(json(BATCH_JSON))

        val match = api.matchImport(de.tlovex.unin.data.model.ImportMatchRequestDto("说客\n妇产科"))
        val batch = api.startImport(ImportStartRequestDto(listOf("m1", "m2"), listOf("m2")))

        assertEquals(ImportLineStatus.NOT_MISSING, match.lines[1].status)
        assertEquals(true, match.lines[1].media!!.inLibrary)
        assertEquals("ALREADY_PRESENT", batch.items!!.single().outcome.name)

        val matchRequest = server.takeRequest()
        assertEquals("/api/library/import/match", matchRequest.path)
        assertEquals("""{"text":"说客\n妇产科"}""", matchRequest.body.readUtf8())
        val startRequest = server.takeRequest()
        assertEquals("/api/library/import", startRequest.path)
        assertEquals("""{"media_ids":["m1","m2"],"existing_ids":["m2"]}""", startRequest.body.readUtf8())
    }

    private fun json(body: String) = MockResponse()
        .setResponseCode(200)
        .setHeader("Content-Type", "application/json")
        .setBody(body)

    private companion object {
        val POLICY_JSON = """
            {
              "enabled": true, "dry_run": false, "auto_identify": true, "scope_mode": "filters",
              "regions": [], "selected_media_ids": [], "site_ids": ["avistaz"], "media_types": ["movie", "tv"],
              "minimum_score": 0.6, "minimum_seeders": 1, "max_size_bytes": null, "allow_warnings": false,
              "interval_minutes": 60, "retry_delay_minutes": 30, "max_attempts": 3,
              "daily_download_limit": 20, "daily_download_bytes": null,
              "years": [2010, 2009],
              "cleanup_enabled": true, "cleanup_dry_run": false, "cleanup_after_days": 10,
              "cleanup_min_seeding_days": 10, "cleanup_grace_days": 2,
              "cleanup_require_library_confirmed": true, "cleanup_daily_limit": 20,
              "cleanup_release_backlog": false,
              "updated_at": "2026-10-16T00:00:00", "last_run_at": null
            }
        """.trimIndent()

        val PREVIEW_JSON = """
            {
              "enabled": true, "dry_run": true, "delete_authorized": false,
              "reclaimable_bytes": 0, "held_reclaimable_bytes": 1000,
              "items": [{
                "download_id": "d1", "media_title": "说客", "name": "The.Lobbyist.mkv",
                "size_bytes": 1000, "completed_at": "2026-09-01T00:00:00", "seeding_days": 40.5,
                "required_seeding_days": 10, "cleanup_state": "HELD", "deletes_at": null,
                "blocked_reason": null, "hold_reason": "BACKLOG"
              }]
            }
        """.trimIndent()

        val DOWNLOAD_JSON = """
            {
              "id": "d1", "media_id": "m1", "candidate_id": "c1", "info_hash": null,
              "name": "The.Lobbyist.mkv", "state": "SEEDING", "progress": 1.0,
              "download_speed": 0, "upload_speed": 0, "ratio": 1.2, "error_message": null,
              "completed_at": null, "seeding_seconds": 0,
              "cleanup_state": "HELD", "cleanup_marked_at": null, "cleanup_deleted_at": null,
              "created_at": "2026-09-01T00:00:00", "updated_at": "2026-09-01T00:00:00"
            }
        """.trimIndent()

        val ACTIVITY_JSON = """
            {
              "items": [{
                "id": "a1", "created_at": "2026-10-01T00:00:00", "event": "DOWNLOAD_CLEANUP_DELETED",
                "category": "cleanup", "message": "已删除", "actor": null, "trigger": null, "reason": null,
                "details": {"seeding_days": 12}, "media_id": null, "media_title": null
              }],
              "total": 31, "page": 2, "page_size": 30
            }
        """.trimIndent()

        val MATCH_JSON = """
            {
              "lines": [
                {"raw": "说客", "status": "MATCHED", "media": {"media_id": "m1", "title": "说客",
                  "original_title": null, "year": 2007, "media_type": "tv", "tmdb_id": 12938, "in_library": false},
                  "choices": [], "note": null},
                {"raw": "妇产科", "status": "NOT_MISSING", "media": {"media_id": "m2", "title": "妇产科",
                  "original_title": null, "year": 2010, "media_type": "tv", "tmdb_id": 47273, "in_library": true},
                  "choices": [], "note": "库中已有"}
              ],
              "truncated": false
            }
        """.trimIndent()

        val BATCH_JSON = """
            {
              "id": "b1", "state": "RUNNING", "started_by": "owner", "started_at": "2026-10-16T00:00:00",
              "finished_at": null, "cancel_requested": false,
              "items": [{"media_id": "m2", "title": "妇产科", "outcome": "ALREADY_PRESENT",
                "message": null, "selected_title": null, "download_id": null, "in_library": true}]
            }
        """.trimIndent()
    }
}
