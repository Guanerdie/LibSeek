package de.tlovex.unin.data.model

import com.google.gson.Gson
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class AutomationDtosTest {
    private val gson = Gson()

    @Test
    fun `automation run response preserves asynchronous state and progress`() {
        val run = gson.fromJson(RUNNING_RUN_JSON, AutomationRunDto::class.java)

        assertEquals("run-42", run.id)
        assertEquals("manual", run.trigger)
        assertEquals(AutomationRunStateDto.RUNNING, run.state)
        assertEquals(8, run.created)
        assertEquals(3, run.succeeded)
        assertEquals(1, run.failed)
        assertEquals(4, run.deferred)
        assertNull(run.errorMessage)
        assertEquals("2026-08-25T01:02:03Z", run.createdAt)
        assertEquals("2026-08-25T01:02:04Z", run.startedAt)
        assertNull(run.finishedAt)
    }

    @Test
    fun `automation policy scope survives response to update round trip`() {
        val policy = gson.fromJson(
            """
            {
              "enabled": true,
              "dry_run": true,
              "auto_identify": false,
              "scope_mode": "selected",
              "regions": ["韩国"],
              "selected_media_ids": ["media-1", "media-2"],
              "site_ids": ["avistaz"],
              "media_types": ["tv"],
              "minimum_score": 0.8,
              "minimum_seeders": 2,
              "max_size_bytes": null,
              "allow_warnings": false,
              "interval_minutes": 60,
              "retry_delay_minutes": 30,
              "max_attempts": 3,
              "daily_download_limit": 4,
              "daily_download_bytes": null,
              "updated_at": "2026-08-25T01:00:00Z",
              "last_run_at": null
            }
            """.trimIndent(),
            AutomationPolicyDto::class.java,
        )
        val update = AutomationPolicyUpdateDto(
            enabled = policy.enabled,
            dryRun = policy.dryRun,
            autoIdentify = policy.autoIdentify,
            scopeMode = policy.scopeMode,
            regions = policy.regions,
            selectedMediaIds = policy.selectedMediaIds,
            siteIds = policy.siteIds,
            mediaTypes = policy.mediaTypes,
            minimumScore = policy.minimumScore,
            minimumSeeders = policy.minimumSeeders,
            maxSizeBytes = policy.maxSizeBytes,
            allowWarnings = policy.allowWarnings,
            intervalMinutes = policy.intervalMinutes,
            retryDelayMinutes = policy.retryDelayMinutes,
            maxAttempts = policy.maxAttempts,
            dailyDownloadLimit = policy.dailyDownloadLimit,
            dailyDownloadBytes = policy.dailyDownloadBytes,
        )

        val json = gson.toJsonTree(update).asJsonObject
        assertEquals("selected", json["scope_mode"].asString)
        assertEquals(listOf("韩国"), json["regions"].asJsonArray.map { it.asString })
        assertEquals(
            listOf("media-1", "media-2"),
            json["selected_media_ids"].asJsonArray.map { it.asString },
        )
    }

    private companion object {
        val RUNNING_RUN_JSON =
            """
            {
              "id": "run-42",
              "trigger": "manual",
              "state": "RUNNING",
              "created": 8,
              "succeeded": 3,
              "failed": 1,
              "deferred": 4,
              "error_message": null,
              "created_at": "2026-08-25T01:02:03Z",
              "started_at": "2026-08-25T01:02:04Z",
              "finished_at": null
            }
            """.trimIndent()
    }
}
