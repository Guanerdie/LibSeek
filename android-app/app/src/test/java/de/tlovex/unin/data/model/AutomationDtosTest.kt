package de.tlovex.unin.data.model

import com.google.gson.Gson
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
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
        val policy = gson.fromJson(SERVER_POLICY_JSON, AutomationPolicyDto::class.java)

        val json = gson.toJsonTree(policy.toUpdate()).asJsonObject

        assertEquals("selected", json["scope_mode"].asString)
        assertEquals(listOf("韩国"), json["regions"].asJsonArray.map { it.asString })
        assertEquals(
            listOf("media-1", "media-2"),
            json["selected_media_ids"].asJsonArray.map { it.asString },
        )
    }

    @Test
    fun `saving does not reset settings this app cannot edit`() {
        // Saving is a whole-document write, so a setting the app leaves out of
        // the body is replaced by a default. The app once knew 17 of the
        // policy's fields and sent only those, quietly wiping the other 11 --
        // cooldown tiers, the variety rules and every quality weight -- the
        // moment anyone saved from a phone. Every value below is deliberately
        // different from this DTO's default, so a dropped field fails here.
        val policy = gson.fromJson(SERVER_POLICY_JSON, AutomationPolicyDto::class.java)

        val json = gson.toJsonTree(policy.toUpdate()).asJsonObject

        assertEquals(6, json["cooldown_tier_1_hours"].asInt)
        assertEquals(48, json["cooldown_tier_2_hours"].asInt)
        assertEquals(96, json["cooldown_tier_3_hours"].asInt)
        assertEquals(9, json["variety_recent_episodes"].asInt)
        assertTrue(json["automate_variety"].asBoolean)
        assertEquals(50, json["weight_resolution"].asInt)
        assertEquals(20, json["weight_size"].asInt)
        assertEquals(10, json["weight_source"].asInt)
        assertEquals(15, json["weight_seeders"].asInt)
        assertEquals(5, json["weight_promotion"].asInt)
        assertEquals(4, json["seeder_floor"].asInt)
    }

    @Test
    fun `the update body carries every field the server accepts`() {
        // A field added to the server and to the response DTO but forgotten in
        // the mapping would be sent as a default on the next save.
        val policy = gson.fromJson(SERVER_POLICY_JSON, AutomationPolicyDto::class.java)

        val sent = gson.toJsonTree(policy.toUpdate()).asJsonObject.keySet()
        val received = gson.toJsonTree(policy).asJsonObject.keySet()

        // updated_at and last_run_at are the server's to set, not the client's.
        assertEquals(emptySet<String>(), received - sent - setOf("updated_at", "last_run_at"))
    }

    private companion object {
        /** A policy whose every value differs from the client-side defaults. */
        val SERVER_POLICY_JSON =
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
              "cooldown_tier_1_hours": 6,
              "cooldown_tier_2_hours": 48,
              "cooldown_tier_3_hours": 96,
              "variety_recent_episodes": 9,
              "automate_variety": true,
              "weight_resolution": 50,
              "weight_size": 20,
              "weight_source": 10,
              "weight_seeders": 15,
              "weight_promotion": 5,
              "seeder_floor": 4,
              "updated_at": "2026-08-25T01:00:00Z",
              "last_run_at": null
            }
            """.trimIndent()

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
