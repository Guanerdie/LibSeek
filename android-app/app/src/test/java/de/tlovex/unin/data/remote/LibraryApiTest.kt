package de.tlovex.unin.data.remote

import com.google.gson.Gson
import de.tlovex.unin.data.model.AutomationJobState
import de.tlovex.unin.data.model.DownloadState
import de.tlovex.unin.data.model.QuickFillRequestDto
import de.tlovex.unin.data.model.SubscriptionUpdateDto
import kotlinx.coroutines.runBlocking
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import retrofit2.Retrofit
import retrofit2.converter.gson.GsonConverterFactory

/**
 * Following a title, and filling one in a single request.
 *
 * Both endpoints already existed on the server and had no client here, so
 * these tests pin the shapes the app now depends on.
 */
class LibraryApiTest {
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
    fun `following a title is a put on that title`() = runBlocking {
        server.enqueue(jsonResponse(mediaDetailJson(subscribed = true, active = true)))

        api.setSubscription("media-1", SubscriptionUpdateDto(subscribed = true))

        val request = server.takeRequest()
        assertEquals("PUT", request.method)
        assertEquals("/api/library/media-1/subscription", request.path)
        assertTrue(request.body.readUtf8().contains("\"subscribed\":true"))
    }

    @Test
    fun `a subscription the policy is not reading is reported as inactive`() = runBlocking {
        // Subscribing while the policy still runs on filters changes nothing
        // until the scope is switched; the app has to be able to say so.
        server.enqueue(jsonResponse(mediaDetailJson(subscribed = true, active = false)))

        val detail = api.setSubscription("media-1", SubscriptionUpdateDto(subscribed = true))

        assertTrue(detail.subscribed)
        assertFalse(detail.subscriptionActive)
    }

    @Test
    fun `the media detail explains the last automated attempt`() = runBlocking {
        server.enqueue(jsonResponse(mediaDetailJson(subscribed = false, active = false)))

        val outcome = api.mediaDetail("media-1").latestAutomation

        assertEquals("job-7", outcome?.jobId)
        assertEquals(AutomationJobState.SUCCEEDED, outcome?.state)
        assertEquals(12, outcome?.candidateCount)
        assertNull(outcome?.selectedTitle)
        assertEquals(listOf("评分低于门槛", "做种数不足"), outcome?.rejected?.flatMap { it.reasons })
    }

    @Test
    fun `quick fill uses the cached search unless asked not to`() = runBlocking {
        server.enqueue(jsonResponse(QUICK_FILL_JSON))

        api.quickFill("media-1", QuickFillRequestDto())

        val request = server.takeRequest()
        assertEquals("POST", request.method)
        assertEquals("/api/library/media-1/quick-fill", request.path)
        assertTrue(request.body.readUtf8().contains("\"force\":false"))
    }

    @Test
    fun `quick fill returns the pick, the download and every candidate`() = runBlocking {
        // The candidates come back whether or not one was submitted: quick fill
        // is a shortcut over the manual path, not a replacement for it.
        server.enqueue(jsonResponse(QUICK_FILL_JSON))

        val result = api.quickFill("media-1", QuickFillRequestDto())

        assertEquals("candidate-1", result.selectedCandidateId)
        assertEquals(DownloadState.QUEUED, result.download?.state)
        assertEquals(2, result.search.candidates.size)
        assertEquals(listOf("体积超过上限"), result.rejected.flatMap { it.reasons })
    }

    @Test
    fun `the monitoring summary reads the headline numbers`() = runBlocking {
        server.enqueue(jsonResponse(STATS_JSON))

        val stats = api.stats()

        assertEquals("/api/stats", server.takeRequest().path)
        assertEquals(7, stats.windowDays)
        assertEquals(40, stats.libraryCoverage?.covered)
        assertEquals(2, stats.downloadHealth?.errored)
        assertEquals("avistaz", stats.siteLatency.single().siteId)
        // A day with no searches carries a null rate, not a zero.
        assertNull(stats.searchTrend.first { it.total == 0 }.successRate)
    }

    private fun jsonResponse(body: String, status: Int = 200) = MockResponse()
        .setResponseCode(status)
        .setHeader("Content-Type", "application/json")
        .setBody(body)

    private companion object {
        fun mediaDetailJson(subscribed: Boolean, active: Boolean): String =
            """
            {
              "id": "media-1",
              "source": "nextfind",
              "source_item_id": "nf-1",
              "media_type": "tv",
              "tmdb_id": 1234,
              "title": "某部剧",
              "original_title": null,
              "country_codes": ["KR"],
              "original_language": "ko",
              "regions": ["韩国"],
              "year": 2024,
              "poster_path": null,
              "state": "CANDIDATES",
              "attention_reason": null,
              "discovered_at": "2026-08-25T01:00:00Z",
              "updated_at": "2026-08-25T01:00:00Z",
              "episodes": [],
              "latest_search": null,
              "latest_automation": {
                "job_id": "job-7",
                "state": "SUCCEEDED",
                "created_at": "2026-08-25T02:00:00Z",
                "finished_at": "2026-08-25T02:00:09Z",
                "error_code": null,
                "error_message": null,
                "candidate_count": 12,
                "selected_title": null,
                "selected_score": null,
                "search_cooldown_until": null,
                "download_skipped": null,
                "rejected": [
                  {"candidate_id": "c1", "title": "候选一", "reasons": ["评分低于门槛"]},
                  {"candidate_id": "c2", "title": "候选二", "reasons": ["做种数不足"]}
                ]
              },
              "minimum_score_override": null,
              "subscribed": $subscribed,
              "subscription_active": $active
            }
            """.trimIndent()

        val QUICK_FILL_JSON =
            """
            {
              "search": {
                "id": "search-1",
                "media_id": "media-1",
                "site_ids": ["avistaz"],
                "state": "SUCCEEDED",
                "error_message": null,
                "created_at": "2026-08-25T03:00:00Z",
                "finished_at": "2026-08-25T03:00:07Z",
                "candidates": [
                  {
                    "id": "candidate-1", "search_id": "search-1", "site_id": "avistaz",
                    "torrent_id": "t1", "title": "某部剧 S01 2160p", "details_url": null,
                    "size_bytes": 42949672960, "seeders": 12, "resolution": "2160p",
                    "source": "WEB-DL", "codec": "H265", "download_factor": 0.0,
                    "collection_type": null, "file_count": 10,
                    "season_coverage": [1], "episode_coverage": [],
                    "score": 0.91, "reasons": ["TMDB_ID_EXACT"], "warnings": []
                  },
                  {
                    "id": "candidate-2", "search_id": "search-1", "site_id": "avistaz",
                    "torrent_id": "t2", "title": "某部剧 S01 1080p", "details_url": null,
                    "size_bytes": 10737418240, "seeders": 3, "resolution": "1080p",
                    "source": "WEBRip", "codec": "H264", "download_factor": 1.0,
                    "collection_type": null, "file_count": 10,
                    "season_coverage": [1], "episode_coverage": [],
                    "score": 0.62, "reasons": [], "warnings": []
                  }
                ]
              },
              "selected_candidate_id": "candidate-1",
              "download": {
                "id": "download-1", "media_id": "media-1", "candidate_id": "candidate-1",
                "info_hash": "abc", "name": "某部剧 S01 2160p", "state": "QUEUED",
                "progress": 0.0, "download_speed": 0, "upload_speed": 0, "ratio": 0.0,
                "error_message": null,
                "created_at": "2026-08-25T03:00:08Z", "updated_at": "2026-08-25T03:00:08Z"
              },
              "rejected": [
                {"candidate_id": "candidate-2", "title": "某部剧 S01 1080p",
                 "reasons": ["体积超过上限"]}
              ]
            }
            """.trimIndent()

        val STATS_JSON =
            """
            {
              "window_days": 7,
              "generated_at": "2026-08-25T04:00:00Z",
              "search_trend": [
                {"date": "2026-08-24", "total": 0, "succeeded": 0, "success_rate": null},
                {"date": "2026-08-25", "total": 8, "succeeded": 6, "success_rate": 0.75}
              ],
              "site_latency": [
                {"site_id": "avistaz", "searches": 8,
                 "average_seconds": 4.31, "slowest_seconds": 11.2}
              ],
              "recent_runs": [],
              "library_coverage": {"total": 127, "covered": 40, "coverage_rate": 0.3149},
              "download_health": {"total": 31, "errored": 2, "by_state": {"ERROR": 2}}
            }
            """.trimIndent()
    }
}
