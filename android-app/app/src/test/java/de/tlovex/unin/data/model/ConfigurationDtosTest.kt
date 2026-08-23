package de.tlovex.unin.data.model

import com.google.gson.Gson
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class ConfigurationDtosTest {
    private val gson = Gson()

    @Test
    fun `configuration response reads only operational status`() {
        val response = gson.fromJson(
            """
            {
              "nextfind": {
                "base_url": "https://nextfind.invalid",
                "username": "not-retained-by-dto",
                "password_configured": true,
                "configured": true
              },
              "tmdb": { "configured": true },
              "pt_site": {
                "architecture": "avistaz",
                "base_url": "https://tracker.invalid",
                "username": "not-retained-by-dto",
                "configured": true,
                "runtime_supported": true,
                "search_ready": true
              },
              "pt_sites": {
                "avistaz": {
                  "architecture": "avistaz",
                  "configured": true,
                  "runtime_supported": true,
                  "search_ready": true
                },
                "nexusphp": {
                  "architecture": "nexusphp",
                  "configured": false,
                  "runtime_supported": false,
                  "search_ready": false
                }
              },
              "qbittorrent": {
                "url": "https://qb.invalid",
                "username": "not-retained-by-dto",
                "configured": true
              },
              "configuration_complete": true
            }
            """.trimIndent(),
            ConfigurationStatusDto::class.java,
        )

        assertTrue(response.configurationComplete)
        assertTrue(response.nextfind.configured)
        assertTrue(response.tmdb.configured)
        assertTrue(response.qbittorrent.configured)
        assertEquals(PtSiteArchitecture.AVISTAZ, response.ptSite?.architecture)
        assertTrue(response.ptSite?.searchReady == true)
        assertFalse(response.ptSites.nexusphp?.runtimeSupported ?: true)
    }

    @Test
    fun `connection test response preserves health detail`() {
        val response = gson.fromJson(
            """
            {
              "target": "qbittorrent",
              "healthy": false,
              "error_code": "QB_AUTH_FAILED",
              "message": "认证失败"
            }
            """.trimIndent(),
            ConnectionTestResultDto::class.java,
        )

        assertEquals("qbittorrent", response.target)
        assertFalse(response.healthy)
        assertEquals("QB_AUTH_FAILED", response.errorCode)
        assertEquals("认证失败", response.message)
    }
}
